# Deploying: Render (API) + Cloudflare Pages (website) + Supabase (database and files)

```
Browser ──► Cloudflare Pages   static React site (frontend repo)
   │
   └──────► Render web service  Django + Channels in Docker (this repo, `prod` branch)
                 ├─► Supabase Postgres   all data   (Session pooler)
                 ├─► Supabase Storage    logos, avatars (public bucket, S3 API)
                 └─► Render Key Value    Redis for live chat
```

Branch rule: Render deploys the **`prod`** branch. Work goes feature branch → `main` (integration tests) → `prod`.

Do the steps in this order; each one produces a value the next needs.

## 1. Supabase

1. New project. Pick the region closest to your Render region. Save the database password.
2. **Connect → Session pooler** → copy the connection string. It looks like
   `postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres`.
   Add `?sslmode=require` at the end. This is `DATABASE_URL`.
   Use the **Session pooler** (port 5432). The direct connection is IPv6-only and Render can't reach it.
3. **Storage → New bucket** named `media`, set to **Public**.
4. **Storage → S3 Connection** (Project Settings → Storage): enable it, create an access key.
   Keep the key id and secret (`SUPABASE_S3_KEY_ID`, `SUPABASE_S3_SECRET`).
   `SUPABASE_PROJECT_REF` is the `<ref>` in `https://<ref>.supabase.co`.
5. Leave Row Level Security alone: the API talks to Postgres directly and is the only thing that reads the data.
   Don't expose the database through Supabase's own API.

## 2. Render

1. **New → Blueprint**, pick the backend repo, branch `prod`. Render reads `render.yaml` and creates the web service
   and the Redis-compatible Key Value store (`REDIS_URL` is wired automatically).
2. Fill in the values it asks for:

   | Variable | Value |
   |---|---|
   | `DATABASE_URL` | from Supabase step 2 |
   | `SUPABASE_PROJECT_REF`, `SUPABASE_S3_KEY_ID`, `SUPABASE_S3_SECRET` | from Supabase step 4 |
   | `FRONTEND_URL`, `CORS_ALLOWED_ORIGINS` | your Pages address, e.g. `https://friends-portal.pages.dev` (no trailing slash). Not known yet: put a placeholder and come back after step 3. |
   | `API_PUBLIC_URL` | the web service's URL, e.g. `https://friends-portal-api.onrender.com` |
   | `EMAIL_HOST`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL` | your SMTP account |
   | `GOOGLE_CLIENT_ID` | optional; leave blank to hide Google sign-in |

   `DJANGO_SECRET_KEY` and `FIELD_ENCRYPTION_KEY` are generated for you and kept between deploys.
   Never change `FIELD_ENCRYPTION_KEY` later: every church would have to re-enter its stream key.
3. Deploy. On each start the container runs `migrate`, then Daphne. Check `https://<service>/health/`.
4. **Shell** tab, once:
   ```bash
   python manage.py createsuperuser                      # system admin (Django admin at /admin/)
   python manage.py platform add-admin --email you@example.org --first-name You
   python manage.py import_hymns                         # optional: the shared Nyimbo Za Injili catalog
   python manage.py load_bible                           # optional: Bible text, if you use it
   ```

The `starter` plan is the smallest one that keeps the service awake, which WebSockets and the live studio need.
The free plan sleeps after inactivity and would drop chat connections.

## 3. Cloudflare Pages

1. **Workers & Pages → Create → Pages → Connect to Git**, pick the frontend repo, production branch `prod`.
2. Build settings: framework **Vite** (or: build command `npm run build`, output directory `dist`). Node 22 is set by `.node-version`.
3. Environment variables (Production):

   | Variable | Value |
   |---|---|
   | `VITE_API_URL` | the Render URL, e.g. `https://friends-portal-api.onrender.com` (no trailing slash) |
   | `VITE_PLATFORM_NAME`, `VITE_PLATFORM_SUBTITLE`, `VITE_PLATFORM_TAGLINE` | branding |

   These are baked in at build time, so changing one needs a new build.
4. Deploy. `public/_redirects` makes deep links such as `/c/friends-church` and page refreshes work.
5. Go back to Render and set `FRONTEND_URL` and `CORS_ALLOWED_ORIGINS` to the real Pages address, then redeploy.
   If you later add a custom domain, list both addresses in `CORS_ALLOWED_ORIGINS`, comma-separated.
6. Google sign-in: add the Pages address under **Authorized JavaScript origins** in Google Cloud.

## 4. Check it works

- `https://<api>/health/` answers `ok`.
- Open the Pages site, register, and confirm the email arrives with the logo and a working link.
- Upload an avatar on the account page; the picture should load from `…supabase.co/storage/v1/object/public/media/…`.
- Open a church's Messages page and send a message from two browsers (this proves Redis and WebSockets).

## Limits to know about

- **Church subdomains are off.** Cloudflare Pages can't serve `*.yourdomain` wildcard hostnames, so leave
  `TENANT_BASE_DOMAIN` and `VITE_TENANT_BASE_DOMAIN` unset. Churches live at `/c/<slug>`. Subdomains would need the
  website served from a Cloudflare Worker or another host with a wildcard certificate.
- **Live studio uses the API server's CPU** (ffmpeg relays each broadcast). One or two broadcasts at a time are fine on
  `starter`; plan a larger instance or a separate worker before many churches stream at once.
- **Backups:** Supabase takes daily backups on paid plans only. On the free plan, schedule your own `pg_dump`.
- **Sign-in is per site.** Tokens are stored in the browser for the Pages origin, so one sign-in covers the whole site
  while churches share the `/c/<slug>` layout.
