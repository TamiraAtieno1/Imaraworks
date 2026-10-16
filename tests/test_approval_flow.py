"""End-to-end tests: the case-study numbers through the whole Part B flow.

The other test files check one piece each (a rule, a save step, an endpoint).
These follow one request from the approver's queue to its final status and its
audit trail, the way people will use it, so they catch pieces that work alone
but are wired together wrongly.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from rest_framework.test import APITestCase

from apps.accounts.models import Profile
from apps.approvals.logic import LEVEL_FINANCE as F, LEVEL_MANAGER as M
from apps.audit.models import AuditLog
from apps.payment_requests.models import PaymentRequest, Project
from apps.vendors.models import Vendor


def make_user(name, role):
    # No password: tests log in with force_authenticate, and hashing is slow.
    user = User.objects.create_user(name)
    Profile.objects.create(user=user, role=role)
    return user


class ApprovalFlowTests(APITestCase):

    @classmethod
    def setUpTestData(cls):
        cls.alice = make_user('alice', Profile.EMPLOYEE)
        cls.carol = make_user('carol', Profile.MANAGER)
        cls.david = make_user('david', Profile.MANAGER)
        cls.faith = make_user('faith', Profile.FINANCE)
        cls.vendor = Vendor.objects.create(name='Nairobi Cement Ltd', service='Cement')
        cls.project = Project.objects.create(name='Kilimani Apartments')

    def make_request(self, amount, requester=None):
        return PaymentRequest.objects.create(
            vendor=self.vendor, project=self.project,
            requester=requester or self.alice,
            amount=Decimal(amount), reason='Materials',
            invoice_number=f'INV-{PaymentRequest.objects.count() + 1}',
            payment_method=PaymentRequest.MPESA,
            date=datetime.date(2026, 10, 15),
            status=PaymentRequest.PENDING_APPROVAL,
        )

    # --- helpers: each one is a single HTTP call, as the pages would make it ---

    def queue_ids(self, user):
        self.client.force_authenticate(user)
        return [item['id'] for item in self.client.get('/api/approvals/queue/').data]

    def approve(self, user, req):
        self.client.force_authenticate(user)
        return self.client.post(f'/api/payment-requests/{req.pk}/approve/')

    def reject(self, user, req, reason):
        self.client.force_authenticate(user)
        return self.client.post(f'/api/payment-requests/{req.pk}/reject/', {'reason': reason}, format='json')

    def change_amount(self, user, req, amount):
        self.client.force_authenticate(user)
        resp = self.client.patch(f'/api/payment-requests/{req.pk}/amount/', {'amount': amount}, format='json')
        self.assertEqual(resp.status_code, 200)
        return resp

    def history(self, user, req):
        self.client.force_authenticate(user)
        return self.client.get(f'/api/payment-requests/{req.pk}/history/').data

    def actions(self, req):
        # Read as a manager: managers see every request, the requester only their own.
        return [(e['action'], e['actor_username']) for e in self.history(self.david, req)]

    # --- the case-study scenarios ---

    def test_24500_needs_one_manager(self):
        req = self.make_request('24500')
        self.assertIn(req.pk, self.queue_ids(self.carol))
        self.assertNotIn(req.pk, self.queue_ids(self.faith))     # <= 50k: finance not needed

        resp = self.approve(self.carol, req)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['status'], PaymentRequest.APPROVED)
        self.assertNotIn(req.pk, self.queue_ids(self.carol))
        self.assertNotIn(req.pk, self.queue_ids(self.david))     # one manager is enough
        self.assertEqual(self.actions(req), [(AuditLog.APPROVED, 'carol')])

    def test_148000_needs_manager_then_finance(self):
        req = self.make_request('148000')
        self.assertIn(req.pk, self.queue_ids(self.carol))
        self.assertIn(req.pk, self.queue_ids(self.faith))

        resp = self.approve(self.carol, req)
        self.assertEqual(resp.data['status'], PaymentRequest.PENDING_APPROVAL)
        self.assertEqual(resp.data['approval']['outstanding_levels'], [F])
        self.assertNotIn(req.pk, self.queue_ids(self.david))     # manager level is done

        resp = self.approve(self.faith, req)
        self.assertEqual(resp.data['status'], PaymentRequest.APPROVED)
        self.assertNotIn(req.pk, self.queue_ids(self.faith))
        self.assertEqual(self.actions(req), [(AuditLog.APPROVED, 'carol'), (AuditLog.APPROVED, 'faith')])

    def test_finance_can_approve_before_manager(self):
        req = self.make_request('148000')
        self.approve(self.faith, req)
        resp = self.approve(self.carol, req)
        self.assertEqual(resp.data['status'], PaymentRequest.APPROVED)

    def test_67000_cut_to_42000_needs_only_a_fresh_manager_approval(self):
        req = self.make_request('67000')
        self.approve(self.carol, req)                             # manager done, finance outstanding

        self.change_amount(self.alice, req, '42000')

        # Carol approved 67,000, not 42,000, so her approval no longer counts,
        # and at 42,000 finance is no longer needed.
        self.assertIn(req.pk, self.queue_ids(self.carol))
        self.assertNotIn(req.pk, self.queue_ids(self.faith))
        resp = self.approve(self.carol, req)
        self.assertEqual(resp.data['status'], PaymentRequest.APPROVED)
        self.assertEqual(resp.data['approval'], {
            'required_levels': [M], 'approved_levels': [M], 'outstanding_levels': [],
        })
        self.assertEqual(self.actions(req), [
            (AuditLog.APPROVED, 'carol'), (AuditLog.AMOUNT_CHANGED, 'alice'), (AuditLog.APPROVED, 'carol'),
        ])

    def test_42000_raised_to_67000_goes_back_for_manager_and_finance(self):
        req = self.make_request('42000')
        self.approve(self.carol, req)
        req.refresh_from_db()
        self.assertEqual(req.status, PaymentRequest.APPROVED)

        self.change_amount(self.alice, req, '67000')

        req.refresh_from_db()
        self.assertEqual(req.status, PaymentRequest.PENDING_APPROVAL)
        self.assertIn(req.pk, self.queue_ids(self.carol))
        self.assertIn(req.pk, self.queue_ids(self.faith))         # now over 50k
        self.approve(self.carol, req)
        resp = self.approve(self.faith, req)
        self.assertEqual(resp.data['status'], PaymentRequest.APPROVED)

    def test_finance_rejects_after_manager_approved(self):
        req = self.make_request('148000')
        self.approve(self.carol, req)

        resp = self.reject(self.faith, req, 'Over the project budget')

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['status'], PaymentRequest.REJECTED)
        self.assertNotIn(req.pk, self.queue_ids(self.carol))
        self.assertNotIn(req.pk, self.queue_ids(self.faith))
        self.assertEqual(self.approve(self.david, req).status_code, 409)   # rejected is final
        # The requester can read why.
        last = self.history(self.alice, req)[-1]
        self.assertEqual(last['action'], AuditLog.REJECTED)
        self.assertEqual(last['details']['reason'], 'Over the project budget')

    def test_manager_request_is_approved_by_another_manager(self):
        # Managers raise requests too; they just can't approve their own.
        req = self.make_request('24500', requester=self.carol)
        self.assertNotIn(req.pk, self.queue_ids(self.carol))
        self.assertEqual(self.approve(self.carol, req).status_code, 403)

        resp = self.approve(self.david, req)
        self.assertEqual(resp.data['status'], PaymentRequest.APPROVED)
        self.assertEqual(self.actions(req), [(AuditLog.APPROVED, 'david')])
