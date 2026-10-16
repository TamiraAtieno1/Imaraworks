"""HTTP tests for PATCH /api/payment-requests/<id>/amount/ (docs/APPROVALS_API.md).

The rules are tested in test_approval_services.py and the saving in
test_amount_change_action.py. These check the HTTP layer: the route, body
validation, each refusal's status code, and the response shape.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from rest_framework.test import APITestCase

from apps.accounts.models import Profile
from apps.approvals.logic import LEVEL_FINANCE as F, LEVEL_MANAGER as M
from apps.approvals.services import MSG_AMOUNT_LOCKED, MSG_NOT_REQUESTER_EDIT, approve, reject
from apps.payment_requests.models import PaymentRequest, Project
from apps.vendors.models import Vendor


def make_user(name, role):
    # No password: tests log in with force_authenticate, and hashing is slow.
    user = User.objects.create_user(name)
    Profile.objects.create(user=user, role=role)
    return user


class AmountApiTests(APITestCase):

    @classmethod
    def setUpTestData(cls):
        cls.alice = make_user('alice', Profile.EMPLOYEE)
        cls.brian = make_user('brian', Profile.EMPLOYEE)
        cls.carol = make_user('carol', Profile.MANAGER)
        cls.grace = make_user('grace', Profile.ADMIN)
        cls.vendor = Vendor.objects.create(name='Nairobi Cement Ltd', service='Cement')
        cls.project = Project.objects.create(name='Kilimani Apartments')

    def make_request(self, amount, status=PaymentRequest.PENDING_APPROVAL):
        return PaymentRequest.objects.create(
            vendor=self.vendor, project=self.project, requester=self.alice,
            amount=Decimal(amount), reason='Materials',
            invoice_number=f'INV-{PaymentRequest.objects.count() + 1}',
            payment_method=PaymentRequest.MPESA,
            date=datetime.date(2026, 10, 15), status=status,
        )

    def patch_as(self, user, req, body):
        self.client.force_authenticate(user)
        return self.client.patch(f'/api/payment-requests/{req.pk}/amount/', body, format='json')

    def assert_amount(self, req, amount):
        req.refresh_from_db()
        self.assertEqual(req.amount, Decimal(amount))

    # --- 200 ---

    def test_42000_approved_raised_to_67000_needs_manager_and_finance(self):
        req = self.make_request('42000')
        approve(req, self.carol)
        res = self.patch_as(self.alice, req, {'amount': '67000'})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['amount'], '67000.00')
        self.assertEqual(res.data['status'], PaymentRequest.PENDING_APPROVAL)
        self.assertEqual(res.data['approval'], {
            'required_levels': sorted([M, F]),
            'approved_levels': [],
            'outstanding_levels': sorted([M, F]),
        })

    def test_draft_amount_changes_and_stays_draft(self):
        req = self.make_request('24500', status=PaymentRequest.DRAFT)
        res = self.patch_as(self.alice, req, {'amount': '26000.50'})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['status'], PaymentRequest.DRAFT)
        self.assert_amount(req, '26000.50')

    def test_same_amount_is_200_and_writes_nothing(self):
        req = self.make_request('24500')
        res = self.patch_as(self.alice, req, {'amount': '24500.00'})
        self.assertEqual(res.status_code, 200)
        self.assertFalse(req.audit_logs.exists())

    # --- 400: bad body ---

    def test_bad_amounts_are_400(self):
        req = self.make_request('24500')
        for body in ({}, {'amount': ''}, {'amount': 'abc'}, {'amount': '0'},
                     {'amount': '-5'}, {'amount': '1.234'}, {'amount': '12345678901'}):
            with self.subTest(body=body):
                res = self.patch_as(self.alice, req, body)
                self.assertEqual(res.status_code, 400)
                self.assertIn('amount', res.data)
        self.assert_amount(req, '24500')

    # --- refusals ---

    def test_not_logged_in_is_401(self):
        req = self.make_request('24500')
        res = self.client.patch(f'/api/payment-requests/{req.pk}/amount/', {'amount': '1'}, format='json')
        self.assertEqual(res.status_code, 401)

    def test_another_employees_request_is_404(self):
        req = self.make_request('24500')
        self.assertEqual(self.patch_as(self.brian, req, {'amount': '1'}).status_code, 404)
        self.assert_amount(req, '24500')

    def test_admin_gets_404(self):
        req = self.make_request('24500')
        self.assertEqual(self.patch_as(self.grace, req, {'amount': '1'}).status_code, 404)

    def test_approver_is_403(self):
        req = self.make_request('24500')
        approve(req, self.carol)
        res = self.patch_as(self.carol, req, {'amount': '20000'})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.data, {'detail': MSG_NOT_REQUESTER_EDIT})
        self.assert_amount(req, '24500')

    def test_rejected_request_is_409(self):
        req = self.make_request('24500')
        reject(req, self.carol, 'Duplicate invoice')
        res = self.patch_as(self.alice, req, {'amount': '20000'})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.data, {'detail': MSG_AMOUNT_LOCKED})
        self.assert_amount(req, '24500')

    def test_put_and_post_are_not_allowed(self):
        req = self.make_request('24500')
        self.client.force_authenticate(self.alice)
        url = f'/api/payment-requests/{req.pk}/amount/'
        self.assertEqual(self.client.put(url, {'amount': '1'}, format='json').status_code, 405)
        self.assertEqual(self.client.post(url, {'amount': '1'}, format='json').status_code, 405)
