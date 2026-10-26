# Code review — October 2026

All fixes are covered by tests: `python manage.py test` gives 25 passing tests. Before the review, there was 1 test and it failed.

## After pulling these changes
```bash
pip install -r requirements.txt
python manage.py migrate      # adds Profile + token blacklist, backfills profiles
python manage.py test
python manage.py runserver    # serves HTTP + WebSockets (daphne)
```
Give people roles in /admin → Users → (user) → Profile. Superusers become Clerk automatically.

## Critical (the features didn't work)
- **No `Profile` model existed.** Every permission check and the chat read `user.profile.role`, so no user could ever create, edit or delete songs, queries or schedules. Even a superuser got 403. Added `Profile` with roles, auto-creation and a backfill migration.
- **Clerks were locked out of hymnal edits.** `IsRecordingClerk` excluded Clerks, the global admins.
- **WebSockets were never wired up.** `asgi.py` had Channels commented out and `channels`/`daphne` were missing from INSTALLED_APPS. Chat also used session auth while the API uses JWT, so the frontend could never authenticate. Added JWT-over-querystring middleware and an origin check.
- **`requirements.txt` didn't install.** psycopg2-binary 2.9.9 has no wheels for Python 3.13/3.14, and DRF 3.14 doesn't support Django 6. Versions are now updated and range-pinned.
- **Health check and API root returned 401.** They inherited `IsAuthenticated`. The API root's name `api-root` also collided with DRF's router.

## Bugs
- `/schedules/upcoming/` returned the 10 events *furthest away*, not the next 10, and required login. It's now public and soonest-first.
- `/queries/active/` required login. It's now public.
- Clerks' schedule list dropped the `type` and date filters, because the queryset was replaced after filtering.
- Bad query params (`?number_min=abc`, `?start_date=nope`) crashed with 500. They now return 400.
- The login response never included `user`, because it read `request.user`, which is anonymous during login. It now returns the user and role.
- Rotated refresh tokens were never blacklisted, because the blacklist app wasn't installed. Added `/auth/logout/`.
- `created_by` could be set to any user by the client. It's now read-only and taken from the request.
- A schedule could end before it started. Added serializer validation and a DB constraint.
- Two queries could be active at once under a race. Added a DB partial unique constraint and an atomic save.
- `DEVELOPMENT.md` documented `/api/v1/auth/login/`, but that path returned 404. It now exists, and `/api/v1/token/` is kept as an alias.

## Efficiency / loops sealed
- List endpoints were unbounded (every song with full lyrics). They are now paginated at 50.
- N+1 on `created_by`: `select_related` added. A test asserts a constant query count.
- Chat did a DB role lookup on **every message**. It now checks once at connect.
- Chat had no message size cap or flood limit. Now: 2000 chars, 30 messages/min per socket.
- Chat `disconnect` announced "left" even for rejected connections. It now only does so if the user joined.
- The health check's Redis ping had no timeout, so a down Redis could hang it. It's now 1s and only runs when `REDIS_URL` is set.
- Added indexes on schedule `(is_published, start_time)`, song title and song type.

## Config / security
- Secret key, DEBUG, hosts, CORS, Redis and Postgres now come from `.env` (see `.env.example`). The app refuses to start with DEBUG off and no secret key. Production security headers switch on automatically.
- Without `REDIS_URL`, chat uses an in-memory layer, so local development needs no Redis.
- `TIME_ZONE` is now Africa/Nairobi (configurable). Data is still stored in UTC.
- Admin now registers all models, with roles editable inline on Users.

## Still open
- ~~Frontend is the untouched Vite starter page~~. Done: the church site is built in `frontend/` (see its README).
- Three virtual environments exist (`backend/myenv`, `.venv`, `venv`), and they all sit inside OneDrive. Keep one. Venvs and `node_modules` in OneDrive cause slow syncs and "cloud-only" file errors.
- No `core` models yet; `core` only serves the root/health endpoints.

## Follow-up (frontend build)
- `/schedules/upcoming/` now includes events **in progress**. Before this, a live service dropped off the list the moment it started.
- `/schedules/?order=asc` lists soonest first, for paginated "upcoming" views.
