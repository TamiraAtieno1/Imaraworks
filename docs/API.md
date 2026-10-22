# API Contract (Part A)

Last updated: 2026-10-16. Matches PRs #2 (payment request endpoints), #4 (vendor and project lists) and the name/id PR [#19]

All endpoints return JSON. Everything except login and refresh needs the header `Authorization: Bearer <access token>`.

## Auth

### POST /api/auth/login/
```json
{"username": "carol", "password": "password123"}
```
200: `{"access": "...", "refresh": "..."}`
401: `{"detail": "No active account found with the given credentials"}`

### POST /api/auth/refresh/
```json
{"refresh": "..."}
```
200: `{"access": "..."}`
401: `{"detail": "Token is invalid or expired", "code": "token_not_valid"}`

Access tokens last about 5 minutes, so expect the 401, refresh, retry flow often.

### GET /api/auth/me/
```json
{"id": 1, "username": "carol", "first_name": "Carol", "last_name": "Wanjiku", "role": "MANAGER"}
```
`role` is one of `EMPLOYEE`, `MANAGER`, `FINANCE`, `ADMIN`.
`role` is `null` for a user with no profile (for example a superuser). Treat it as no access.

## Reference data

Any logged-in role. Plain arrays, no pagination.

### GET /api/vendors/
Active vendors only, sorted by name.
```json
[{"id": 1, "name": "Metro Hardware Ltd.", "service": "Construction materials"}]
```

### GET /api/projects/
Sorted by name.
```json
[{"id": 1, "name": "Westlands Project"}]
```

## Payment requests

| Method | Path | Purpose |
|---|---|---|
| GET | /api/payment-requests/ | List, newest first (plain array) |
| POST | /api/payment-requests/ | Create |
| GET | /api/payment-requests/<id>/ | One request |

### POST body
```json
{
  "vendor": 1,
  "project": 1,
  "amount": "24500.00",
  "reason": "Site materials",
  "invoice_number": "INV-1032",
  "payment_method": "MPESA",
  "date": "2026-10-14",
  "notes": ""
}
```
- `vendor` and `project` are ids from the reference endpoints.
- `amount` must be greater than 0.
- `reason` max 500 characters. `invoice_number` required, max 100 characters, trimmed.
- `payment_method` is `MPESA`, `BANK_TRANSFER` or `CASH`.
- `date` is the invoice date (`YYYY-MM-DD`). It is not when the request was created. Use `created_at` for that.
- Do **not** send `requester` or `status`. The server sets them (requester is the logged-in user, status is `DRAFT`). If sent, they are ignored.

