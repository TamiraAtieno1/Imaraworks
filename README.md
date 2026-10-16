# ImaraWorks Vendor Payment & Approval System

A Django REST API for submitting, approving and tracking vendor payment requests.
KamiLimu ICT Track case study.

## Setup

```
python -m venv venv
venv\Scripts\Activate.ps1        # Windows PowerShell
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_users
python manage.py runserver
```

## Test accounts (password: `password123`)

| Username | Role |
|---|---|
| alice, brian | Employee |
| carol, david | Manager |
| faith | Finance Officer |
| grace | Administrator |

## Known limitations

- **A request over KES 50,000 raised by faith can never be fully approved.** Requests over KES 50,000 need a manager and a finance approval, and nobody may approve their own request. faith is the only Finance user, so nobody can give the finance approval and the request stays in `PENDING_APPROVAL`. faith's requests of KES 50,000 or less are approved as normal by carol or david. This is the separation of duties working as intended: a real finance team has more than one person, and the demo data seeds only one. To see a large request go through, raise it as another user. (Decided in #15.)

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