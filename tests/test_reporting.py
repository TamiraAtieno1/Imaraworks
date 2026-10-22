"""
Reports: totals over payment requests, scoped to what the user can see.

Uses the case-study amounts (24,500 / 67,000 / 148,000 / 42,000).
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APITestCase

from apps.accounts.models import Profile
from apps.payment_requests.models import PaymentRequest, Project
from apps.payments.models import PaymentTransaction
from apps.reporting.services import (
    filter_requests, summary, totals_by_project, totals_by_status, totals_by_vendor,
)
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

    def test_no_filter_keeps_everything(self):
        self.assertEqual(filter_requests(self.all).count(), 4)

    def test_status_filter_keeps_only_that_status(self):
        paid = filter_requests(self.all, status=PaymentRequest.PAID)
        self.assertEqual(totals_by_vendor(paid), [
            {'id': self.metro.id, 'name': 'Metro Hardware Ltd.', 'count': 1, 'total': '24500.00'},
        ])


class ReportApiTests(ReportData, APITestCase):
    def setUp(self):
        self.make_data()

    def get_as(self, user, **params):
        self.client.force_authenticate(user)
        return self.client.get(URL, params)

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

    def test_status_filter_gives_paid_totals_per_project_and_vendor(self):
        # What the Finance page tables need: money actually paid, not everything requested.
        res = self.get_as(self.faith, status=PaymentRequest.PAID)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['overall'], {'count': 1, 'total': '24500.00'})
        self.assertEqual([(p['name'], p['total']) for p in res.data['by_project']],
                         [('Westlands Project', '24500.00')])
        self.assertEqual([(v['name'], v['total']) for v in res.data['by_vendor']],
                         [('Metro Hardware Ltd.', '24500.00')])

    def test_status_filter_cannot_widen_what_an_employee_sees(self):
        # brian's 148,000 is also PENDING_APPROVAL, but alice must still only see her own.
        res = self.get_as(self.alice, status=PaymentRequest.PENDING_APPROVAL)
        self.assertEqual(res.data['overall'], {'count': 1, 'total': '67000.00'})

    def test_unknown_status_is_rejected(self):
        res = self.get_as(self.carol, status='BOGUS')
        self.assertEqual(res.status_code, 400)
        self.assertIn('status', res.data)

    def test_status_is_case_sensitive(self):
        # Values are the uppercase constants, as in every other response.
        self.assertEqual(self.get_as(self.carol, status='paid').status_code, 400)


class ReportDateFilterTests(ReportData, APITestCase):
    """24,500 on 30 Sep; 67,000 and 148,000 on 1 Oct; 42,000 on 31 Oct."""

    def setUp(self):
        self.make_data()
        for amount, day in [('24500', date(2026, 9, 30)), ('67000', date(2026, 10, 1)),
                            ('148000', date(2026, 10, 1)), ('42000', date(2026, 10, 31))]:
            PaymentRequest.objects.filter(amount=Decimal(amount)).update(date=day)
        self.client.force_authenticate(self.carol)

    def overall(self, **params):
        res = self.client.get(URL, params)
        self.assertEqual(res.status_code, 200)
        return res.data['overall']

    def test_range_includes_both_end_days(self):
        # All of October: 1 Oct and 31 Oct are in, 30 Sep is out.
        self.assertEqual(self.overall(date_from='2026-10-01', date_to='2026-10-31'),
                         {'count': 3, 'total': '257000.00'})

    def test_date_from_alone(self):
        self.assertEqual(self.overall(date_from='2026-10-31'), {'count': 1, 'total': '42000.00'})

    def test_date_to_alone(self):
        self.assertEqual(self.overall(date_to='2026-09-30'), {'count': 1, 'total': '24500.00'})

    def test_dates_combine_with_status(self):
        self.assertEqual(self.overall(date_from='2026-10-01', status=PaymentRequest.PENDING_APPROVAL),
                         {'count': 2, 'total': '215000.00'})

    def test_bad_date_is_rejected(self):
        res = self.client.get(URL, {'date_from': '2026-13-01'})
        self.assertEqual(res.status_code, 400)
        self.assertIn('date_from', res.data)

    def test_reversed_range_is_rejected(self):
        res = self.client.get(URL, {'date_from': '2026-10-31', 'date_to': '2026-10-01'})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['date_to'], ['Must be on or after date_from.'])

    def test_same_day_range_is_allowed(self):
        self.assertEqual(self.overall(date_from='2026-10-01', date_to='2026-10-01')['count'], 2)


EAT = timezone(timedelta(hours=3))


class ReportPaidDateFilterTests(ReportData, APITestCase):
    """paid_from/paid_to count money by when it was paid out, in Kenyan days (#41).

    24,500 (alice): requested 30 Sep, paid 2 Oct 10:00, after a failed try on 1 Oct.
    15,000 (brian): requested 1 Oct, paid 31 Oct 23:30 (20:30 UTC, still 31 Oct).
    9,000 (brian): requested 1 Oct, failed 20 Oct, paid 1 Nov 01:00 (31 Oct 22:00 UTC).
    """

    def setUp(self):
        self.make_data()
        first = PaymentRequest.objects.get(amount=Decimal('24500'))
        PaymentRequest.objects.filter(pk=first.pk).update(date=date(2026, 9, 30))
        self.pay(first, datetime(2026, 10, 1, 9, 0, tzinfo=EAT), PaymentTransaction.FAILED)
        self.pay(first, datetime(2026, 10, 2, 10, 0, tzinfo=EAT))
        late = self.make_request(self.brian, '15000.00', PaymentRequest.PAID, self.metro, self.kisumu)
        self.pay(late, datetime(2026, 10, 31, 23, 30, tzinfo=EAT))
        nov = self.make_request(self.brian, '9000.00', PaymentRequest.PAID, self.metro, self.kisumu)
        self.pay(nov, datetime(2026, 10, 20, 12, 0, tzinfo=EAT), PaymentTransaction.FAILED)
        self.pay(nov, datetime(2026, 11, 1, 1, 0, tzinfo=EAT))
        PaymentRequest.objects.filter(pk__in=[late.pk, nov.pk]).update(date=date(2026, 10, 1))
        self.client.force_authenticate(self.carol)

    def pay(self, req, when, status=PaymentTransaction.SUCCESS):
        count = req.payment_transactions.count()
        PaymentTransaction.objects.create(
            request=req, idempotency_key=f'key-{req.pk}-{count}', amount=req.amount,
            status=status, completed_at=when,
        )

    def overall(self, **params):
        res = self.client.get(URL, params)
        self.assertEqual(res.status_code, 200)
        return res.data['overall']

    def test_october_counts_money_paid_in_october(self):
        # 24,500 was requested in September but paid in October, so it's in.
        # 9,000 was paid at 01:00 on 1 November in Nairobi, so it's out, even
        # though that is still 31 October in UTC.
        self.assertEqual(self.overall(paid_from='2026-10-01', paid_to='2026-10-31'),
                         {'count': 2, 'total': '39500.00'})

    def test_request_date_range_gives_a_different_answer(self):
        # The same month by request date: the difference #41 is about.
        self.assertEqual(self.overall(date_from='2026-10-01', date_to='2026-10-31',
                                      status=PaymentRequest.PAID),
                         {'count': 2, 'total': '24000.00'})

    def test_last_minutes_of_the_day_count_in_that_day(self):
        self.assertEqual(self.overall(paid_from='2026-10-31', paid_to='2026-10-31'),
                         {'count': 1, 'total': '15000.00'})

    def test_paid_from_alone(self):
        self.assertEqual(self.overall(paid_from='2026-11-01'), {'count': 1, 'total': '9000.00'})

    def test_paid_to_alone(self):
        self.assertEqual(self.overall(paid_to='2026-10-02'), {'count': 1, 'total': '24500.00'})

    def test_failed_attempts_do_not_count(self):
        # 24,500 failed on 1 Oct and 9,000 failed on 20 Oct; no money moved on either day.
        self.assertEqual(self.overall(paid_from='2026-10-01', paid_to='2026-10-01'),
                         {'count': 0, 'total': '0.00'})
        self.assertEqual(self.overall(paid_from='2026-10-20', paid_to='2026-10-20'),
                         {'count': 0, 'total': '0.00'})

    def test_a_request_with_several_transactions_is_counted_once(self):
        res = self.client.get(URL, {'paid_from': '2026-10-01', 'paid_to': '2026-10-02'})
        self.assertEqual(res.data['by_vendor'],
                         [{'id': self.metro.id, 'name': 'Metro Hardware Ltd.',
                           'count': 1, 'total': '24500.00'}])

    def test_unpaid_requests_never_match(self):
        res = self.client.get(URL, {'paid_from': '2026-01-01'})
        self.assertEqual(res.data['pending_approval'], {'count': 0, 'total': '0.00'})
        self.assertEqual(res.data['paid'], res.data['overall'])

    def test_combines_with_request_date(self):
        # Raised in October and paid in October: only 15,000.
        self.assertEqual(self.overall(date_from='2026-10-01', paid_from='2026-10-01',
                                      paid_to='2026-10-31'),
                         {'count': 1, 'total': '15000.00'})

    def test_employee_still_sees_only_their_own(self):
        self.client.force_authenticate(self.alice)
        self.assertEqual(self.overall(paid_from='2026-10-01', paid_to='2026-10-31'),
                         {'count': 1, 'total': '24500.00'})

    def test_bad_paid_date_is_rejected(self):
        res = self.client.get(URL, {'paid_to': '31-10-2026'})
        self.assertEqual(res.status_code, 400)
        self.assertIn('paid_to', res.data)

    def test_reversed_paid_range_is_rejected(self):
        res = self.client.get(URL, {'paid_from': '2026-10-31', 'paid_to': '2026-10-01'})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['paid_to'], ['Must be on or after paid_from.'])
