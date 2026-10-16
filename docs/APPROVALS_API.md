# API Contract (Part B: approvals and audit)

Status: **built** (PR "Part B: approvals and audit endpoints"). Last updated: 2026-10-15.

Same conventions as [API.md](API.md): JSON everywhere, `Authorization: Bearer <access token>` on every call, errors as `{"detail": "..."}`.

## Approval rules (enforced by the server)

- **≤ KES 50,000** needs one `manager` approval. **> KES 50,000** needs `manager` **and** `finance`, in either order.
- A new request starts as `DRAFT`. Its requester submits it, which moves it straight to `PENDING_APPROVAL`; no request is left showing `SUBMITTED`.
- Only requests in `PENDING_APPROVAL` can be approved or rejected.
- **Nobody can approve or reject a request they created.**
- One rejection at any level ends the request (`REJECTED`). A reason is required.
- If the amount changes after approval, earlier approvals stop counting and the request goes back to `PENDING_APPROVAL`. The levels are recalculated from the new amount.

Levels are lowercase: `manager`, `finance`. A MANAGER user approves as `manager`, a FINANCE user as `finance`. EMPLOYEE and ADMIN users can't approve.

## The `approval` block

The queue, the submit response and the approve/reject responses include this block, so pages can show "Waiting on Finance" and similar:

```json
"approval": {
  "required_levels": ["finance", "manager"],
  "approved_levels": ["manager"],
  "outstanding_levels": ["finance"]
}
```
- `outstanding_levels` is empty once the request is fully approved.

## Endpoints

| Method | Path | Who | Purpose |
|---|---|---|---|
| POST | /api/payment-requests/<id>/submit/ | The request's requester (any role) | Send a draft for approval |
| GET | /api/approvals/queue/ | MANAGER, FINANCE | Requests waiting on **my** decision |
| POST | /api/payment-requests/<id>/approve/ | MANAGER, FINANCE | Approve at my level |
| POST | /api/payment-requests/<id>/reject/ | MANAGER, FINANCE | Reject with a reason |
| GET | /api/payment-requests/<id>/history/ | Anyone who can see the request | Audit trail of one request |
| GET | /api/audit/ | ADMIN | Whole audit log |

### POST /api/payment-requests/<id>/submit/
No body. Only the person who created the request can submit it, whatever their role (managers and finance raise requests too). `DRAFT` goes straight to `PENDING_APPROVAL`, and a `SUBMITTED` entry is added to the request's history.

200: the full request, with the same fields as `GET /api/payment-requests/<id>/`, plus `approval`:
```json
{
  "id": 7, "vendor_name": "SwiftHaul Logistics", "requester_username": "alice",
  "amount": "67000.00", "status": "PENDING_APPROVAL", "created_at": "...",
  "approval": {"required_levels": ["finance", "manager"], "approved_levels": [], "outstanding_levels": ["finance", "manager"]}
}
```

Errors (checked in this order):

| Status | `detail` | When |
|---|---|---|
| 404 | `Not found.` | No such request, or you can't see it (another employee's request, or any ADMIN) |
| 403 | `Only the person who created this request can submit it.` | You can see it but didn't create it (e.g. a manager) |
| 409 | `This request has already been submitted.` | Status isn't `DRAFT` (or a leftover `SUBMITTED`) |

### GET /api/approvals/queue/
These are the requests the current user can act on right now. The request is `PENDING_APPROVAL`, needs the user's level, hasn't been approved at that level yet, and wasn't created by the user. The list is oldest first, as a plain array.

Each item has the same fields as `GET /api/payment-requests/<id>/`, plus `approval`:
```json
[{
  "id": 7, "vendor_name": "SwiftHaul Logistics", "requester_username": "alice",
  "amount": "67000.00", "status": "PENDING_APPROVAL", "created_at": "...",
  "approval": {"required_levels": ["finance", "manager"], "approved_levels": [], "outstanding_levels": ["finance", "manager"]}
}]
```
EMPLOYEE and ADMIN users get `403`.

### POST /api/payment-requests/<id>/approve/
No body.

200:
```json
{
  "id": 7,
  "status": "PENDING_APPROVAL",
  "approval": {"required_levels": ["finance", "manager"], "approved_levels": ["manager"], "outstanding_levels": ["finance"]}
}
```
- `status` becomes `APPROVED` once `outstanding_levels` is empty. Until then it stays `PENDING_APPROVAL`.

### POST /api/payment-requests/<id>/reject/
```json
{"reason": "Invoice does not match the delivery note"}
```
200:
```json
{
  "id": 7,
  "status": "REJECTED",
  "reason": "Invoice does not match the delivery note",
  "approval": {"required_levels": ["manager"], "approved_levels": [], "outstanding_levels": ["manager"]}
}
```
- Leading and trailing spaces are trimmed. A blank reason gives `400`.

### Approve / reject errors

Checks run in this order, and the first one that fails is returned:

| Status | `detail` | When |
|---|---|---|
| 404 | `Not found.` | No such request, or you can't see it (this includes every ADMIN, since admins see no requests) |
| 403 | `Your role cannot approve payments.` | EMPLOYEE, on their own request |
| 409 | `This request is not awaiting approval.` | Status isn't `PENDING_APPROVAL` |
| 403 | `You cannot approve a request you created.` | Self-approval (applies to reject too) |
| 403 | `Your approval is not required for this request.` | e.g. FINANCE on a request ≤ 50k |
| 409 | `This level has already approved.` | Your level has already approved |
| 400 | `A reason is required to reject a request.` | Reject only, blank reason |

`403` means "not you", `409` means "not now" (the request's state changed, so reload it), and `400` means "fix your input".

### GET /api/payment-requests/<id>/history/
You can see a request's history exactly when you can see the request (see "Who sees what" in API.md). Anyone else gets `404`. The list is oldest first.
```json
[
  {"id": 1, "action": "CREATED", "actor_username": "alice", "details": {}, "timestamp": "..."},
  {"id": 4, "action": "APPROVED", "actor_username": "carol",
   "details": {"level": "manager", "amount": "67000", "fully_approved": false}, "timestamp": "..."}
]
```
- `action` is one of `CREATED`, `SUBMITTED`, `APPROVED`, `REJECTED`, `AMOUNT_CHANGED`, `PAYMENT_STARTED`, `PAID`, `PAYMENT_FAILED`.
- `details` depends on the action. `SUBMITTED` has `amount` and `required_levels`, `REJECTED` has `reason`, and `AMOUNT_CHANGED` has `old_amount`, `new_amount` and `retiered`.

### GET /api/audit/
ADMIN only, and read-only. Admins can't see payment requests or payment data (see API.md), so they see who did what and when, but not `details` (amounts, vendors, rejection reasons). Every other role gets `403`. The list is newest first.
```json
[
  {"id": 4, "action": "APPROVED", "actor_username": "carol", "timestamp": "...", "request": 12}
]
```

Optional filter: `?action=REJECTED`.

## Not covered here
- Editing a request's amount: the endpoint doesn't exist yet. Part B will add it (agreed on PR #5).
- Payment processing (Part C).
