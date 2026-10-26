# Admins: who can do what

| Account | How it's made | Where they work | Can do |
|---|---|---|---|
| **System admin** | `python manage.py createsuperuser` | Django admin at `/admin/` (and the dashboard) | Everything, including platform admins' accounts |
| **Platform admin** | `python manage.py platform add-admin …` | Admin dashboard in the website: **Admin** button → `/admin-dashboard` | People & roles in any church, suspend/reactivate churches, suspend/reactivate accounts, confirm emails, send password resets |
| **Church Clerk** | Registers a church, or is appointed | Their church's **Manage** page | Run their own church |

Platform admins cannot open the Django admin, cannot change their own account from the dashboard,
and cannot suspend or reset other admins. The system admin looks after admins.

## The platform script

Run from `backend/quaker_church_mangement_system` with the virtual environment active.

```bash
# Onboard a new platform admin. They get an email with a link to choose their password (valid 3 days).
python manage.py platform add-admin --email ruth@example.org --first-name Ruth --last-name Akinyi

# Someone who already has an account? Same command: they are promoted and emailed a link to the dashboard.
python manage.py platform add-admin --email grace@example.org

# No email set up yet? Print the set-password link to give them yourself:
python manage.py platform add-admin --email ruth@example.org --no-email
# ...or type their password now:
python manage.py platform add-admin --email ruth@example.org --set-password

python manage.py platform resend-invite ruth@example.org   # new link if the first one expired
python manage.py platform list-admins                      # shows "invite pending" until they set a password
python manage.py platform remove-admin ruth@example.org    # takes away admin rights; account and church roles stay
python manage.py platform status                           # counts at a glance
```

Options for `add-admin`: `--username` (default: the part of the email before @), `--first-name`, `--last-name`,
`--set-password`, `--no-email`.

## The dashboard

- **Overview**: people and churches, new in the last 30 days, suspended accounts and churches, unconfirmed
  emails, roles across all churches, churches that have no Clerk (with an **Appoint a Clerk** button), newest sign-ups.
- **People & roles**: search anyone, open them to change their role in each church, add them to a church
  with any role (e.g. make them Clerk of a church they haven't joined), remove them, suspend or reactivate
  the account, mark their email as confirmed, send a password reset email.
- **Churches**: every church including suspended ones; open one to add people, change roles, or
  suspend/reactivate it. A suspended church's pages are hidden from the public.

Rules kept everywhere: every church keeps at least one Clerk; suspended accounts are signed out on all
devices and can't sign in; people are emailed when their role changes.

## The user model (for developers)

`AUTH_USER_MODEL = 'accounts.User'`. It keeps Django's original `auth_user` table, so existing databases and
users carry on as before: `migrate` only adds the `is_platform_admin` column. Its manager (`User.objects`) adds:

- `create_user()`, `create_superuser()` (Django's), and `create_platform_admin(username, email, password=None)`
- `promote_to_platform_admin(user)`, `demote_platform_admin(user)`
- `get_by_login("name or email")` (case-insensitive)
- `.active()`, `.platform_admins()`, `.search("text")`

Emails are stored in lower case. In code, use `django.contrib.auth.get_user_model()` or
`from accounts.models import User`, never `django.contrib.auth.models.User`.

Production note: if a reverse proxy sends `/admin…` to Django, make it match `/admin/` only, so the website's
`/admin-dashboard` page still reaches the frontend.
