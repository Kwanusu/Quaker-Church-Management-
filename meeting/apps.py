from django.apps import AppConfig


class MeetingConfig(AppConfig):
    name = 'meeting'
    verbose_name = 'Meeting'

    def ready(self):
        from . import signals  # noqa: F401  (registers signal handlers)
