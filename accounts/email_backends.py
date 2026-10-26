"""
Development email backend: prints each email to the terminal as plain, readable text.

Django's own console backend prints the raw encoded message, where long lines
(such as confirmation and password-reset links) are split with "=" and the link
you click is cut short ("This confirmation link is not valid"). This one prints
the text body exactly as the recipient would read it, so links work when clicked
or copied. Used automatically when no EMAIL_HOST is configured.
"""
import threading

from django.core.mail.backends.console import EmailBackend as ConsoleBackend


class ReadableConsoleBackend(ConsoleBackend):
    _lock = threading.RLock()

    def write_message(self, message):
        line = '-' * 79
        out = [
            line,
            f'Subject: {message.subject}',
            f'From: {message.from_email}',
            f'To: {", ".join(message.to)}',
            '',
            message.body.rstrip(),
            '',
        ]
        text = '\n'.join(out) + '\n'
        try:
            self.stream.write(text)
        except UnicodeEncodeError:  # e.g. a Windows terminal that can't show curly quotes
            encoding = getattr(self.stream, 'encoding', None) or 'ascii'
            self.stream.write(text.encode(encoding, 'replace').decode(encoding))

    def send_messages(self, email_messages):
        # Keep emails sent from background threads from interleaving in the terminal.
        with self._lock:
            return super().send_messages(email_messages)
