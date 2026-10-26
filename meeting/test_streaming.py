"""Browser studio: WebM chunks over a WebSocket → ffmpeg → (here) an FLV file instead of YouTube."""
import json
import shutil
import subprocess
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.test import TestCase, TransactionTestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import User
from accounts.services import mark_verified
from churches.models import Church, Membership, Role, StreamSettings
from meeting.models import Schedule, StreamSession
from meeting.services import set_up_new_church
from meeting.streaming import build_command, explain_failure
from quaker_church_mangement_system.asgi import application

FFMPEG = shutil.which('ffmpeg')
KEY = 'abcd-efgh-ijkl-mnop'


def person(name, church=None, role=None):
    u = User.objects.create_user(name, f'{name}@example.com', 'Str0ng!pass-9x', first_name=name.title())
    mark_verified(u)
    if church:
        Membership.objects.create(user=u, church=church, role=role)
    return u


def sample_webm(seconds=6):
    """A short WebM like Chrome's MediaRecorder makes (VP8 video + Opus audio)."""
    out = Path(tempfile.mkdtemp()) / 'sample.webm'
    subprocess.run([FFMPEG, '-loglevel', 'error', '-f', 'lavfi', '-i', f'testsrc=size=640x360:rate=30:duration={seconds}',
                    '-f', 'lavfi', '-i', f'sine=frequency=440:duration={seconds}', '-c:v', 'libvpx', '-b:v', '800k',
                    '-c:a', 'libopus', '-shortest', str(out)], check=True)
    return out.read_bytes()


class CommandTests(TestCase):
    def test_command_uses_preset_and_hides_nothing_unexpected(self):
        with override_settings(FFMPEG_PATH='/usr/bin/ffmpeg', STREAM_OUTPUT_URL=''):
            cmd = build_command('1080p', KEY)
        self.assertIn('4500k', cmd)
        self.assertIn('scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,'
                      'fps=30,format=yuv420p', cmd)
        self.assertEqual(cmd[-1], f'rtmps://a.rtmps.youtube.com:443/live2/{KEY}')
        with override_settings(FFMPEG_PATH='/usr/bin/ffmpeg', STREAM_USE_RTMPS=False):
            silent = build_command('720p', KEY, has_audio=False)
        self.assertIn('anullsrc=channel_layout=stereo:sample_rate=44100', ' '.join(silent))
        self.assertTrue(silent[-1].startswith('rtmp://a.rtmp.youtube.com/live2/'))

    def test_failure_messages(self):
        self.assertIn('stream key', explain_failure('rtmps://…/live2/••••: Input/output error'))
        self.assertIn('internet', explain_failure('Failed to resolve hostname a.rtmps.youtube.com: Name or service not known'))


class StudioApiTests(TestCase):
    def setUp(self):
        self.church = Church.objects.create(name='Kaimosi')
        set_up_new_church(self.church)
        self.clerk = person('grace', self.church, Role.CLERK)
        self.api = APIClient()
        self.api.force_authenticate(self.clerk)
        self.url = f'/api/v1/churches/{self.church.slug}/streaming/'

    def test_state_and_checklist(self):
        r = self.api.get(self.url)
        self.assertFalse(r.data['browser_streaming']['available'])
        self.assertIn('server_ready', r.data['browser_streaming'])
        self.assertIsNone(r.data['live_session'])
        with override_settings(FFMPEG_PATH='/usr/bin/ffmpeg'):
            r = self.api.patch(self.url, {'stream_key': KEY}, format='json')
            self.assertTrue(r.data['browser_streaming']['available'], r.data)
            self.assertEqual((r.data['browser_streaming']['width'], r.data['browser_streaming']['video_kbps']), (1280, 3000))
        with override_settings(FFMPEG_PATH='', STREAM_OUTPUT_URL=''), unittest.mock.patch('shutil.which', return_value=None):
            r = self.api.get(self.url)
            self.assertIn('ffmpeg', r.data['browser_streaming']['reason'])

    def test_live_shows_on_church_and_remote_stop(self):
        s = StreamSession.objects.create(church=self.church, started_by=self.clerk, status='live')
        StreamSession.objects.filter(pk=s.pk).update(went_live_at=s.started_at)
        pub = APIClient()
        self.assertIsNotNone(pub.get(f'/api/v1/churches/{self.church.slug}/').data['live'])
        listing = pub.get('/api/v1/churches/').data['results']
        self.assertTrue(next(c for c in listing if c['slug'] == self.church.slug)['live'])
        self.assertEqual(self.api.get(self.url).data['live_session']['id'], s.id)
        r = self.api.post(f'{self.url}stop/')
        self.assertEqual(r.data['stopped'], 1)
        self.assertIsNone(pub.get(f'/api/v1/churches/{self.church.slug}/').data['live'])
        self.assertEqual(StreamSession.objects.get(pk=s.pk).status, 'ended')
        # A session whose studio went quiet no longer counts as live
        old = StreamSession.objects.create(church=self.church, status='live')
        StreamSession.objects.filter(pk=old.pk).update(last_seen_at=old.started_at.replace(year=2020))
        self.assertIsNone(pub.get(f'/api/v1/churches/{self.church.slug}/').data['live'])

    def test_only_the_clerk(self):
        member = person('mary', self.church, Role.RECORDING_CLERK)
        c = APIClient()
        c.force_authenticate(member)
        self.assertEqual(c.get(self.url).status_code, 403)
        self.assertEqual(c.post(f'{self.url}stop/').status_code, 403)


