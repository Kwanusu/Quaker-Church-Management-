# Email, Google sign-in, reminders and live streaming

All settings go in `backend/quaker_church_mangement_system/.env` (copy `.env.example`).
Restart the server after changing it.

## 1. Email (any SMTP account)

Without `EMAIL_HOST`, emails are **printed in the server terminal**. That's handy for trying
things out: open the confirmation or reset link from there (each link is printed whole, on one line).
Links in emails printed before this fix were cut short; sign in and use **Resend** to get a fresh one.

To send real email, fill in your provider's SMTP details:

| Provider | EMAIL_HOST | EMAIL_PORT | User / password |
|---|---|---|---|
| Gmail / Google Workspace | smtp.gmail.com | 587 | your address + an **App Password** (Google Account → Security → 2-Step Verification → App passwords) |
| Brevo | smtp-relay.brevo.com | 587 | from Brevo → SMTP & API |
| Zoho Mail | smtp.zoho.com | 587 | your address + app password |
| cPanel hosting | mail.your-domain.org | 465 | mailbox address + password |

Also set:
- `DEFAULT_FROM_EMAIL=Friends Church <no-reply@your-domain.org>`
- `FRONTEND_URL=https://your-site.org` (links in emails point here)

What gets sent:

| Email | When |
|---|---|
| Confirm your email | sign-up; changing email address |
| Reset password / Password changed | "Forgot password?"; any password change |
| Welcome to <church> | joining a church |
| <name> joined <church> | to that church's Clerks |
| You are now <role> | when the Clerk changes someone's role |
| Announcements | each post in a church's Announcements room, to its members (opt-out) |
| Tomorrow: <worship> | day-before reminder for published worship (opt-out) |

Church emails only go to **confirmed** addresses. Each one has an unsubscribe link, and
people can also change these preferences on their Account page.

## 2. Sign in with Google

1. Go to https://console.cloud.google.com and create a project (e.g. "Friends Church").
2. **APIs & Services → OAuth consent screen**: choose External, set the app name and logo, and add your support email.
3. **Credentials → Create credentials → OAuth client ID → Web application**.
   Under **Authorized JavaScript origins** add `http://localhost:5173` and your live site, e.g. `https://your-site.org`.
4. Copy the **Client ID** into `.env` as `GOOGLE_CLIENT_ID=...`.

The "Continue with Google" button appears automatically once that's set. Google accounts
count as email-confirmed. If someone already has an account with the same email, Google
sign-in links to it. Google-only accounts can add a password from their Account page.

## 3. Worship reminders (run hourly)

```bash
python manage.py send_worship_reminders
```
This emails members about published **Sunday Worship** events starting in the next 24 hours.
It is safe to run as often as you like: each event is only ever reminded once.

**Windows (Task Scheduler):** create a task that runs hourly:
- Program: `C:\Users\Admin\Desktop\quaker-faith\backend\quaker_church_mangement_system\.venv\Scripts\python.exe`
- Arguments: `manage.py send_worship_reminders`
- Start in: `C:\Users\Admin\Desktop\quaker-faith\backend\quaker_church_mangement_system`

**Linux server (cron):** `5 * * * * cd /srv/app && .venv/bin/python manage.py send_worship_reminders`

## 4. Live streaming from a camcorder (browser studio → YouTube)

The Clerk opens **Manage → Live streaming**: a studio with the camera preview, a sound meter, camera and
microphone pickers, the event that's on air, and a **Go live** button. While live it shows the time on air and
the upload speed, and the church page shows **● Live now** with the player.

```
camcorder ─HDMI→ USB capture card ─→ laptop browser (Chrome/Edge) ─→ this server (ffmpeg) ─→ YouTube Live
```

**One-time setup**

1. **ffmpeg on the server** (it converts the browser's video into what YouTube accepts):
   - Windows: `winget install Gyan.FFmpeg` (then open a new terminal), or download from https://www.gyan.dev/ffmpeg/builds/ and set `FFMPEG_PATH=C:\ffmpeg\bin\ffmpeg.exe` in `.env`
   - macOS: `brew install ffmpeg` · Ubuntu/Debian: `sudo apt install ffmpeg`
2. **YouTube**: in YouTube Studio → Create → Go live, enable live streaming (the first time can take 24 hours),
   then copy a **reusable stream key** into the studio's checklist. It is stored encrypted and never sent to browsers.
3. **Church settings**: add the channel ID so the stream shows on the church page.
4. **HTTPS in production**: browsers only allow camera access on `https://` sites (or `localhost`).

**Good to know**

- Keep the studio page open while live; closing it ends the broadcast. Another Clerk device sees
  "live from another device" and can end it or take over.
- Quality (480p / 720p / 1080p) sets the size and bitrate sent to YouTube; the server re-encodes with H.264,
  2-second keyframes and AAC audio. 720p needs about 5 Mbps upload from the laptop and from the server.
- The server uses one CPU core or so per live church at 720p. For several churches live at once, use a server with
  enough cores, or let those churches use OBS (details under "Use OBS or a hardware encoder instead").
- Testing without YouTube: set `STREAM_OUTPUT_URL=/tmp/test-{key}.flv` and the stream is written to that file.
