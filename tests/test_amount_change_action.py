"""Database tests for the amount-change save step (apps/approvals/services.py: change_amount).

The rules are covered in test_approval_services.py. These check that old
approvals stop counting, the request goes back for approval (re-tiered if it
crossed 50k), and the change is audited.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Profile
from apps.approvals.logic import LEVEL_FINANCE as F, LEVEL_MANAGER as M
from apps.approvals.services import (
    MSG_AMOUNT_LOCKED,
    MSG_NOT_REQUESTER_EDIT,
    AmountChangeDenied,
    approval_summary,
    approve,
    change_amount,
    reject,
)
from apps.audit.models import AuditLog
from apps.payment_requests.models import PaymentRequest, Project
from apps.vendors.models import Vendor


def make_user(name, role):
    # No password: tests never log in, and hashing one per user is slow.
    user = User.objects.create_user(name)
    Profile.objects.create(user=user, role=role)
    return user


class ChangeAmountTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.alice = make_user('alice', Profile.EMPLOYEE)
        cls.carol = make_user('carol', Profile.MANAGER)
        cls.faith = make_user('faith', Profile.FINANCE)
        cls.vendor = Vendor.objects.create(name='Nairobi Cement Ltd', service='Cement')
        cls.project = Project.objects.create(name='Kilimani Apartments')

    def make_request(self, amount, status=PaymentRequest.PENDING_APPROVAL):
        return PaymentRequest.objects.create(
            vendor=self.vendor, project=self.project, requester=self.alice,
            amount=Decimal(amount), reason='Materials',
            invoice_number=f'INV-{PaymentRequest.objects.count() + 1}',
            payment_method=PaymentRequest.MPESA,
            date=datetime.date(2026, 10, 14), status=status,
        )

    # --- After approval: approvals stop counting, back to pending ---
    def test_change_after_approval_needs_reapproval(self):
        req = self.make_request('24500')
        approve(req, self.carol)
        change_amount(req, self.alice, '26000')

        req.refresh_from_db()
        self.assertEqual(req.status, PaymentRequest.PENDING_APPROVAL)
        self.assertEqual(req.amount, Decimal('26000'))
        self.assertEqual(approval_summary(req)['outstanding_levels'], [M])

    def test_old_approval_is_kept_but_superseded(self):
        req = self.make_request('24500')
        approve(req, self.carol)
        change_amount(req, self.alice, '26000')
        row = req.approvals.get()
        self.assertTrue(row.superseded)   # kept for the audit trail, not deleted

    def test_same_manager_can_approve_new_amount(self):
        # The one-active-approval-per-level constraint ignores superseded rows.
        req = self.make_request('24500')
        approve(req, self.carol)
        change_amount(req, self.alice, '26000')
        approve(req, self.carol)
        req.refresh_from_db()
        self.assertEqual(req.status, PaymentRequest.APPROVED)

    def test_even_a_decrease_needs_reapproval(self):
        req = self.make_request('24500')
        approve(req, self.carol)
        change_amount(req, self.alice, '20000')
        req.refresh_from_db()
        self.assertEqual(req.status, PaymentRequest.PENDING_APPROVAL)

    def test_change_after_approval_restarts_the_approval_wait(self):
        # The approvers are now being asked about a new amount, so the
        # queue's "waiting" time counts from the change.
        req = self.make_request('24500')
        req.submitted_at = timezone.now() - datetime.timedelta(days=3)
        req.save()
        approve(req, self.carol)
        before = timezone.now()
        change_amount(req, self.alice, '26000')
        req.refresh_from_db()
        self.assertGreaterEqual(req.submitted_at, before)

    def test_change_while_pending_restarts_the_approval_wait(self):
        req = self.make_request('24500')
        req.submitted_at = timezone.now() - datetime.timedelta(days=3)
        req.save()
        before = timezone.now()
        change_amount(req, self.alice, '26000')
        req.refresh_from_db()
        self.assertGreaterEqual(req.submitted_at, before)

    # --- Re-tiering across 50k ---
    def test_42000_raised_to_67000_now_needs_finance(self):
        req = self.make_request('42000')
        approve(req, self.carol)
        decision = change_amount(req, self.alice, '67000')
        req.refresh_from_db()

        self.assertTrue(decision.retiered)
        self.assertEqual(approval_summary(req), {
            'required_levels': [F, M], 'approved_levels': [], 'outstanding_levels': [F, M],
        })

    def test_148000_lowered_to_42000_needs_only_manager(self):
        req = self.make_request('148000')
        approve(req, self.carol)
        approve(req, self.faith)
        change_amount(req, self.alice, '42000')
        req.refresh_from_db()
        self.assertEqual(approval_summary(req)['outstanding_levels'], [M])
        self.assertEqual(req.approvals.filter(superseded=True).count(), 2)

    def test_change_while_partly_approved(self):
        req = self.make_request('148000')
        approve(req, self.carol)          # still pending, waiting on finance
        change_amount(req, self.alice, '150000')
        self.assertEqual(approval_summary(req)['outstanding_levels'], [F, M])

    # --- Audit ---
    def test_change_is_audited(self):
        req = self.make_request('42000')
        approve(req, self.carol)
        change_amount(req, self.alice, '67000')

        entry = req.audit_logs.get(action=AuditLog.AMOUNT_CHANGED)
        self.assertEqual(entry.actor, self.alice)
        self.assertEqual(entry.details, {
            'old_amount': '42000.00', 'new_amount': '67000',
            'superseded_levels': [M], 'retiered': True,
        })

    # --- Before approval / no change / locked ---
    def test_draft_amount_changes_freely(self):
        req = self.make_request('24500', status=PaymentRequest.DRAFT)
        change_amount(req, self.alice, '67000')
        req.refresh_from_db()
        self.assertEqual((req.status, req.amount), (PaymentRequest.DRAFT, Decimal('67000')))

    def test_draft_change_does_not_set_submitted_at(self):
        req = self.make_request('24500', status=PaymentRequest.DRAFT)
        change_amount(req, self.alice, '67000')
        req.refresh_from_db()
        self.assertIsNone(req.submitted_at)

    def test_same_amount_writes_nothing(self):
        req = self.make_request('24500')
        approve(req, self.carol)
        self.assertIsNone(change_amount(req, self.alice, '24500.00'))
        req.refresh_from_db()
        self.assertEqual(req.status, PaymentRequest.APPROVED)
        self.assertFalse(req.approvals.filter(superseded=True).exists())
        self.assertFalse(req.audit_logs.filter(action=AuditLog.AMOUNT_CHANGED).exists())

    def test_rejected_request_amount_is_locked(self):
        req = self.make_request('24500')
        reject(req, self.carol, 'Duplicate invoice')
        with self.assertRaises(AmountChangeDenied) as ctx:
            change_amount(req, self.alice, '20000')
        self.assertEqual(ctx.exception.reason, MSG_AMOUNT_LOCKED)
        req.refresh_from_db()
        self.assertEqual(req.amount, Decimal('24500'))

    def test_paid_request_amount_is_locked(self):
        req = self.make_request('24500', status=PaymentRequest.PAID)
        with self.assertRaises(AmountChangeDenied):
            change_amount(req, self.alice, '20000')
        self.assertFalse(req.audit_logs.exists())

    def test_approver_cannot_change_the_amount(self):
        # carol approved it; she still can't edit the number she approved.
        req = self.make_request('24500')
        approve(req, self.carol)
        with self.assertRaises(AmountChangeDenied) as ctx:
            change_amount(req, self.carol, '20000')
        self.assertEqual(ctx.exception.reason, MSG_NOT_REQUESTER_EDIT)
        req.refresh_from_db()
        self.assertEqual(req.amount, Decimal('24500'))
        self.assertEqual(req.status, PaymentRequest.APPROVED)
        self.assertFalse(req.approvals.filter(superseded=True).exists())
        self.assertFalse(req.audit_logs.filter(action=AuditLog.AMOUNT_CHANGED).exists())
