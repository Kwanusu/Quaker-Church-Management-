"""
Sending email.

Every message has a plain-text and an HTML version rendered from
templates/emails/<name>.txt / <name>.html, with the subject in <name>_subject.txt.

- send_email(): one recipient, sent now (account emails: confirm, reset, …).
- send_bulk(): many recipients, each gets their own copy (no shared To: list),
  sent in a background thread when settings.EMAIL_ASYNC is on, after the
  database transaction commits, so a slow mail server never delays a request.

Failures are logged, never raised: an email outage must not break sign-up,
chat or joining a church.
"""
import logging
from concurrent.futures import ThreadPoolExecutor
from email.utils import formataddr, parseaddr
from pathlib import Path

from django.conf import settings
from django.core import signing
from django.core.mail import EmailMultiAlternatives, get_connection
from django.db import close_old_connections, transaction
from django.template.loader import render_to_string

log = logging.getLogger(__name__)
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='email')

UNSUBSCRIBE_SALT = 'accounts.unsubscribe'

# The brand mark is attached to each message and referenced as cid:logo. Mail apps block remote and data: images by
# default, and a localhost/private URL would never load for the recipient, so an attachment is the reliable route.
LOGO_PATH = Path(__file__).resolve().parent / 'email_assets' / 'logo-mark.png'
LOGO_CID = 'logo-mark'


def _context(extra):
    return {
        'platform_name': settings.PLATFORM_NAME,
        'site_url': settings.FRONTEND_URL,
        'guiding_text': 'You are my friends if you do what I command you.',
        'guiding_ref': 'John 15:14',
        'logo_cid': LOGO_CID,
        **extra,
    }


CHURCH_TEMPLATES = ('church_', 'announcement', 'worship_reminder')


def _church_sender(template, ctx):
    """Mail about a church comes from that church's name (replies go to its own address); the platform sends the rest."""
    church = ctx.get('church')
    if church is None or not template.startswith(CHURCH_TEMPLATES):
        return settings.DEFAULT_FROM_EMAIL, []
    address = parseaddr(settings.DEFAULT_FROM_EMAIL)[1]
    name = f'{church.name} via {settings.PLATFORM_NAME}'.replace('"', '')
    return formataddr((name, address)), ([church.email] if church.email else [])


class BrandedMessage(EmailMultiAlternatives):
    """Puts the logo inside the HTML part (multipart/related), which is where mail apps look for cid: images."""

    def _add_bodies(self, msg):
        super()._add_bodies(msg)
        try:
            data = LOGO_PATH.read_bytes()
        except OSError:
            log.warning('Email logo missing at %s', LOGO_PATH)
            return
        html = msg.get_body(preferencelist=('html',))
        if html is not None:
            html.add_related(data, 'image', 'png', cid=f'<{LOGO_CID}>', disposition='inline', filename='logo.png')


def build_message(template, to, context=None, headers=None):
    ctx = _context(context or {})
    sender, reply_to = _church_sender(template, ctx)
    subject = ' '.join(render_to_string(f'emails/{template}_subject.txt', ctx).split())
    text = render_to_string(f'emails/{template}.txt', ctx)
    html = render_to_string(f'emails/{template}.html', ctx)
    msg = BrandedMessage(subject, text, sender, [to], headers=headers or {}, reply_to=reply_to)
    msg.attach_alternative(html, 'text/html')
    return msg


def send_email(template, to, context=None, headers=None):
    """Send one email immediately. Returns True if the mail server accepted it."""
    if not to:
        return False
    try:
        return build_message(template, to, context, headers).send() == 1
    except Exception:  # noqa: BLE001 — log, don't break the request
        log.exception('Could not send %s email to %s', template, to)
        return False


def _send_many(messages):
    close_old_connections()
    try:
        with get_connection() as conn:  # one SMTP session for the whole batch
            sent = conn.send_messages(messages) or 0
        log.info('Sent %s of %s emails', sent, len(messages))
    except Exception:  # noqa: BLE001
        log.exception('Bulk email failed (%s messages)', len(messages))
    finally:
        close_old_connections()


def send_bulk(template, recipients):
    """
    recipients: iterable of (email, context, headers) — each person gets a
    personal copy (e.g. their own unsubscribe link). Sent after commit, in the
    background if EMAIL_ASYNC is on.
    """
    messages = []
    for email, ctx, headers in recipients:
        if email:
            try:
                messages.append(build_message(template, email, ctx, headers))
            except Exception:  # noqa: BLE001
                log.exception('Could not render %s for %s', template, email)
    if not messages:
        return 0

    def dispatch():
        if getattr(settings, 'EMAIL_ASYNC', True):
            _pool.submit(_send_many, messages)
        else:
            _send_many(messages)

    transaction.on_commit(dispatch)
    return len(messages)


# ---------------------------------------------------------------------------
# One-click unsubscribe links
# ---------------------------------------------------------------------------
def unsubscribe_token(user, kind):
    """Signed, non-expiring token for 'announcements' or 'reminders'."""
    return signing.dumps({'u': user.pk, 'k': kind}, salt=UNSUBSCRIBE_SALT)


def unsubscribe_url(user, kind):
    return f'{settings.FRONTEND_URL}/unsubscribe/{unsubscribe_token(user, kind)}'


def unsubscribe_headers(user, kind):
    """RFC 2369/8058 headers so mail apps show their own 'Unsubscribe' button."""
    api = f'{settings.FRONTEND_URL}/api/v1/auth/unsubscribe/{unsubscribe_token(user, kind)}/'
    return {'List-Unsubscribe': f'<{api}>', 'List-Unsubscribe-Post': 'List-Unsubscribe=One-Click'}
