from django.apps import AppConfig


class PlatformAdminConfig(AppConfig):
    """The in-app admin dashboard API and the `manage.py platform` script (no models of its own)."""
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'platformadmin'
    verbose_name = 'Platform administration'
