"""
Go live from the browser.

    camcorder ─HDMI→ capture card ─USB→ browser (getUserMedia + MediaRecorder, WebM)
        ─WebSocket /ws/stream/<church>/→ this server ─ffmpeg (H.264/AAC, FLV)→ YouTube Live (RTMPS)

The studio page sends small WebM chunks over a WebSocket; ffmpeg turns them into
the stream YouTube expects and pushes it with the church's (encrypted) stream key.

Works on Windows, macOS and Linux: ffmpeg runs as a normal subprocess fed by a
writer thread (Daphne's event loop on Windows can't run asyncio subprocesses).
Needs ffmpeg on the server (settings.FFMPEG_PATH or on the PATH).
"""
import asyncio
import json
import logging
import queue
import shutil
import subprocess
import threading
import time
from collections import deque

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings
from django.utils import timezone

from .consumers import CLOSE_FORBIDDEN, CLOSE_NOT_FOUND, CLOSE_UNAUTHENTICATED, CLOSE_UNVERIFIED

log = logging.getLogger(__name__)

# YouTube's recommended settings per quality (video bitrate in kbit/s).
PRESETS = {
    '480p': (854, 480, 1500),
    '720p': (1280, 720, 3000),
    '1080p': (1920, 1080, 4500),
}
FPS = 30
CLOSE_NOT_READY = 4409          # no stream key / ffmpeg missing
GO_LIVE_AFTER_SECONDS = 3       # ffmpeg still running with data flowing → we call it live
HEARTBEAT_SECONDS = 10          # how often the session's last_seen_at is refreshed
MAX_CHUNK_BYTES = 4 * 1024 * 1024


def ffmpeg_path():
    return getattr(settings, 'FFMPEG_PATH', '') or shutil.which('ffmpeg') or ''


def browser_streaming_status(stream_settings):
    """(available, reason) for the studio's checklist."""
    if not ffmpeg_path():
        return False, 'ffmpeg is not installed on the server. Install it (see the setup guide) or use OBS.'
    if not stream_settings.stream_key_encrypted:
        return False, 'Save your YouTube stream key first.'
    return True, ''


def output_url(key):
    """Where ffmpeg sends the stream. STREAM_OUTPUT_URL (with {key}) overrides YouTube, e.g. for testing."""
    override = getattr(settings, 'STREAM_OUTPUT_URL', '')
    if override:
        return override.format(key=key)
    from churches.models import YOUTUBE_RTMP_PRIMARY, YOUTUBE_RTMPS_PRIMARY
    base = YOUTUBE_RTMPS_PRIMARY if getattr(settings, 'STREAM_USE_RTMPS', True) else YOUTUBE_RTMP_PRIMARY
    return f'{base}/{key}'


def build_command(resolution, key, has_audio=True):
    width, height, kbps = PRESETS.get(resolution, PRESETS['720p'])
    cmd = [ffmpeg_path(), '-hide_banner', '-loglevel', 'error', '-nostdin',
           '-thread_queue_size', '1024', '-fflags', '+genpts', '-i', 'pipe:0']
    if not has_audio:  # YouTube expects an audio track; send silence
        cmd += ['-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=44100']
    cmd += ['-map', '0:v:0', '-map', '0:a:0' if has_audio else '1:a:0']
    cmd += [
        '-vf', (f'scale={width}:{height}:force_original_aspect_ratio=decrease,'
                f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps={FPS},format=yuv420p'),
        '-c:v', 'libx264', '-preset', 'veryfast', '-tune', 'zerolatency', '-profile:v', 'high',
        '-b:v', f'{kbps}k', '-maxrate', f'{kbps}k', '-bufsize', f'{kbps * 2}k',
        '-g', str(FPS * 2), '-keyint_min', str(FPS * 2), '-sc_threshold', '0',
        '-c:a', 'aac', '-b:a', '128k', '-ar', '44100', '-ac', '2',
    ]
    if not has_audio:
        cmd += ['-shortest']
    cmd += ['-f', 'flv', output_url(key)]
    return cmd


class Relay:
    """One ffmpeg process fed from a queue by a writer thread; its error output is kept (key removed)."""

    def __init__(self, cmd, secret):
        self.secret = secret
        self.queue = queue.Queue(maxsize=240)  # ~2 minutes of 500 ms chunks
        self.errors = deque(maxlen=20)
        flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                     creationflags=flags)
        threading.Thread(target=self._write, daemon=True, name='stream-writer').start()
        threading.Thread(target=self._read_errors, daemon=True, name='stream-errors').start()

    def _write(self):
        while True:
            chunk = self.queue.get()
            if chunk is None:
                break
            try:
                self.proc.stdin.write(chunk)
                self.proc.stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                break
        try:
            self.proc.stdin.close()
        except OSError:
            pass

    def _read_errors(self):
        for raw in iter(self.proc.stderr.readline, b''):
            line = raw.decode('utf-8', 'replace').strip()
            if line:
                self.errors.append(line.replace(self.secret, '••••') if self.secret else line)

    def feed(self, chunk, timeout=5):
        self.queue.put(chunk, timeout=timeout)

    @property
    def running(self):
        return self.proc.poll() is None

    def last_error(self):
        return self.errors[-1] if self.errors else ''

    def stop(self, wait=6):
        """Finish sending what's queued, then end ffmpeg (forcefully if it hangs)."""
        try:
            self.queue.put(None, timeout=1)
        except queue.Full:
            pass
        try:
            self.proc.wait(timeout=wait)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=3)


