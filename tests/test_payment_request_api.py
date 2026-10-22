from django.contrib.auth.models import User
from rest_framework.test import APITestCase

from apps.accounts.models import Profile
from apps.payment_requests.models import Project
from apps.vendors.models import Vendor


class PaymentRequestApiTests(APITestCase):
    def setUp(self):
        self.vendor = Vendor.objects.create(name='Metro Hardware Ltd.', service='Materials')
        self.project = Project.objects.create(name='Westlands Project')
        self.alice = self.make_user('alice', Profile.EMPLOYEE)
        self.brian = self.make_user('brian', Profile.EMPLOYEE)
        self.carol = self.make_user('carol', Profile.MANAGER)
        self.grace = self.make_user('grace', Profile.ADMIN)
        self.url = '/api/payment-requests/'

    def make_user(self, name, role):
        user = User.objects.create_user(name)
        Profile.objects.create(user=user, role=role)
        return user

    def payload(self, **overrides):
        data = {
            'vendor': self.vendor.id, 'project': self.project.id,
            'amount': '24500.00', 'reason': 'Site materials',
            'invoice_number': 'INV-1032', 'payment_method': 'MPESA',
            'date': '2026-10-11', 'notes': '',
        }
        data.update(overrides)
        return data

    def test_requires_login(self):
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_requester_is_set_from_logged_in_user(self):
        self.client.force_authenticate(self.alice)
        res = self.client.post(self.url, self.payload(requester=self.brian.id), format='json')
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data['requester_username'], 'alice')
        self.assertEqual(res.data['status'], 'DRAFT')

    def test_client_cannot_set_status(self):
        self.client.force_authenticate(self.alice)
        res = self.client.post(self.url, self.payload(status='APPROVED'), format='json')
        self.assertEqual(res.data['status'], 'DRAFT')

    def test_new_draft_has_no_submitted_at_and_client_cannot_set_it(self):
        self.client.force_authenticate(self.alice)
        res = self.client.post(
            self.url, self.payload(submitted_at='2026-01-01T00:00:00Z'), format='json')
        self.assertEqual(res.status_code, 201)
        self.assertIsNone(res.data['submitted_at'])

    def test_duplicate_invoice_returns_400(self):
        self.client.force_authenticate(self.alice)
        self.client.post(self.url, self.payload(), format='json')
        res = self.client.post(self.url, self.payload(), format='json')
        self.assertEqual(res.status_code, 400)

    def test_negative_amount_returns_400(self):
        self.client.force_authenticate(self.alice)
        res = self.client.post(self.url, self.payload(amount='-5'), format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn('amount', res.data)

    def test_employee_sees_only_own_requests(self):
        self.client.force_authenticate(self.alice)
        self.client.post(self.url, self.payload(), format='json')
        self.client.force_authenticate(self.brian)
        self.assertEqual(len(self.client.get(self.url).data), 0)

    def test_manager_sees_all_requests(self):
        self.client.force_authenticate(self.alice)
        self.client.post(self.url, self.payload(), format='json')
        self.client.force_authenticate(self.carol)
        self.assertEqual(len(self.client.get(self.url).data), 1)

    def test_other_employees_request_is_404(self):
        self.client.force_authenticate(self.alice)
        pk = self.client.post(self.url, self.payload(), format='json').data['id']
        self.client.force_authenticate(self.brian)
        self.assertEqual(self.client.get(f'{self.url}{pk}/').status_code, 404)

    def test_admin_cannot_create_or_list(self):
        self.client.force_authenticate(self.grace)
        self.assertEqual(self.client.post(self.url, self.payload(), format='json').status_code, 403)
        self.assertEqual(len(self.client.get(self.url).data), 0)

    def test_forbidden_role_is_rejected_before_validation(self):
        self.client.force_authenticate(self.alice)
        self.client.post(self.url, self.payload(), format='json')
        self.client.force_authenticate(self.grace)
        res = self.client.post(self.url, self.payload(), format='json')  # same invoice
        self.assertEqual(res.status_code, 403)

    def test_me_works_for_user_without_profile(self):
        user = User.objects.create_user('noprofile')
        self.client.force_authenticate(user)
        res = self.client.get('/api/auth/me/')
        self.assertEqual(res.status_code, 200)
        self.assertIsNone(res.data['role'])

    def test_me_returns_user_id(self):
        self.client.force_authenticate(self.alice)
        res = self.client.get('/api/auth/me/')
        self.assertEqual(res.data['id'], self.alice.id)

    def test_request_includes_requester_full_name(self):
        self.alice.first_name = 'Alice'
        self.alice.last_name = 'Mwangi'
        self.alice.save()
        self.client.force_authenticate(self.alice)
        res = self.client.post(self.url, self.payload(), format='json')
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data['requester_name'], 'Alice Mwangi')