Here is a clean, comprehensive, and professional `README.md` file custom-tailored for your Quaker Meeting Portal ecosystem. It details the unique values of the project, architecture layout, technical setup instructions, and RBAC governance configuration.

---

# 🕊️ Friends Portal: Quaker Meeting Ecosystem

An open-source, simplified, and highly modern church management system and virtual meetinghouse built explicitly around the **Quaker Faith and Practice**. This ecosystem facilitates hybrid unprogrammed/programmed worship, communal spiritual reflection, real-time engagement, and archival compliance using a decoupled **React** (Frontend) and **Django REST Framework** (Backend) architecture.

---

## ✨ Features Checklist

* **🔒 Auth & Contextual RBAC:** Secure token-based validation mapping to traditional Quaker roles (*Clerk, Recording Clerk, Member, Attender*).
* **⚡ Real-Time In-App Chat:** High-performance, concurrent group interaction built on top of asynchronous WebSockets.
* **🛡️ System-Wide Throttling:** Multi-tier rate limiting (*Anon, User, Burst scopes*) protecting standard REST endpoints and sockets from spam or DDoS attacks.
* **🎥 Unified Live Streams & Radio:** Native adaptive bitrate HLS (.m3u8) audio and video players built for hybrid worship or local continuous broadcasts.
* **📖 Reading & Song Accompaniment:** Live external Scripture query API integrations side-by-side with localized database-backed electronic hymnals.

---

## 🏗️ System Architecture

```
                  ┌────────────────────────────────────────┐
                  │          React Client (Vite)           │
                  └────┬─────────────────┬──────────────┬──┘
                       │                 │              │
                 HTTP Requests      WebSockets     Video Stream
              (Auth, RBAC, Radio)     (Chat)      (Live HLS/M3U8)
                       │                 │              │
                       ▼                 ▼              ▼
             ┌──────────────────┐ ┌────────────┐ ┌──────────────┐
             │   Django REST    │ │   Django   │ │ External CDN │
             │ Framework (HTTP) │ │  Channels  │ │  (AWS/Mux/  │
             └─────────┬────────┘ └─────┬──────┘ │  Owncast)   │
                       │                │        └──────────────┘
                       ▼                ▼
             ┌─────────────────────────────────┐
             │       PostgreSQL & Redis        │
             └─────────────────────────────────┘

```

---

## 🗂️ Project Repository Structure

```text
friends-portal/
├── backend/                   # Django REST Framework Root
│   ├── core/                  # Project configuration settings, routing pipelines
│   ├── meeting/               # Main application app logic
│   │   ├── consumers.py       # Asynchronous WebSockets chat routing logic
│   │   ├── models.py          # Relational Database Schemas (Query, Schedule, Song)
│   │   ├── permissions.py     # Custom RBAC rules matching Quaker Governance
│   │   └── views.py           # Controller actions and throttling implementations
│   └── manage.py
└── frontend/                  # React Frontend (Vite engine layout template)
    ├── src/
    │   ├── components/
    │   │   ├── BibleReader.jsx   # Client-side API Scripture engine parser
    │   │   ├── Chat.jsx          # Live reactive state-driven message center
    │   │   ├── SongBook.jsx      # Numeric Hymnal index matching custom files
    │   │   └── StreamPlayer.jsx  # Native HTML5 engine audio & video wrapper
    │   ├── App.jsx            # Layout coordinator
    │   └── main.jsx
    └── package.json

```

---

## 🛠️ Local Installation & Development Setup

### Prerequisite Environment Assets

Ensure you have **Python 3.11+**, **Node.js (v18+)**, and a running **Redis** server instance locally on your developer system.

### 1. Backend Service Configuration (Django)

Navigate to your backend workspace directory, activate a virtual execution environment, and initialize your system Dependencies:

```bash
cd backend
python -m venv venv
source venv/bin/activate  # On Windows use: venv\Scripts\activate

# Install Core Libraries
pip install -r requirements.txt

# Apply Database Schemas & Seed System
python manage.py migrate
python manage.py createsuperuser          # the system admin (Django admin at /admin/)
python manage.py platform add-admin --email someone@example.org   # a platform admin (in-app dashboard)

# Fire up Django Local Server Engine
python manage.py runserver 8000

```

> ⚠️ **Important Redis Requirement:** Ensure your local Redis instance is active on port `6379` to allow Django Channels to manage the WebSocket message cache memory backbone.

### 2. Frontend Interface Configuration (React)

Open a new system terminal window to initialize and launch your UI components:

```bash
cd frontend
npm create vite@latest . -- --template react
npm install
npm install react-player

# Spin up Vite local server process
npm run dev
```

Your UI should now be reachable locally at `http://localhost:5173`.

---

## 🔐 Quaker Role-Based Access Control (RBAC) Specification

The portal utilizes granular checking logic matching traditional Quaker organizational structures:

| Security Group | Permissions Profile | Portal Access Level |
| --- | --- | --- |
| **Clerk** | Global Admin / Full Database CRUD | Can adjust structural meeting models, ban users, update legal schedules |
| **Recording Clerk** | Write & Update on specific endpoints | Allowed to upload/save approved corporate Meeting Minutes & Hymnal items |
| **Member** | Authenticated Read & Write Socket channels | Accesses streaming content, reads internal archives, and posts live chat |
| **Attender** | Unauthenticated Read Only | Public access to worship notifications, open bible reader, public index items |

---

## 📡 API Reference Endpoint Cheat Sheet

All standard data transmission formats default to strict application JSON outputs:

* `POST /api/v1/auth/login/` - Obtain JWT access + refresh tokens (response includes the user's role).
* `POST /api/v1/auth/refresh/`, `POST /api/v1/auth/logout/`, `GET /api/v1/auth/me/`
* `GET /api/schedule/` - Fetches calendar events and connection links.
* `GET /api/query/active/` - Returns the designated active spiritual query of the current week.
* `GET /api/songs/?search={query}` - Returns a subset array of matching songs filtered by number or string terms.
* `WS /ws/chat/<room_name>/?token=<access_token>` - Real-time chat (Members and above).

---

## 📜 License & Community Contribution Standard

This project is open-source under the **MIT License**.

In alignment with the **Quaker Peace Testimony**, we ask that all contributors interact respectfully, maintain transparent codebase standards, and ensure that enhancements preserve digital accessibility for all seekers regardless of their tech literacy or visual/auditory needs.

*For issues, bug reports, or to register your local meetinghouse infrastructure, please create a structural pull request or draft a GitHub tracker ticket.*