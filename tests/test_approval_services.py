"""Unit tests for the approve decision (apps/approvals/services.py).

SimpleTestCase -> no database. These test the *combined* outcome of an approve
action (level recorded, new status, audit details); the individual rules are
already covered in test_logic.py, so denial tests here only check that a
refusal surfaces as ApprovalDenied with the right reason.
"""

from django.test import SimpleTestCase

from apps.approvals import logic
from apps.approvals.services import (
    AUDIT_AMOUNT_CHANGED,
    AUDIT_APPROVED,
    AUDIT_REJECTED,
    MSG_AMOUNT_LOCKED,
    MSG_NOT_REQUESTER_EDIT,
    MSG_REASON_REQUIRED,
    STATUS_APPROVED,
    STATUS_DRAFT,
    STATUS_PENDING,
    STATUS_REJECTED,
    STATUS_SUBMITTED,
    AmountChangeDenied,
    ApprovalDenied,
    decide_amount_change,
    decide_approval,
    decide_rejection,
)
from apps.approvals.logic import LEVEL_MANAGER as M, LEVEL_FINANCE as F
from apps.accounts.models import Profile
from apps.payment_requests.models import PaymentRequest


class DecideApprovalTests(SimpleTestCase):

    def _decide(self, **overrides):
        """A valid manager-approves-a-small-pending-request call, tweak per test."""
        base = dict(
            role=Profile.MANAGER,
            is_requester=False,
            status=STATUS_PENDING,
            amount=24_500,
            approved_levels=set(),
        )
        base.update(overrides)
        return decide_approval(**base)

    # --- <=50k: one manager completes it ---
    def test_manager_approval_completes_small_request(self):
        d = self._decide()
        self.assertEqual(d.level, M)
        self.assertEqual(d.new_status, STATUS_APPROVED)

    # --- >50k: two separate approvals, either order ---
    def test_manager_alone_leaves_large_request_pending(self):
        d = self._decide(amount=67_000)
        self.assertEqual(d.level, M)
        self.assertEqual(d.new_status, STATUS_PENDING)

    def test_finance_after_manager_completes_large_request(self):
        d = self._decide(role=Profile.FINANCE, amount=148_000, approved_levels={M})
        self.assertEqual(d.level, F)
        self.assertEqual(d.new_status, STATUS_APPROVED)

    def test_finance_first_leaves_large_request_pending(self):
        d = self._decide(role=Profile.FINANCE, amount=67_000)
        self.assertEqual(d.level, F)
        self.assertEqual(d.new_status, STATUS_PENDING)

    def test_manager_after_finance_completes_large_request(self):
        d = self._decide(amount=67_000, approved_levels={F})
        self.assertEqual(d.new_status, STATUS_APPROVED)

    # --- Audit details describe what happened ---
    def test_audit_details_recorded(self):
        d = self._decide(amount=67_000)
        self.assertEqual(d.audit_action, AUDIT_APPROVED)
        self.assertEqual(
            d.audit_details,
            {"level": M, "amount": "67000", "fully_approved": False},
        )

    # --- Refusals surface as ApprovalDenied with the logic.py reason ---
    def assertDenied(self, reason, **overrides):
        with self.assertRaises(ApprovalDenied) as ctx:
            self._decide(**overrides)
        self.assertEqual(ctx.exception.reason, reason)

    def test_employee_denied(self):
        self.assertDenied(logic.MSG_ROLE_CANNOT, role=Profile.EMPLOYEE)

    def test_self_approval_denied(self):
        self.assertDenied(logic.MSG_SELF_APPROVAL, is_requester=True)

    def test_non_pending_status_denied(self):
        self.assertDenied(logic.MSG_NOT_PENDING, status=STATUS_APPROVED)

    def test_finance_on_small_request_denied(self):
        self.assertDenied(logic.MSG_NOT_REQUIRED, role=Profile.FINANCE)

    def test_second_manager_approval_denied(self):
        self.assertDenied(
            logic.MSG_ALREADY_APPROVED, amount=67_000, approved_levels={M}
        )