@unittest.skipUnless(FFMPEG, 'ffmpeg not installed')
class StudioRelayTests(TransactionTestCase):
    def setUp(self):
        self.church = Church.objects.create(name='Kaimosi')
        set_up_new_church(self.church)
        self.clerk = person('grace', self.church, Role.CLERK)
        ss, _ = StreamSettings.objects.get_or_create(church=self.church)
        ss.set_stream_key(KEY)
        ss.resolution = '480p'
        ss.save()
        self.out = Path(tempfile.mkdtemp())
        self.event = Schedule.objects.create(church=self.church, title='Sunday worship', is_hybrid=False,
                                             start_time='2026-10-25T07:00:00Z', end_time='2026-10-25T09:00:00Z')

    def connect(self, user):
        return WebsocketCommunicator(application, f'/ws/stream/{self.church.slug}/?token={AccessToken.for_user(user)}',
                                     headers=[(b'origin', b'http://localhost:5173')])

    async def next_status(self, comm, want, timeout=15):
        for _ in range(60):
            msg = json.loads(await comm.receive_from(timeout=timeout))
            if msg.get('state') in want or msg.get('type') in want:
                return msg
        raise AssertionError(f'no {want}')

    def test_only_clerk_can_open_the_studio(self):
        cases = ((person('mem', self.church, Role.MEMBER), 4403), (person('out'), 4403))

        async def run():
            for user, code in cases:
                comm = self.connect(user)
                connected, close_code = await comm.connect()
                self.assertEqual((connected, close_code), (False, code))
        async_to_sync(run)()

    def test_browser_video_reaches_the_output(self):
        data = sample_webm()
        target = self.out / 'live-{key}.flv'

        async def run():
            with override_settings(STREAM_OUTPUT_URL=str(target)):
                comm = self.connect(self.clerk)
                self.assertTrue((await comm.connect())[0])
                ready = json.loads(await comm.receive_from())
                self.assertEqual((ready['type'], ready['available'], ready['resolution']), ('ready', True, '480p'))
                await comm.send_to(text_data=json.dumps({'action': 'start', 'event': self.event.id}))
                self.assertEqual((await self.next_status(comm, {'starting'}))['state'], 'starting')
                # A second device can't start at the same time…
                other = self.connect(self.clerk)
                await other.connect()
                await other.receive_from()
                await other.send_to(text_data=json.dumps({'action': 'start'}))
                busy = await self.next_status(other, {'failed'})
                self.assertIn('already live', busy['message'])
                await other.disconnect()
                # …send the "recording" in chunks, as the browser does every half second
                step = max(1, len(data) // 12)
                for i in range(0, len(data), step):
                    await comm.send_to(bytes_data=data[i:i + step])
                live = await self.next_status(comm, {'live'})
                self.assertGreater(live['kbps'], 0)
                await comm.send_to(text_data=json.dumps({'action': 'stop'}))
                ended = await self.next_status(comm, {'ended', 'failed'})
                self.assertEqual(ended['state'], 'ended', ended)
                await comm.disconnect()
        async_to_sync(run)()

        out = self.out / f'live-{KEY}.flv'
        probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_name,width,height',
                                '-of', 'json', str(out)], capture_output=True, text=True, check=True)
        streams = {s['codec_name']: s for s in json.loads(probe.stdout)['streams']}
        self.assertEqual(set(streams), {'h264', 'aac'})
        self.assertEqual((streams['h264']['width'], streams['h264']['height']), (854, 480))
        s = StreamSession.objects.get()
        self.assertEqual((s.status, s.event, s.started_by), ('ended', self.event, self.clerk))
        self.assertIsNotNone(s.went_live_at)
        self.assertGreater(s.bytes_received, 0)
        self.event.refresh_from_db()
        self.assertTrue(self.event.is_hybrid)

    def test_bad_destination_is_explained_and_key_hidden(self):
        async def run():
            with override_settings(STREAM_OUTPUT_URL='/nonexistent-dir/{key}/x.flv'):
                comm = self.connect(self.clerk)
                await comm.connect()
                await comm.receive_from()
                await comm.send_to(text_data=json.dumps({'action': 'start'}))
                await self.next_status(comm, {'starting'})
                await comm.send_to(bytes_data=sample_webm(2))
                failed = await self.next_status(comm, {'failed'})
                self.assertNotIn(KEY, failed['message'])
                await comm.disconnect()
        async_to_sync(run)()
        self.assertEqual(StreamSession.objects.get().status, 'failed')
