"""HTTP tests for POST /api/payment-requests/<id>/submit/ (docs/APPROVALS_API.md).

The rules are tested in test_submit_decision.py and the saving in
test_submit_action.py. These check the HTTP layer: the route, each refusal's
status code, and the response shape.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.utils.dateparse import parse_datetime
from rest_framework.test import APITestCase

from apps.accounts.models import Profile
from apps.approvals.logic import LEVEL_FINANCE as F, LEVEL_MANAGER as M
from apps.approvals.services import MSG_ALREADY_SUBMITTED, MSG_NOT_REQUESTER
from apps.payment_requests.models import PaymentRequest, Project
from apps.vendors.models import Vendor


def make_user(name, role):
    # No password: tests log in with force_authenticate, and hashing is slow.
    user = User.objects.create_user(name)
    Profile.objects.create(user=user, role=role)
    return user


class SubmitApiTests(APITestCase):

    @classmethod
    def setUpTestData(cls):
        cls.alice = make_user('alice', Profile.EMPLOYEE)
        cls.brian = make_user('brian', Profile.EMPLOYEE)
        cls.carol = make_user('carol', Profile.MANAGER)
        cls.grace = make_user('grace', Profile.ADMIN)
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

    def submit_as(self, user, req):
        self.client.force_authenticate(user)
        return self.client.post(f'/api/payment-requests/{req.pk}/submit/')

    def assert_still(self, req, status):
        req.refresh_from_db()
        self.assertEqual(req.status, status)

    # --- 200 ---

    def test_requester_submits_and_gets_full_request_with_approval_block(self):
        req = self.make_request('148000')
        res = self.submit_as(self.alice, req)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['id'], req.pk)
        self.assertEqual(res.data['status'], PaymentRequest.PENDING_APPROVAL)
        self.assertEqual(res.data['vendor_name'], 'Nairobi Cement Ltd')
        self.assertEqual(res.data['approval'], {
            'required_levels': sorted([M, F]),
            'approved_levels': [],
            'outstanding_levels': sorted([M, F]),
        })

    def test_response_includes_submitted_at(self):
        req = self.make_request('24500')
        res = self.submit_as(self.alice, req)
        req.refresh_from_db()
        self.assertIsNotNone(res.data['submitted_at'])
        self.assertEqual(parse_datetime(res.data['submitted_at']), req.submitted_at)

    def test_submitted_request_shows_in_managers_queue(self):
        req = self.make_request('24500')
        self.submit_as(self.alice, req)
        self.client.force_authenticate(self.carol)
        res = self.client.get('/api/approvals/queue/')
        self.assertEqual([item['id'] for item in res.data], [req.pk])

    # --- refusals ---

    def test_not_logged_in_is_401(self):
        req = self.make_request('24500')
        res = self.client.post(f'/api/payment-requests/{req.pk}/submit/')
        self.assertEqual(res.status_code, 401)

    def test_another_employees_request_is_404(self):
        # brian can't see alice's request at all, so it "doesn't exist".
        req = self.make_request('24500')
        self.assertEqual(self.submit_as(self.brian, req).status_code, 404)
        self.assert_still(req, PaymentRequest.DRAFT)

    def test_admin_gets_404(self):
        req = self.make_request('24500')
        self.assertEqual(self.submit_as(self.grace, req).status_code, 404)

    def test_manager_on_an_employees_request_is_403(self):
        # carol can see it (managers see all requests), but it isn't hers.
        req = self.make_request('24500')
        res = self.submit_as(self.carol, req)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.data, {'detail': MSG_NOT_REQUESTER})
        self.assert_still(req, PaymentRequest.DRAFT)

    def test_submitting_twice_is_409(self):
        req = self.make_request('24500')
        self.submit_as(self.alice, req)
        res = self.submit_as(self.alice, req)
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.data, {'detail': MSG_ALREADY_SUBMITTED})
        self.assertEqual(req.audit_logs.count(), 1)

    def test_get_is_not_allowed(self):
        req = self.make_request('24500')
        self.client.force_authenticate(self.alice)
        res = self.client.get(f'/api/payment-requests/{req.pk}/submit/')
        self.assertEqual(res.status_code, 405)