class DecideRejectionTests(SimpleTestCase):

    def _decide(self, **overrides):
        """A valid manager-rejects-a-small-pending-request call, tweak per test."""
        base = dict(
            role=Profile.MANAGER,
            is_requester=False,
            status=STATUS_PENDING,
            amount=42_000,
            approved_levels=set(),
            reason="Duplicate invoice",
        )
        base.update(overrides)
        return decide_rejection(**base)

    def assertDenied(self, expected, **overrides):
        # "expected", not "reason": overrides may contain the rejection reason.
        with self.assertRaises(ApprovalDenied) as ctx:
            self._decide(**overrides)
        self.assertEqual(ctx.exception.reason, expected)

    # --- Allowed paths ---
    def test_manager_rejects_small_request(self):
        d = self._decide()
        self.assertEqual(d.level, M)
        self.assertEqual(d.new_status, STATUS_REJECTED)
        self.assertEqual(d.reason, "Duplicate invoice")

    def test_finance_rejects_large_request_after_manager_approved(self):
        # One rejection ends the request even though manager already approved.
        d = self._decide(role=Profile.FINANCE, amount=148_000, approved_levels={M})
        self.assertEqual(d.level, F)
        self.assertEqual(d.new_status, STATUS_REJECTED)

    def test_reason_is_trimmed(self):
        d = self._decide(reason="  Wrong vendor  ")
        self.assertEqual(d.reason, "Wrong vendor")

    def test_audit_details_include_reason(self):
        d = self._decide()
        self.assertEqual(d.audit_action, AUDIT_REJECTED)
        self.assertEqual(
            d.audit_details,
            {"level": M, "amount": "42000", "reason": "Duplicate invoice"},
        )

    # --- Rule: rejections must include a reason ---
    def test_empty_reason_denied(self):
        self.assertDenied(MSG_REASON_REQUIRED, reason="")

    def test_whitespace_reason_denied(self):
        self.assertDenied(MSG_REASON_REQUIRED, reason="   ")

    def test_missing_reason_denied(self):
        self.assertDenied(MSG_REASON_REQUIRED, reason=None)

    # --- Same gate as approving ---
    def test_employee_cannot_reject(self):
        self.assertDenied(logic.MSG_ROLE_CANNOT, role=Profile.EMPLOYEE)

    def test_cannot_reject_own_request(self):
        self.assertDenied(logic.MSG_SELF_APPROVAL, is_requester=True)

    def test_cannot_reject_non_pending_request(self):
        self.assertDenied(logic.MSG_NOT_PENDING, status=STATUS_APPROVED)

    def test_finance_cannot_reject_small_request(self):
        self.assertDenied(logic.MSG_NOT_REQUIRED, role=Profile.FINANCE)

    def test_permission_checked_before_reason(self):
        # An employee with no reason is told about their role, not the reason.
        self.assertDenied(logic.MSG_ROLE_CANNOT, role=Profile.EMPLOYEE, reason="")


