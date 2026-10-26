# Deploying: Render (API) + Cloudflare Worker (website) + Supabase (database and files)

```
Browser ──► Cloudflare Worker   the built React site, on yourdomain.com AND *.yourdomain.com (one per church)
   │
   └──────► Render web service  Django + Channels in Docker (this repo, `prod` branch), e.g. api.yourdomain.com
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
   | `TENANT_BASE_DOMAIN` | your domain, e.g. `friends.example.org`. A church with the slug `kaimosi` is then served at `kaimosi.friends.example.org`. |
   | `FRONTEND_URL`, `CORS_ALLOWED_ORIGINS` | the platform's own site, e.g. `https://friends.example.org` (no trailing slash). Church subdomains are allowed automatically. |
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

## 3. Cloudflare Worker (the website, with church subdomains)

Cloudflare Pages can't serve a wildcard hostname, so the site is a Worker with static assets. One Worker answers
`yourdomain.com` and every `*.yourdomain.com`. The browser then calls the API on its own address; the backend learns
which church is meant from the `Origin` of the calling site, so the Worker doesn't need to proxy anything.

1. Your domain's DNS must be on Cloudflare. Add two **proxied** (orange cloud) records:
   - `A` `@` → `192.0.2.1` (a placeholder address: Cloudflare answers before it is used)
   - `A` `*` → `192.0.2.1`
   Cloudflare's free certificate covers `*.yourdomain.com` (one level), which is what church subdomains need.
2. In the frontend repo, edit `wrangler.jsonc`: replace `yourdomain.com` (twice) with your domain, and commit.
3. **Workers & Pages → Create → Import a repository** (Workers Builds), branch `prod`. Settings:
   - Build command: `npm run build`
   - Deploy command: `npx wrangler deploy`
   - **Build variables** (baked into the site at build time; change one and rebuild):

     | Variable | Value |
     |---|---|
     | `VITE_API_URL` | the API address, e.g. `https://api.example.org` (or the `onrender.com` URL), no trailing slash |
     | `VITE_TENANT_BASE_DOMAIN` | the same domain as `TENANT_BASE_DOMAIN` on Render |
     | `VITE_PLATFORM_NAME`, `VITE_PLATFORM_SUBTITLE`, `VITE_PLATFORM_TAGLINE` | branding |
     | `NODE_VERSION` | `22` |
4. Optional, tidier: add `api.yourdomain.com` as a custom domain on the Render service (Render shows the CNAME to
   create; leave that record **DNS only**, not proxied), then use it as `VITE_API_URL` and `API_PUBLIC_URL`.
   `api` can't be a church name; it is reserved.
5. Google sign-in: add `https://yourdomain.com` under **Authorized JavaScript origins**. Google doesn't accept
   wildcards, so sign-in with Google works on the platform site only; churches use email and password.

To deploy from your own machine instead: `npm run deploy` (needs `npx wrangler login` once).

## 4. Check it works

- `https://<api>/health/` answers `ok`.
- Open `https://yourdomain.com`, register, and confirm the email arrives with the logo and a working link.
- Upload an avatar on the account page; the picture should load from `…supabase.co/storage/v1/object/public/media/…`.
- Open a church's Messages page and send a message from two browsers (this proves Redis and WebSockets).

## Limits to know about

- **A church's subdomain exists as soon as the church is active.** Nothing to configure per church. The names `www`,
  `api`, `admin`, `app` and a few others are reserved and can't be used as church addresses.
- **Sign-in is per address.** The browser keeps the login for the exact site it was made on, so a person who belongs to
  two churches signs in separately on each subdomain. Fixing that needs a shared sign-in design (not built yet).
- **Live studio uses the API server's CPU** (ffmpeg relays each broadcast). One or two broadcasts at a time are fine on
  `starter`; plan a larger instance or a separate worker before many churches stream at once.
- **Backups:** Supabase takes daily backups on paid plans only. On the free plan, schedule your own `pg_dump`.
