from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.contrib.auth.models import UserManager as DjangoUserManager
from django.db import models
from django.db.models import Q


class UserQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)

    def platform_admins(self):
        """People who can use the admin dashboard (platform admins and system admins)."""
        return self.filter(Q(is_platform_admin=True) | Q(is_superuser=True))

    def search(self, term):
        term = (term or '').strip()
        if not term:
            return self
        return self.filter(Q(username__icontains=term) | Q(email__icontains=term)
                           | Q(first_name__icontains=term) | Q(last_name__icontains=term))


class UserManager(DjangoUserManager.from_queryset(UserQuerySet)):
    """
    Three kinds of account, from least to most powerful:
      - create_user():           an ordinary person (roles come from church memberships)
      - create_platform_admin(): runs the platform from the in-app admin dashboard;
                                 no access to the Django admin
      - create_superuser():      the system admin; full Django admin (/admin/)
    """
    use_in_migrations = True

    @classmethod
    def normalize_email(cls, email):
        return super().normalize_email(email or '').strip().lower()

    def get_by_login(self, login):
        """Find a user by username or email (case-insensitive). Returns None if not found."""
        login = (login or '').strip()
        if not login:
            return None
        field = 'email__iexact' if '@' in login else 'username__iexact'
        return self.filter(**{field: login}).order_by('pk').first()

    def create_platform_admin(self, username, email, password=None, **extra_fields):
        if not email:
            raise ValueError('Platform admins need an email address (for sign-in links and alerts).')
        extra_fields.setdefault('is_staff', False)
        extra_fields['is_platform_admin'] = True
        user = self.create_user(username, email, password, **extra_fields)
        if password is None:
            user.set_unusable_password()  # they choose one through the emailed link
            user.save(update_fields=['password'])
        return user

    def promote_to_platform_admin(self, user):
        if not user.is_platform_admin:
            user.is_platform_admin = True
            user.save(update_fields=['is_platform_admin'])
        return user

    def demote_platform_admin(self, user):
        if user.is_platform_admin:
            user.is_platform_admin = False
            user.save(update_fields=['is_platform_admin'])
        return user


class User(AbstractUser):
    """
    The project's user. It keeps Django's original `auth_user` table so existing
    databases carry on unchanged; `is_platform_admin` is the only new column.
    """
    id = models.AutoField(primary_key=True)  # matches the existing auth_user.id column
    is_platform_admin = models.BooleanField(
        default=False,
        help_text='Can use the in-app admin dashboard to manage churches, people and roles. '
                  'Onboard with: python manage.py platform add-admin',
    )

    objects = UserManager()

    class Meta(AbstractUser.Meta):
        db_table = 'auth_user'

    @property
    def can_admin_platform(self):
        return self.is_active and (self.is_platform_admin or self.is_superuser)

    @property
    def account_type(self):
        if self.is_superuser:
            return 'system_admin'
        return 'platform_admin' if self.is_platform_admin else 'user'


class SocialAccount(models.Model):
    """A sign-in identity from an outside provider (currently Google), linked to a user."""
    PROVIDERS = [('google', 'Google')]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='social_accounts')
    provider = models.CharField(max_length=20, choices=PROVIDERS)
    uid = models.CharField(max_length=255, help_text="The provider's stable user id (Google 'sub').")
    email = models.EmailField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_login = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['provider', 'uid'], name='unique_social_identity')]

    def __str__(self):
        return f'{self.get_provider_display()}: {self.email or self.uid} → {self.user}'