### Response (201 on create, 200 on get)
```json
{
  "id": 1,
  "vendor": 1, "vendor_name": "Metro Hardware Ltd.",
  "project": 1, "project_name": "Westlands Project",
  "requester": 1, "requester_username": "alice",   "requester_name": "Alice Mwangi",
  "amount": "24500.00",
  "reason": "Site materials",
  "invoice_number": "INV-1032",
  "payment_method": "MPESA",
  "date": "2026-10-14",
  "notes": "",
  "status": "DRAFT",
  "created_at": "2026-10-14T01:00:00Z",
  "updated_at": "2026-10-14T01:00:00Z",
  "approval": {"required_levels": ["manager"], "approved_levels": [], "outstanding_levels": []}
}
```
- `requester_name` is the full name, or the username if the user has no name set.
- `approval` shows which approval levels are needed, done and still outstanding. See [APPROVALS_API.md](APPROVALS_API.md#the-approval-block).
- `amount` is a string, not a number. Format it for display and don't do arithmetic on it directly.
- Timestamps are UTC.
- `status` values: `DRAFT`, `SUBMITTED`, `PENDING_APPROVAL`, `APPROVED`, `REJECTED`, `PROCESSING`, `PAID`, `FAILED`.

### Who sees what
| Role | List and view | Create |
|---|---|---|
| EMPLOYEE | Own requests only | Yes |
| MANAGER | All requests | Yes |
| FINANCE | All requests | Yes |
| ADMIN | None (deliberate: admins manage users, not payments) | No (403) |

## Payments

### POST /api/payment-requests/<id>/pay/

FINANCE only. Pays an `APPROVED` request, or retries a `FAILED` one, through the mock provider. The amount charged is always the request's approved amount.

Body (all optional):
```json
{"idempotency_key": "a uuid", "currency": "KES", "gateway": "mock", "description": "..."}
```
- Send the same `idempotency_key` when retrying after a network error: you get the first attempt back (`"replayed": true`), not a second charge. One is generated if you leave it out.
- A `description` containing "FAIL" makes the mock provider decline (for demos).

Response (201 new attempt, 200 replay):
```json
{
  "id": 7, "request": 1, "status": "SUCCESS", "amount": "24500.00", "currency": "KES",
  "provider_reference": "MOCK-...", "provider_reason": "", "replayed": false,
  "created_at": "2026-10-20T08:00:00Z", "completed_at": "2026-10-20T08:00:01Z"
}
```
`status` is `SUCCESS` (request becomes `PAID`) or `FAILED` (request becomes `FAILED` and can be retried).

| Status | When |
|---|---|
| 403 | Not FINANCE, or the request is your own (`"You cannot pay a request you created."`) |
| 404 | No such request |
| 409 | Not payable (not `APPROVED`/`FAILED`), already paid, or a payment is in progress |
| 422 | `amount` was sent (it is never accepted), or unknown `gateway` |
| 502 | The provider errored; the attempt is recorded `FAILED` |

### GET /api/payments/transactions/

FINANCE only. Every payment attempt, newest first (plain array).

## Errors

| Status | Body | When |
|---|---|---|
| 400 | `{"amount": ["Amount must be greater than zero."]}` | Field validation. Keys are field names, values are lists of messages |
| 400 | `{"non_field_errors": ["The fields vendor, invoice_number must make a unique set."]}` | Same vendor and invoice number already exists |
| 401 | `{"detail": "Authentication credentials were not provided."}` | Missing or expired token |
| 403 | `{"detail": "Your role cannot create payment requests."}` | Role not allowed to create |
| 404 | `{"detail": "..."}` | Missing id, or another employee's request (same response for both) |

DRF generates the `detail` wording, so check one real response before relying on exact text. The shapes are reliable.

## Reports

### GET /api/reports/summary/

Totals over the requests **you can see**, using the same rule as the request list ("Who sees what" above). Managers and finance get totals for everything; an employee gets totals for their own requests only. ADMIN gets 403 (`"Your role cannot view reports."`), for the same reason they see no requests. Read-only: other methods return 405.

```json
{
  "overall":          {"count": 4, "total": "281500.00"},
  "pending_approval": {"count": 2, "total": "215000.00"},
  "paid":             {"count": 1, "total": "24500.00"},
  "by_status": [
    {"status": "DRAFT", "label": "Draft", "count": 0, "total": "0.00"},
    {"status": "PENDING_APPROVAL", "label": "Pending Approval", "count": 2, "total": "215000.00"}
  ],
  "by_project": [{"id": 3, "name": "Kisumu Project", "count": 2, "total": "190000.00"}],
  "by_vendor":  [{"id": 2, "name": "Prime Cement Supplies", "count": 2, "total": "215000.00"}]
}
```

- `by_status` always lists **all 8 statuses** in lifecycle order (DRAFT … FAILED), with zeros for unused ones, so a dashboard always has the same rows. (Shortened above.)
- `by_project` and `by_vendor` list only those with at least one request, biggest total first.
- Amounts are 2dp **strings**, as everywhere else in this API, so nothing is rounded by a float.
- `total` adds up `amount` across requests in any status. Use `paid` for money actually paid out.

Optional filter: `?status=PAID` (any status value above, uppercase) narrows the requests **before** every total, so `by_project` and `by_vendor` show only paid money. The filter applies inside what you can already see, so an employee still gets only their own requests. With a filter, the `pending_approval` and `paid` blocks follow it too (e.g. `pending_approval` is zero under `?status=PAID`). An unknown value gives 400: `{"status": ["\"BOGUS\" is not a valid choice."]}`.

Optional date range: `?date_from=2026-10-01&date_to=2026-10-31` (YYYY-MM-DD, either one alone is fine) keeps requests whose `date` is in the range, **including both end days**, so that example is all of October. It combines with `?status=`. A badly formatted date gives 400 on that field, and `date_from` after `date_to` gives 400: `{"date_to": ["Must be on or after date_from."]}`.

Optional payment-date range: `?paid_from=2026-10-01&paid_to=2026-10-31` keeps requests whose **successful payment completed** in the range, so that example is "money paid out in October", whenever the request was raised. Use this, not `date_from`/`date_to`, for paid totals. Days are Kenyan days (EAT, UTC+3): a payment at 01:00 on 1 November in Nairobi counts in November. Failed payment attempts never count, so only paid requests can match, and each request is counted once. It combines with `?status=` and `?date_from=`/`?date_to=`, with the same 400s (`{"paid_to": ["Must be on or after paid_from."]}`).

## Not built yet

Submit, approve and reject; payment processing; audit trail; search filters on the list. Don't call these. Their contracts will be added here by the people who build them.

## Test users

Password for all: `password123`.
alice and brian (EMPLOYEE), carol and david (MANAGER), faith (FINANCE), grace (ADMIN).