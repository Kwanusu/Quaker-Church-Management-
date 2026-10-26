# 🕊️ Friends Portal - Backend Development Setup Guide

## Project Overview

The **Friends Portal** backend is a Django REST Framework application designed specifically for Quaker faith communities. It supports hybrid worship (in-person and online), real-time chat, hymnal management, and role-based access control aligned with traditional Quaker governance structures.

---

## Quick Start

### 1. Prerequisites

- **Python 3.10+**
- **Redis 6.0+** (for WebSocket channel layer and caching)
- **PostgreSQL 13+** (recommended for production)

### 2. Environment Setup

```bash
# Clone repository
git clone <repo-url>
cd backend/quaker_church_mangement_system

# Create virtual environment
python -m venv venv

# Activate virtual environment
# On Windows:
.\venv\Scripts\activate
# On macOS/Linux:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Create database tables
python manage.py migrate

# Create superuser for admin panel
python manage.py createsuperuser

# Run development server (with ASGI)
daphne -b 0.0.0.0 -p 8000 quaker_church_mangement_system.asgi:application
```

---

## Project Structure

```
quaker_church_mangement_system/
├── meeting/                          # Core meeting application
│   ├── models.py                    # Domain models (Song, Query, Schedule)
│   ├── views.py                     # DRF ViewSets with API endpoints
│   ├── permissions.py               # RBAC permission classes
│   ├── serializers.py               # DRF serializers
│   ├── consumers.py                 # WebSocket chat consumer
│   ├── routing.py                   # WebSocket URL routing
│   └── admin.py                     # Django admin configuration
│
├── core/                            # Project configuration
│   ├── settings.py                  # Django settings (DRF, Channels, CORS)
│   ├── asgi.py                      # ASGI config (Channels + HTTP)
│   ├── wsgi.py                      # WSGI config (fallback)
│   └── urls.py                      # Project URL routing
│
├── requirements.txt                  # Python dependencies
├── manage.py                        # Django management script
└── .gitignore                       # Git ignore rules
```

---

## Domain Models

### Song (Hymnal)
Represents hymns and spiritual songs in the meetinghouse hymnal.
- **Fields**: `number`, `title`, `lyrics`, `author`, `song_type`
- **Types**: Hymn, Spiritual Poem, Scripture Reading
- **Access**: Public read, Recording Clerk write

### Query (Weekly Spiritual Question)
The centerpiece of Quaker corporate meditation.
- **Fields**: `title`, `text`, `week_starting`, `is_active`, `created_by`
- **Behavior**: Only one active query at a time (enforced via `save()` method)
- **Access**: Public read, Clerk write

### Schedule (Worship Events)
Manages worship gatherings and special meetings.
- **Fields**: `title`, `event_type`, `start_time`, `end_time`, `stream_url`, `is_hybrid`, `is_published`
- **Event Types**: Sunday Worship, Special Meeting, Adult Education, Committee Meeting
- **Access**: Public read (published only), Clerk write (all)

---

## API Endpoints

### Authentication
```
POST   /api/v1/auth/login/           # Obtain JWT access token
POST   /api/v1/auth/refresh/         # Refresh access token
```

### Songs (Hymnal)
```
GET    /api/v1/songs/                 # List all songs (searchable, filterable)
GET    /api/v1/songs/:id/             # Retrieve single song
POST   /api/v1/songs/                 # Create song (Recording Clerk+)
PATCH  /api/v1/songs/:id/             # Update song (Recording Clerk+)
DELETE /api/v1/songs/:id/             # Delete song (Recording Clerk+)
```

**Query Parameters**:
- `search=string`: Search title or author
- `type=hymn|poem|reading`: Filter by type
- `number_min=N`: Minimum song number
- `number_max=N`: Maximum song number

### Queries (Spiritual Questions)
```
GET    /api/v1/queries/               # List all queries
GET    /api/v1/queries/active/        # Get currently active query
GET    /api/v1/queries/:id/           # Retrieve single query
POST   /api/v1/queries/               # Create query (Clerk only)
PATCH  /api/v1/queries/:id/           # Update query (Clerk only)
DELETE /api/v1/queries/:id/           # Delete query (Clerk only)
```

**Query Parameters**:
- `active=true|false`: Filter by active status

### Schedules (Worship Events)
```
GET    /api/v1/schedules/             # List published schedules
GET    /api/v1/schedules/upcoming/    # Next 10 upcoming events
GET    /api/v1/schedules/:id/         # Retrieve single schedule
POST   /api/v1/schedules/             # Create schedule (Clerk only)
PATCH  /api/v1/schedules/:id/         # Update schedule (Clerk only)
DELETE /api/v1/schedules/:id/         # Delete schedule (Clerk only)
```

**Query Parameters**:
- `type=worship|special|adult_ed|committee`: Filter by event type
- `start_date=YYYY-MM-DD`: Filter from date
- `end_date=YYYY-MM-DD`: Filter to date

