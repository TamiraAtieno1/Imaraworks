# ImaraWorks Vendor Payment & Approval System

A Django REST API for submitting, approving and tracking vendor payment requests.
KamiLimu ICT Track case study.

## Setup

```
python -m venv venv
venv\Scripts\Activate.ps1        # Windows PowerShell
pip install -r requirements.txt
$env:DJANGO_DEBUG="1"            # once per terminal; see "Settings" below
python manage.py migrate
python manage.py seed_users
python manage.py runserver
```

### Settings

Deployment settings come from environment variables, never from the code
(issue #21). Without `DJANGO_DEBUG=1` the app runs in production mode and
refuses to start unless a secret key is set.

| Variable | Local development | Deployed demo |
|---|---|---|
| `DJANGO_DEBUG` | `1` | leave unset (DEBUG off) |
| `DJANGO_SECRET_KEY` | not needed | **required**: a new random key, kept out of git |
| `DJANGO_ALLOWED_HOSTS` | not needed (defaults to `localhost,127.0.0.1`) | the demo's domain, comma-separated |

Generate a key with
`python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"`.
Tests (`python manage.py test`) need none of these.

## Test accounts (local password: `password123`)

`password123` is for local development only. With DEBUG off, `seed_users`
refuses to run unless `SEED_USER_PASSWORD` is set, so the deployed demo's
accounts get a password that is not in this repo (issue #21).

| Username | Role |
|---|---|
| alice, brian | Employee |
| carol, david | Manager |
| faith | Finance Officer |
| grace | Administrator |

## Project structure

- `apps/accounts` - roles, login, `/api/auth/me/`
- `apps/vendors` - vendors
- `apps/payment_requests` - payment requests, projects, validation
- `apps/approvals`, `apps/payments`, `apps/audit`, `apps/reporting` - in progress
- `frontend` - templates and static files
- `tests` - automated tests

## Running tests

```
python manage.py test
```

## Team

| Area | Owner |
|---|---|
| Accounts, vendors, payment requests | A |
| Approvals | B |
| Payments | C |
| Frontend | D |