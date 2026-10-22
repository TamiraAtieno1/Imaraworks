"""Tests for the migration that adds submitted_at and fills it in (#40).

Requests sent for approval before the field existed get it from the audit log:
the latest SUBMITTED entry, or a later AMOUNT_CHANGED entry if the amount
changed after submission. These run the real migration on a database rolled
back to just before it.
"""

import datetime

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone

# The migration under test, and the one before it.
BEFORE, AFTER = '0001_initial', '0002_submitted_at'


def targets(payment_requests_migration):
    """Every app at its latest migration, except payment_requests at the one given."""
    leaves = MigrationExecutor(connection).loader.graph.leaf_nodes()
    return [node for node in leaves if node[0] != 'payment_requests'] + [
        ('payment_requests', payment_requests_migration)]


class SubmittedAtBackfillTests(TransactionTestCase):

    def setUp(self):
        before = targets(BEFORE)
        executor = MigrationExecutor(connection)
        executor.migrate(before)
        old_apps = executor.loader.project_state(before).apps

        User = old_apps.get_model('auth', 'User')
        Vendor = old_apps.get_model('vendors', 'Vendor')
        Project = old_apps.get_model('payment_requests', 'Project')
        PaymentRequest = old_apps.get_model('payment_requests', 'PaymentRequest')
        AuditLog = old_apps.get_model('audit', 'AuditLog')

        alice = User.objects.create(username='alice')
        vendor = Vendor.objects.create(name='Nairobi Cement Ltd', service='Cement')
        project = Project.objects.create(name='Kilimani Apartments')
        self.base = timezone.now() - datetime.timedelta(days=10)

        def make(invoice, status):
            return PaymentRequest.objects.create(
                vendor=vendor, project=project, requester=alice,
                amount='24500', reason='Materials', invoice_number=invoice,
                payment_method='MPESA', date=datetime.date(2026, 10, 1), status=status,
            )

        def log(req, action, days_after):
            entry = AuditLog.objects.create(request=req, actor=alice, action=action)
            # timestamp is auto_now_add, so set it afterwards. The historical
            # model has no append-only guard, so update() is allowed here.
            AuditLog.objects.filter(pk=entry.pk).update(
                timestamp=self.base + datetime.timedelta(days=days_after))

        self.draft = make('INV-1', 'DRAFT')
        log(self.draft, 'CREATED', 0)

        self.submitted = make('INV-2', 'PENDING_APPROVAL')
        log(self.submitted, 'SUBMITTED', 1)

        self.changed = make('INV-3', 'PENDING_APPROVAL')
        log(self.changed, 'SUBMITTED', 1)
        log(self.changed, 'AMOUNT_CHANGED', 4)

        # Amount changed while still a draft, then submitted: the
        # submission is the later event, so it wins.
        self.changed_as_draft = make('INV-4', 'PENDING_APPROVAL')
        log(self.changed_as_draft, 'AMOUNT_CHANGED', 1)
        log(self.changed_as_draft, 'SUBMITTED', 2)

        # Created straight into a later status, with no SUBMITTED entry.
        self.no_entry = make('INV-5', 'APPROVED')

        after = targets(AFTER)
        executor = MigrationExecutor(connection)
        executor.migrate(after)
        self.PaymentRequest = executor.loader.project_state(after).apps.get_model(
            'payment_requests', 'PaymentRequest')

    def tearDown(self):
        # Leave the database fully migrated for the tests that run after these.
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def submitted_at(self, req):
        return self.PaymentRequest.objects.get(pk=req.pk).submitted_at

    def at(self, days):
        return self.base + datetime.timedelta(days=days)

    def test_draft_is_left_empty(self):
        self.assertIsNone(self.submitted_at(self.draft))

    def test_taken_from_the_submitted_entry(self):
        self.assertEqual(self.submitted_at(self.submitted), self.at(1))

    def test_a_later_amount_change_restarts_the_wait(self):
        self.assertEqual(self.submitted_at(self.changed), self.at(4))

    def test_a_change_before_submission_is_ignored(self):
        self.assertEqual(self.submitted_at(self.changed_as_draft), self.at(2))

    def test_no_submitted_entry_is_left_empty(self):
        self.assertIsNone(self.submitted_at(self.no_entry))
