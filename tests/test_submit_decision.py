"""Unit tests for the submit decision (apps/approvals/services.py).

SimpleTestCase -> no database. decide_submission() takes plain data, so each
rule is checked on its own: who may submit, from which status, and what the
audit entry records.
"""

from django.test import SimpleTestCase

from apps.approvals.logic import LEVEL_FINANCE as F, LEVEL_MANAGER as M
from apps.approvals.services import (
    AUDIT_SUBMITTED,
    MSG_ALREADY_SUBMITTED,
    MSG_NOT_REQUESTER,
    STATUS_APPROVED,
    STATUS_DRAFT,
    STATUS_PENDING,
    STATUS_REJECTED,
    STATUS_SUBMITTED,
    SubmitDenied,
    decide_submission,
)
from apps.payment_requests.models import PaymentRequest


class DecideSubmissionTests(SimpleTestCase):

    def _decide(self, **overrides):
        args = dict(is_requester=True, status=STATUS_DRAFT, amount=24500)
        args.update(overrides)
        return decide_submission(**args)

    def _assert_denied(self, reason, **overrides):
        with self.assertRaises(SubmitDenied) as ctx:
            self._decide(**overrides)
        self.assertEqual(ctx.exception.reason, reason)

    # --- the happy path ---

    def test_draft_goes_straight_to_pending_approval(self):
        self.assertEqual(self._decide().new_status, STATUS_PENDING)

    def test_submitted_status_can_still_be_sent_on(self):
        decision = self._decide(status=STATUS_SUBMITTED)
        self.assertEqual(decision.new_status, STATUS_PENDING)

    def test_audit_entry_is_submitted(self):
        self.assertEqual(self._decide().audit_action, AUDIT_SUBMITTED)

    def test_small_request_records_manager_tier(self):
        details = self._decide(amount=24500).audit_details
        self.assertEqual(details, {"amount": "24500", "required_levels": [M]})

    def test_large_request_records_manager_and_finance_tier(self):
        details = self._decide(amount=148000).audit_details
        self.assertEqual(details["required_levels"], sorted([M, F]))

    def test_exactly_50000_is_manager_tier(self):
        details = self._decide(amount=50000).audit_details
        self.assertEqual(details["required_levels"], [M])

    # --- refusals ---

    def test_only_the_requester_can_submit(self):
        self._assert_denied(MSG_NOT_REQUESTER, is_requester=False)

    def test_pending_request_cannot_be_submitted_again(self):
        self._assert_denied(MSG_ALREADY_SUBMITTED, status=STATUS_PENDING)

    def test_later_statuses_cannot_be_submitted(self):
        for status in (STATUS_APPROVED, STATUS_REJECTED, PaymentRequest.PROCESSING,
                       PaymentRequest.PAID, PaymentRequest.FAILED):
            with self.subTest(status=status):
                self._assert_denied(MSG_ALREADY_SUBMITTED, status=status)

    def test_requester_check_comes_before_status_check(self):
        # Someone else's request gets "not yours", not a hint about its state.
        self._assert_denied(MSG_NOT_REQUESTER, is_requester=False, status=STATUS_PENDING)
