"""Database tests for the submit save step (apps/approvals/services.py: submit).

The rules themselves are covered in test_submit_decision.py. These check the
saving: the status and audit entry are written when allowed, and NOTHING is
written when refused.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Profile
from apps.approvals.logic import LEVEL_FINANCE as F, LEVEL_MANAGER as M
from apps.approvals.services import (
    MSG_ALREADY_SUBMITTED,
    MSG_NOT_REQUESTER,
    SubmitDenied,
    approval_queue,
    submit,
)
from apps.audit.models import AuditLog
from apps.payment_requests.models import PaymentRequest, Project
from apps.vendors.models import Vendor


def make_user(name, role):
    # No password: tests never log in, and hashing one per user is slow.
    user = User.objects.create_user(name)
    Profile.objects.create(user=user, role=role)
    return user


class SubmitActionTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.alice = make_user('alice', Profile.EMPLOYEE)
        cls.brian = make_user('brian', Profile.EMPLOYEE)
        cls.carol = make_user('carol', Profile.MANAGER)
        cls.david = make_user('david', Profile.MANAGER)
        cls.vendor = Vendor.objects.create(name='Nairobi Cement Ltd', service='Cement')
        cls.project = Project.objects.create(name='Kilimani Apartments')

    def make_request(self, amount, requester=None, status=PaymentRequest.DRAFT):
        return PaymentRequest.objects.create(
            vendor=self.vendor, project=self.project,
            requester=requester or self.alice,
            amount=Decimal(amount), reason='Materials',
            invoice_number=f'INV-{PaymentRequest.objects.count() + 1}',
            payment_method=PaymentRequest.MPESA,
            date=datetime.date(2026, 10, 15), status=status,
        )

    def assert_denied(self, req, user, reason):
        with self.assertRaises(SubmitDenied) as ctx:
            submit(req, user)
        self.assertEqual(ctx.exception.reason, reason)

    def assert_nothing_written(self, req, status=PaymentRequest.DRAFT):
        req.refresh_from_db()
        self.assertEqual(req.status, status)
        self.assertIsNone(req.submitted_at)
        self.assertFalse(req.audit_logs.exists())

    # --- allowed ---

    def test_requester_submits_draft_and_it_is_pending(self):
        req = self.make_request('24500')
        submit(req, self.alice)
        req.refresh_from_db()
        self.assertEqual(req.status, PaymentRequest.PENDING_APPROVAL)

    def test_submit_writes_one_audit_entry(self):
        req = self.make_request('148000')
        submit(req, self.alice)
        entry = req.audit_logs.get()
        self.assertEqual(entry.action, AuditLog.SUBMITTED)
        self.assertEqual(entry.actor, self.alice)
        self.assertEqual(entry.details, {
            'amount': '148000.00', 'required_levels': sorted([M, F]),
        })

    def test_manager_can_submit_their_own_request(self):
        # Role gates approval, not creating or submitting.
        req = self.make_request('24500', requester=self.carol)
        submit(req, self.carol)
        req.refresh_from_db()
        self.assertEqual(req.status, PaymentRequest.PENDING_APPROVAL)

    def test_submitted_request_reaches_the_approval_queue(self):
        req = self.make_request('24500')
        self.assertNotIn(req, approval_queue(self.carol))
        submit(req, self.alice)
        self.assertIn(req, approval_queue(self.carol))

    def test_submit_records_when_the_approval_wait_starts(self):
        req = self.make_request('24500')
        self.assertIsNone(req.submitted_at)
        before = timezone.now()
        submit(req, self.alice)
        req.refresh_from_db()
        self.assertGreaterEqual(req.submitted_at, before)
        self.assertGreater(req.submitted_at, req.created_at)

    # --- refused: nothing written ---

    def test_someone_else_cannot_submit(self):
        req = self.make_request('24500')
        self.assert_denied(req, self.brian, MSG_NOT_REQUESTER)
        self.assert_nothing_written(req)

    def test_manager_cannot_submit_an_employees_request(self):
        req = self.make_request('24500')
        self.assert_denied(req, self.carol, MSG_NOT_REQUESTER)
        self.assert_nothing_written(req)

    def test_second_submit_is_refused_and_not_audited_twice(self):
        req = self.make_request('24500')
        submit(req, self.alice)
        self.assert_denied(req, self.alice, MSG_ALREADY_SUBMITTED)
        self.assertEqual(req.audit_logs.count(), 1)

    def test_second_submit_leaves_submitted_at_alone(self):
        req = self.make_request('24500')
        submit(req, self.alice)
        req.refresh_from_db()
        first = req.submitted_at
        self.assert_denied(req, self.alice, MSG_ALREADY_SUBMITTED)
        req.refresh_from_db()
        self.assertEqual(req.submitted_at, first)

    def test_rejected_request_cannot_be_resubmitted(self):
        req = self.make_request('24500', status=PaymentRequest.REJECTED)
        self.assert_denied(req, self.alice, MSG_ALREADY_SUBMITTED)
        self.assert_nothing_written(req, status=PaymentRequest.REJECTED)

    def test_stale_object_is_reread_before_deciding(self):
        # `req` still says DRAFT in memory, but the row is already pending.
        # submit() re-reads under the lock, so it refuses.
        req = self.make_request('24500')
        PaymentRequest.objects.filter(pk=req.pk).update(status=PaymentRequest.PENDING_APPROVAL)
        self.assertEqual(req.status, PaymentRequest.DRAFT)
        self.assert_denied(req, self.alice, MSG_ALREADY_SUBMITTED)
        self.assertFalse(req.audit_logs.exists())