def explain_failure(error):
    """Turn ffmpeg's last words into something a Clerk can act on."""
    e = error.lower()
    if 'connection refused' in e or 'input/output error' in e or 'i/o error' in e or 'handshake' in e:
        return ('YouTube refused the stream. Check that live streaming is turned on for the channel '
                'and that the saved stream key is the current one.')
    if 'name or service not known' in e or 'resolve' in e or 'network is unreachable' in e:
        return 'The server could not reach YouTube. Check its internet connection.'
    if 'invalid data' in e or 'ebml' in e or 'matroska' in e:
        return 'The video from your browser could not be read. Reload the page and try again (Chrome or Edge work best).'
    return error or 'The stream stopped unexpectedly.'


class StreamConsumer(AsyncWebsocketConsumer):
    """
    /ws/stream/<church>/?token=<jwt> — the Clerk's studio.

    Client → server:  {"action": "start", "event": <id|null>, "has_audio": true, "takeover": false}
                      binary WebM chunks (after "starting")
                      {"action": "stop"}
    Server → client:  {"type": "ready", "available": bool, "reason": "...", "resolution": "720p", "live_elsewhere": bool}
                      {"type": "status", "state": "starting|live|ended|failed", "message": "...",
                       "seconds": n, "kbps": n, "session": id}
    """

    relay = None
    session_id = None
    monitor = None

    async def connect(self):
        self.user = self.scope.get('user')
        if not self.user or not self.user.is_authenticated:
            return await self.close(code=CLOSE_UNAUTHENTICATED)
        info = await self.load(self.scope['url_route']['kwargs']['church_slug'])
        if info is None:
            return await self.close(code=CLOSE_NOT_FOUND)
        if info['role'] != 'clerk':
            return await self.close(code=CLOSE_FORBIDDEN)
        if not info['verified']:
            return await self.close(code=CLOSE_UNVERIFIED)
        self.church_id = info['church_id']
        self.resolution = info['resolution']
        self.group = f'stream_church_{self.church_id}'
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()
        await self.send_json({'type': 'ready', 'available': info['available'], 'reason': info['reason'],
                              'resolution': self.resolution, 'live_elsewhere': info['live_elsewhere']})

    async def disconnect(self, code):
        if getattr(self, 'group', None):
            await self.channel_layer.group_discard(self.group, self.channel_name)
        if self.relay:
            await self.finish('ended', 'The studio was closed or lost its connection.', notify=False)

    async def receive(self, text_data=None, bytes_data=None):
        if bytes_data is not None:
            return await self.on_chunk(bytes_data)
        try:
            msg = json.loads(text_data or '{}')
        except json.JSONDecodeError:
            return await self.send_status('failed', 'Unreadable message.')
        action = msg.get('action') if isinstance(msg, dict) else None
        if action == 'start':
            await self.start(msg)
        elif action == 'stop':
            await self.finish('ended', 'You ended the stream.')

    # ------------------------------------------------------------------ start / stop
    async def start(self, msg):
        if self.relay:
            return await self.send_status('failed', 'Already streaming.')
        prep = await self.prepare(msg.get('event'), bool(msg.get('takeover')))
        if 'error' in prep:
            return await self.send_status('failed', prep['error'], live_elsewhere=prep.get('live_elsewhere', False))
        cmd = build_command(self.resolution, prep['key'], has_audio=msg.get('has_audio', True) is not False)
        try:
            self.relay = await asyncio.get_running_loop().run_in_executor(None, Relay, cmd, prep['key'])
        except OSError as exc:
            log.exception('Could not start ffmpeg')
            await self.end_session(prep['session_id'], 'failed', f'Could not start ffmpeg: {exc}')
            return await self.send_status('failed', 'The server could not start ffmpeg.')
        self.session_id = prep['session_id']
        self.started = time.monotonic()
        self.bytes = 0
        self.window = deque()  # (time, bytes) for the bitrate shown in the studio
        self.went_live = False
        self.monitor = asyncio.create_task(self.watch())
        await self.send_status('starting', 'Connecting to YouTube…')

    async def on_chunk(self, chunk):
        if not self.relay or len(chunk) > MAX_CHUNK_BYTES:
            return
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, self.relay.feed, chunk)
        except queue.Full:
            return await self.finish('failed', 'The server could not keep up with the video. '
                                               'Try a lower quality or a faster internet connection.')
        self.bytes += len(chunk)
        self.window.append((time.monotonic(), len(chunk)))

    async def watch(self):
        """Every second: is ffmpeg alive? Report time on air and bitrate; keep the session fresh."""
        last_beat = 0
        try:
            while self.relay:
                await asyncio.sleep(1)
                if not self.relay:
                    break
                if not self.relay.running:
                    return await self.finish('failed', explain_failure(self.relay.last_error()))
                now = time.monotonic()
                while self.window and now - self.window[0][0] > 5:
                    self.window.popleft()
                kbps = int(sum(n for _, n in self.window) * 8 / 1000 / 5)
                if not self.went_live and self.bytes and now - self.started >= GO_LIVE_AFTER_SECONDS:
                    self.went_live = True
                    self.live_since = now
                    await self.mark(status='live', went_live=True)
                if now - last_beat >= HEARTBEAT_SECONDS:
                    last_beat = now
                    await self.mark()
                await self.send_status('live' if self.went_live else 'starting',
                                       'On air' if self.went_live else 'Connecting to YouTube…',
                                       seconds=int(now - self.live_since) if self.went_live else 0, kbps=kbps)
        except asyncio.CancelledError:
            pass

    async def finish(self, state, message, notify=True):
        relay, self.relay = self.relay, None
        if self.monitor and self.monitor is not asyncio.current_task():
            self.monitor.cancel()
        if relay:
            await asyncio.get_running_loop().run_in_executor(None, relay.stop)
            if state == 'ended' and relay.last_error() and not self.went_live:
                state, message = 'failed', explain_failure(relay.last_error())
        if self.session_id:
            await self.end_session(self.session_id, state, message)
        if notify:
            await self.send_status(state, message)

    async def stream_stop(self, event):
        """Another device (or the Clerk from the studio) asked this broadcast to end."""
        if self.relay and event.get('session') == self.session_id:
            await self.finish('ended', event.get('message') or 'The stream was ended from another device.')

    # ------------------------------------------------------------------ helpers
    async def send_json(self, data):
        await self.send(text_data=json.dumps(data))

    async def send_status(self, state, message, **extra):
        await self.send_json({'type': 'status', 'state': state, 'message': message,
                              'session': self.session_id, **extra})

    @database_sync_to_async
    def load(self, church_slug):
        from accounts.services import is_verified
        from churches.models import Church, StreamSettings
        from churches.permissions import get_role

        from .models import StreamSession
        from churches.tenancy import tenant_slug_from_scope
        tenant = tenant_slug_from_scope(self.scope)
        church = Church.objects.filter(slug=church_slug, is_active=True).first()
        if church is None or (tenant is not None and tenant != church_slug):
            return None
        ss, _ = StreamSettings.objects.get_or_create(church=church)
        available, reason = browser_streaming_status(ss)
        return {
            'church_id': church.id, 'role': get_role(self.user, church), 'verified': is_verified(self.user),
            'resolution': ss.resolution, 'available': available, 'reason': reason,
            'live_elsewhere': StreamSession.objects.filter(StreamSession.active_q(), church=church).exists(),
        }

    @database_sync_to_async
    def prepare(self, event_id, takeover):
        from django.db import transaction

        from churches.models import StreamSettings

        from .models import Schedule, StreamSession
        ss = StreamSettings.objects.select_related('church').get(church_id=self.church_id)
        available, reason = browser_streaming_status(ss)
        if not available:
            return {'error': reason}
        event = None
        if event_id:
            event = Schedule.objects.filter(pk=event_id, church_id=self.church_id).first()
            if event is None:
                return {'error': 'That event is not on this church’s calendar.'}
        with transaction.atomic():
            active = StreamSession.objects.select_for_update().filter(StreamSession.active_q(), church_id=self.church_id)
            if active.exists():
                if not takeover:
                    return {'error': 'This church is already live from another device.', 'live_elsewhere': True}
                active.update(status=StreamSession.Status.ENDED, ended_at=timezone.now(),
                              end_reason='Taken over by another device.')
            # Sessions whose studio vanished without saying goodbye
            StreamSession.objects.filter(church_id=self.church_id, status__in=StreamSession.ACTIVE).update(
                status=StreamSession.Status.ENDED, ended_at=timezone.now(), end_reason='Lost contact with the studio.')
            session = StreamSession.objects.create(church_id=self.church_id, event=event, started_by=self.user,
                                                   resolution=ss.resolution)
        if event and not event.is_hybrid:
            Schedule.objects.filter(pk=event.pk).update(is_hybrid=True)
        return {'key': ss.get_stream_key(), 'session_id': session.id}

    @database_sync_to_async
    def mark(self, status=None, went_live=False):
        from .models import StreamSession
        fields = {'last_seen_at': timezone.now(), 'bytes_received': self.bytes}
        if status:
            fields['status'] = status
        if went_live:
            fields['went_live_at'] = timezone.now()
        StreamSession.objects.filter(pk=self.session_id, status__in=StreamSession.ACTIVE).update(**fields)

    @database_sync_to_async
    def end_session(self, session_id, state, message):
        from .models import StreamSession
        StreamSession.objects.filter(pk=session_id, status__in=StreamSession.ACTIVE).update(
            status=state, ended_at=timezone.now(), end_reason=message[:255], last_seen_at=timezone.now(),
            bytes_received=getattr(self, 'bytes', 0))
