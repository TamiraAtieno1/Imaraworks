"""
Reports: totals over payment requests, scoped to what the user can see.

Uses the case-study amounts (24,500 / 67,000 / 148,000 / 42,000).
"""

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APITestCase

from apps.accounts.models import Profile
from apps.payment_requests.models import PaymentRequest, Project
from apps.reporting.services import summary, totals_by_project, totals_by_status, totals_by_vendor
from apps.vendors.models import Vendor

URL = '/api/reports/summary/'


def make_user(name, role):
    user = User.objects.create_user(name)
    Profile.objects.create(user=user, role=role)
    return user


class ReportData:
    """alice: 24,500 PAID and 67,000 PENDING. brian: 148,000 PENDING and 42,000 REJECTED."""

    def make_data(self):
        self.alice = make_user('alice', Profile.EMPLOYEE)
        self.brian = make_user('brian', Profile.EMPLOYEE)
        self.carol = make_user('carol', Profile.MANAGER)
        self.faith = make_user('faith', Profile.FINANCE)
        self.grace = make_user('grace', Profile.ADMIN)
        self.metro = Vendor.objects.create(name='Metro Hardware Ltd.', service='Materials')
        self.cement = Vendor.objects.create(name='Prime Cement Supplies', service='Cement')
        self.westlands = Project.objects.create(name='Westlands Project')
        self.kisumu = Project.objects.create(name='Kisumu Project')
        self.make_request(self.alice, '24500.00', PaymentRequest.PAID, self.metro, self.westlands)
        self.make_request(self.alice, '67000.00', PaymentRequest.PENDING_APPROVAL, self.cement, self.westlands)
        self.make_request(self.brian, '148000.00', PaymentRequest.PENDING_APPROVAL, self.cement, self.kisumu)
        self.make_request(self.brian, '42000.00', PaymentRequest.REJECTED, self.metro, self.kisumu)

    def make_request(self, requester, amount, status, vendor, project):
        count = PaymentRequest.objects.count()
        return PaymentRequest.objects.create(
            requester=requester, vendor=vendor, project=project, amount=Decimal(amount),
            status=status, reason='Site materials', invoice_number=f'INV-{count + 1}',
            payment_method=PaymentRequest.MPESA, date=date(2026, 10, 11),
        )


class SummaryServiceTests(ReportData, TestCase):
    def setUp(self):
        self.make_data()
        self.all = PaymentRequest.objects.all()

    def test_overall_total(self):
        self.assertEqual(summary(self.all)['overall'], {'count': 4, 'total': '281500.00'})

    def test_pending_approval_headline(self):
        self.assertEqual(summary(self.all)['pending_approval'], {'count': 2, 'total': '215000.00'})

    def test_paid_headline(self):
        self.assertEqual(summary(self.all)['paid'], {'count': 1, 'total': '24500.00'})

    def test_by_status_lists_every_status_in_lifecycle_order(self):
        rows = totals_by_status(self.all)
        self.assertEqual([r['status'] for r in rows], [s for s, _ in PaymentRequest.STATUS_CHOICES])

    def test_by_status_shows_zero_for_unused_statuses(self):
        rows = {r['status']: r for r in totals_by_status(self.all)}
        self.assertEqual(rows[PaymentRequest.DRAFT]['count'], 0)
        self.assertEqual(rows[PaymentRequest.DRAFT]['total'], '0.00')
        self.assertEqual(rows[PaymentRequest.REJECTED]['total'], '42000.00')
        self.assertEqual(rows[PaymentRequest.PENDING_APPROVAL]['label'], 'Pending Approval')

    def test_by_project_biggest_first(self):
        self.assertEqual(totals_by_project(self.all), [
            {'id': self.kisumu.id, 'name': 'Kisumu Project', 'count': 2, 'total': '190000.00'},
            {'id': self.westlands.id, 'name': 'Westlands Project', 'count': 2, 'total': '91500.00'},
        ])

    def test_by_vendor_biggest_first(self):
        self.assertEqual(totals_by_vendor(self.all), [
            {'id': self.cement.id, 'name': 'Prime Cement Supplies', 'count': 2, 'total': '215000.00'},
            {'id': self.metro.id, 'name': 'Metro Hardware Ltd.', 'count': 2, 'total': '66500.00'},
        ])

    def test_empty_queryset_gives_zeros_not_errors(self):
        report = summary(PaymentRequest.objects.none())
        self.assertEqual(report['overall'], {'count': 0, 'total': '0.00'})
        self.assertEqual(report['by_project'], [])
        self.assertEqual(report['by_vendor'], [])
        self.assertTrue(all(r['count'] == 0 for r in report['by_status']))

    def test_amounts_keep_cents(self):
        self.make_request(self.alice, '0.05', PaymentRequest.DRAFT, self.metro, self.westlands)
        self.assertEqual(summary(self.all)['overall']['total'], '281500.05')


class ReportApiTests(ReportData, APITestCase):
    def setUp(self):
        self.make_data()

    def get_as(self, user):
        self.client.force_authenticate(user)
        return self.client.get(URL)

    def test_requires_login(self):
        self.assertEqual(self.client.get(URL).status_code, 401)

    def test_manager_sees_every_request(self):
        res = self.get_as(self.carol)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['overall'], {'count': 4, 'total': '281500.00'})

    def test_finance_sees_every_request(self):
        self.assertEqual(self.get_as(self.faith).data['overall']['count'], 4)

    def test_employee_sees_only_their_own(self):
        res = self.get_as(self.alice)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['overall'], {'count': 2, 'total': '91500.00'})
        self.assertEqual([v['name'] for v in res.data['by_vendor']],
                         ['Prime Cement Supplies', 'Metro Hardware Ltd.'])
        self.assertEqual(res.data['by_project'][0]['total'], '91500.00')

    def test_admin_is_refused(self):
        # Admins don't see financial data (team decision), so no totals either.
        res = self.get_as(self.grace)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(str(res.data['detail']), 'Your role cannot view reports.')

    def test_user_without_role_is_refused(self):
        self.assertEqual(self.get_as(User.objects.create_user('nobody')).status_code, 403)

    def test_amounts_are_json_strings(self):
        res = self.get_as(self.carol)
        self.assertIn(b'"total":"281500.00"', res.content)

    def test_post_not_allowed(self):
        self.client.force_authenticate(self.carol)
        self.assertEqual(self.client.post(URL).status_code, 405)