class DecideAmountChangeTests(SimpleTestCase):

    def _decide(self, **overrides):
        """An approved 42,000 request changed to 45,000 (same tier), tweak per test."""
        base = dict(
            is_requester=True,
            status=STATUS_APPROVED,
            old_amount=42_000,
            new_amount=45_000,
            approved_levels={M},
        )
        base.update(overrides)
        return decide_amount_change(**base)

    # --- Rule: a change after approval invalidates approvals -> PENDING ---
    def test_change_after_approval_supersedes_and_returns_to_pending(self):
        d = self._decide()
        self.assertEqual(d.supersede_levels, {M})
        self.assertEqual(d.new_status, STATUS_PENDING)
        self.assertFalse(d.retiered)

    def test_decrease_also_forces_reapproval(self):
        d = self._decide(new_amount=40_000)
        self.assertEqual(d.supersede_levels, {M})
        self.assertEqual(d.new_status, STATUS_PENDING)

    def test_partial_approval_on_pending_request_is_superseded(self):
        d = self._decide(
            status=STATUS_PENDING, old_amount=67_000, new_amount=70_000,
            approved_levels={M},
        )
        self.assertEqual(d.supersede_levels, {M})
        self.assertEqual(d.new_status, STATUS_PENDING)

    def test_unchanged_amount_does_nothing(self):
        self.assertIsNone(self._decide(new_amount="42000.00"))

    # --- Re-tiering across 50k ---
    def test_crossing_up_over_50k_is_retiered(self):
        self.assertTrue(self._decide(new_amount=67_000).retiered)

    def test_crossing_down_under_50k_is_retiered(self):
        d = self._decide(old_amount=148_000, new_amount=24_500,
                         approved_levels={M, F})
        self.assertTrue(d.retiered)
        self.assertEqual(d.supersede_levels, {M, F})

    def test_full_flow_reapproval_after_crossing_50k(self):
        # 42,000 approved by a manager, then raised to 67,000.
        change = self._decide(new_amount=67_000)
        remaining = {M} - change.supersede_levels      # approvals still counting
        # Manager re-approves: not enough any more, finance now required.
        first = decide_approval(role=Profile.MANAGER, is_requester=False,
                                status=change.new_status, amount=67_000,
                                approved_levels=remaining)
        self.assertEqual(first.new_status, STATUS_PENDING)
        second = decide_approval(role=Profile.FINANCE, is_requester=False,
                                 status=first.new_status, amount=67_000,
                                 approved_levels=remaining | {first.level})
        self.assertEqual(second.new_status, STATUS_APPROVED)

    # --- Draft: nothing to invalidate ---
    def test_draft_change_keeps_draft_status(self):
        d = self._decide(status=STATUS_DRAFT, approved_levels=set())
        self.assertEqual(d.supersede_levels, frozenset())
        self.assertEqual(d.new_status, STATUS_DRAFT)

    # --- Submitted: not yet approved, so also nothing to invalidate ---
    def test_submitted_change_keeps_submitted_status(self):
        d = self._decide(status=STATUS_SUBMITTED, approved_levels=set())
        self.assertEqual(d.supersede_levels, frozenset())
        self.assertEqual(d.new_status, STATUS_SUBMITTED)

    # --- Locked statuses ---
    def test_rejected_request_amount_is_locked(self):
        with self.assertRaises(AmountChangeDenied) as ctx:
            self._decide(status=STATUS_REJECTED)
        self.assertEqual(ctx.exception.reason, MSG_AMOUNT_LOCKED)

    def test_paid_request_amount_is_locked(self):
        with self.assertRaises(AmountChangeDenied):
            self._decide(status=PaymentRequest.PAID)

    # --- Audit ---
    def test_audit_details_record_old_and_new(self):
        d = self._decide(new_amount=67_000)
        self.assertEqual(d.audit_action, AUDIT_AMOUNT_CHANGED)
        self.assertEqual(d.audit_details, {
            "old_amount": "42000",
            "new_amount": "67000",
            "superseded_levels": [M],
            "retiered": True,
        })

    # --- Rule: only the requester may change the amount ---
    def test_only_the_requester_can_change_the_amount(self):
        with self.assertRaises(AmountChangeDenied) as ctx:
            self._decide(is_requester=False)
        self.assertEqual(ctx.exception.reason, MSG_NOT_REQUESTER_EDIT)

    def test_requester_check_comes_first(self):
        # Even a locked request or an unchanged amount says "not yours" first.
        for overrides in ({"status": STATUS_REJECTED}, {"new_amount": 42_000}):
            with self.subTest(**overrides):
                with self.assertRaises(AmountChangeDenied) as ctx:
                    self._decide(is_requester=False, **overrides)
                self.assertEqual(ctx.exception.reason, MSG_NOT_REQUESTER_EDIT)