---

## WebSocket Chat

### Connection
```
WS /ws/chat/<room_name>/?token=<access_token>
```

**Auth**:
- Browsers can't set headers on WebSockets, so pass the JWT access token as `?token=`
- User must have `Member` role or higher (close code 4401 = not logged in, 4403 = not a member)
- Origin must match `DJANGO_ALLOWED_HOSTS`
- Max 2000 chars/message, 30 messages/minute per connection

### Message Format

**Sending**:
```json
{
    "message": "Your message text here"
}
```

**Receiving**:
```json
{
    "username": "john_smith",
    "full_name": "John Smith",
    "message": "Your message text here",
    "event_type": "message",
    "timestamp": "2026-06-17T14:30:00.000000"
}
```

**Events**:
- `message`: Regular chat message
- `user_joined`: User joined the meeting
- `user_left`: User left the meeting

---

## Role-Based Access Control (RBAC)

| Role | Permissions | API Access |
|------|------------|-----------|
| **Clerk** | Global admin, full CRUD | All endpoints |
| **Recording Clerk** | Write/update specific endpoints, create minutes | Songs, Schedules (write); Queries (read-only) |
| **Member** | Read all, chat, archive access | All read endpoints, WebSocket |
| **Attender** | Public read-only | Songs, Queries, published Schedules (read-only) |

---

## Rate Limiting

- **Anonymous users**: 100 requests/hour
- **Authenticated users**: 1000 requests/hour
- **Burst (login endpoint)**: 20 requests/hour

---

## Environment Variables

Create a `.env` file in the project root:

```env
DEBUG=True
SECRET_KEY=your-django-secret-key-here
DATABASE_URL=postgresql://user:password@localhost/quaker_portal
REDIS_URL=redis://localhost:6379/0
ALLOWED_HOSTS=localhost,127.0.0.1
```

---

## Database Configuration

### SQLite (Development Default)
```python
# settings.py
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}
```

### PostgreSQL (Production)
```python
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'quaker_portal',
        'USER': 'postgres',
        'PASSWORD': 'password',
        'HOST': 'localhost',
        'PORT': '5432',
    }
}
```

---

## Redis Configuration

Django Channels uses Redis for channel layers:

```python
# settings.py
CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels_redis.core.RedisChannelLayer',
        'CONFIG': {
            'hosts': [('127.0.0.1', 6379)],
        },
    },
}
```

**To start Redis locally**:
```bash
redis-server
```

---

## Running Tests

```bash
# Run all tests
python manage.py test

# Run tests for specific app
python manage.py test meeting

# Run with coverage
pip install coverage
coverage run --source='.' manage.py test
coverage report
```

---

## Common Commands

```bash
# Create database migrations
python manage.py makemigrations

# Apply migrations
python manage.py migrate

# Create superuser
python manage.py createsuperuser

# Load fixtures (seed data)
python manage.py loaddata fixtures/initial_data.json

# Django shell
python manage.py shell

# Run development server
python manage.py runserver 8000

# Run ASGI server (with WebSocket support)
daphne -b 0.0.0.0 -p 8000 quaker_church_mangement_system.asgi:application
```

---

## Git Commit Convention

Professional commit messages follow this format:

```
<type>(<scope>): <subject>

<body>

<footer>
```

**Types**:
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation
- `style`: Code style changes (no logic)
- `refactor`: Code refactoring
- `test`: Test additions/changes
- `chore`: Build, dependency updates
- `perf`: Performance improvements

**Examples**:
```bash
git commit -m "feat(meeting): add sermon upload to schedules"
git commit -m "fix(permissions): correct member role check in chat"
git commit -m "docs: update API endpoint documentation"
```

---

## Troubleshooting

### `ModuleNotFoundError: No module named 'django'`
Solution: Ensure virtual environment is activated and requirements are installed:
```bash
.\venv\Scripts\activate
pip install -r requirements.txt
```

### Redis connection error
Solution: Ensure Redis is running:
```bash
redis-server
# Or on macOS with Homebrew:
brew services start redis
```

### Database locked (SQLite)
Solution: Close other database connections and delete `db.sqlite3-journal`:
```bash
rm db.sqlite3-journal
python manage.py migrate
```

---

## Resources

- [Django Documentation](https://docs.djangoproject.com/)
- [Django REST Framework](https://www.django-rest-framework.org/)
- [Django Channels](https://channels.readthedocs.io/)
- [Quaker History & Faith](https://www.quaker.org.uk/)

---

## Contributing

1. Create a feature branch: `git checkout -b feature/your-feature`
2. Make commits with professional messages
3. Push and create a Pull Request
4. Ensure tests pass and code follows project conventions

**Code Style**: PEP 8 compliant with 4-space indentation

---

**Last Updated**: June 17, 2026
