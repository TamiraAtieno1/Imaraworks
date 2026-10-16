"""Tests for payment mock provider (Part C)."""
import threading
import time
from decimal import Decimal
from unittest import mock
from uuid import uuid4

from django.contrib.auth.models import User
from django.db import IntegrityError, OperationalError, transaction
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from apps.accounts.models import Profile
from apps.payment_requests.models import PaymentRequest, Project
from apps.payments.models import PaymentTransaction
from apps.payments.services import PaymentDenied, initiate_payment
from apps.vendors.models import Vendor


def make_user(name, role):
    user = User.objects.create_user(name, password="pw")
    Profile.objects.create(user=user, role=role)
    return user


def make_approved_request(requester, amount="24500.00", vendor_name=None):
    """Create an already-approved payment request.

    The request is created directly with status APPROVED: approval transitions
    are Part B's concern and are tested there. Part C only cares that a request
    is payable, so it does not depend on Part B's approve()/models.
    """
    vendor = Vendor.objects.create(name=vendor_name or f"Test Vendor {uuid4().hex[:8]}", service="Materials")
    project = Project.objects.create(name=f"Test Project {uuid4().hex[:8]}")
    return PaymentRequest.objects.create(
        vendor=vendor,
        project=project,
        requester=requester,
        amount=Decimal(amount),
        reason="Test payment",
        invoice_number=f"INV-{uuid4().hex[:8]}",
        payment_method=PaymentRequest.MPESA,
        date="2026-10-14",
        status=PaymentRequest.APPROVED,
    )


class MockGatewayDeclineTests(TestCase):
    """Test that mock gateway can be triggered to decline via description."""

    def setUp(self):
        self.alice = make_user("alice", Profile.EMPLOYEE)
        self.faith = make_user("faith", Profile.FINANCE)

    def test_mock_succeeds_by_default(self):
        req = make_approved_request(self.alice)
        key = str(uuid4())
        result = initiate_payment(payment_request=req, idempotency_key=key)
        self.assertEqual(result.transaction.status, PaymentTransaction.SUCCESS)
        self.assertTrue(result.charge_result.status == "SUCCESS")

    def test_mock_declines_when_description_contains_fail(self):
        req = make_approved_request(self.alice)
        key = str(uuid4())
        result = initiate_payment(
            payment_request=req,
            idempotency_key=key,
            description="Request #1 - Test Vendor - FAIL this payment",
        )
        self.assertEqual(result.transaction.status, PaymentTransaction.FAILED)
        self.assertEqual(result.charge_result.status, "FAILED")
        self.assertIn("FAIL", result.charge_result.reason.upper())

    def test_mock_decline_case_insensitive(self):
        req = make_approved_request(self.alice)
        key = str(uuid4())
        result = initiate_payment(
            payment_request=req,
            idempotency_key=key,
            description="please fail this one",
        )
        self.assertEqual(result.transaction.status, PaymentTransaction.FAILED)


class IdempotencyTests(TestCase):
    """Idempotency: same key = replay, different key on same request = blocked by DB."""

    def setUp(self):
        self.alice = make_user("alice", Profile.EMPLOYEE)

    def test_same_key_returns_existing_transaction(self):
        req = make_approved_request(self.alice)
        key = str(uuid4())

        result1 = initiate_payment(payment_request=req, idempotency_key=key)
        result2 = initiate_payment(payment_request=req, idempotency_key=key)

        self.assertTrue(result2.replayed)
        self.assertEqual(result1.transaction.pk, result2.transaction.pk)
        self.assertEqual(PaymentTransaction.objects.count(), 1)

    def test_different_key_same_request_blocked_by_constraint(self):
        """Two different keys for same request: second should fail due to one_success_per_request."""
        req = make_approved_request(self.alice)
        key1 = str(uuid4())
        key2 = str(uuid4())

        result1 = initiate_payment(payment_request=req, idempotency_key=key1)
        self.assertEqual(result1.transaction.status, PaymentTransaction.SUCCESS)

        # Second attempt with different key should be denied
        with self.assertRaises(PaymentDenied) as ctx:
            initiate_payment(payment_request=req, idempotency_key=key2)
        self.assertIn("already been paid", ctx.exception.reason)

    def test_key_reuse_on_different_request_allowed(self):
        """Same key used for different request is allowed (key scoped to request)."""
        req1 = make_approved_request(self.alice, vendor_name=f"Vendor1 {uuid4().hex[:8]}")
        req2 = make_approved_request(self.alice, vendor_name=f"Vendor2 {uuid4().hex[:8]}")
        key = str(uuid4())

        # Pay req1 with key
        result1 = initiate_payment(payment_request=req1, idempotency_key=key)
        self.assertEqual(result1.transaction.status, PaymentTransaction.SUCCESS)

        # Pay req2 with same key - should work (different request)
        result2 = initiate_payment(payment_request=req2, idempotency_key=key)
        self.assertEqual(result2.transaction.status, PaymentTransaction.SUCCESS)
        self.assertNotEqual(result1.transaction.pk, result2.transaction.pk)

    def test_failed_payment_can_be_retried_with_new_key(self):
        """FAILED transaction doesn't block retry with new key."""
        req = make_approved_request(self.alice)
        key1 = str(uuid4())

        # First attempt fails
        result1 = initiate_payment(
            payment_request=req,
            idempotency_key=key1,
            description="FAIL",
        )
        self.assertEqual(result1.transaction.status, PaymentTransaction.FAILED)

        # Retry with new key should succeed
        key2 = str(uuid4())
        result2 = initiate_payment(payment_request=req, idempotency_key=key2)
        self.assertEqual(result2.transaction.status, PaymentTransaction.SUCCESS)


class ConcurrencyTests(TransactionTestCase):
    """Concurrent payment attempts should not double-charge.

    Note: SQLite has limitations with concurrent writes. This test demonstrates
    the intent; in production with PostgreSQL, the row locking works correctly.
    """

    def setUp(self):
        self.alice = make_user("alice", Profile.EMPLOYEE)
        self.faith = make_user("faith", Profile.FINANCE)

    def test_concurrent_pay_calls_single_charge(self):
        """Two threads calling pay simultaneously with different keys -> at most one SUCCESS."""
        req = make_approved_request(self.alice)
        results = []
        errors = []

        def pay(key):
            # Small stagger to reduce SQLite lock contention in test
            time.sleep(0.01)
            try:
                result = initiate_payment(payment_request=req, idempotency_key=key)
                results.append(result)
            except (PaymentDenied, OperationalError) as e:
                errors.append(e)

        key1 = str(uuid4())
        key2 = str(uuid4())

        t1 = threading.Thread(target=pay, args=(key1,))
        t2 = threading.Thread(target=pay, args=(key2,))

        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        # Key invariant: NEVER more than one SUCCESS (the "never pay twice" guarantee)
        success_count = sum(1 for r in results if r.transaction.status == PaymentTransaction.SUCCESS)
        denied_count = len(errors)

        # Every thread must end in exactly one of the two buckets: a result
        # (possibly FAILED) or a denial/lock error. Nothing silently disappears.
        self.assertEqual(success_count + denied_count, 2)

        # In SQLite, threads may hit lock errors; in Postgres, one succeeds, one denied
        # The critical invariant: at most one SUCCESS transaction
        self.assertLessEqual(success_count, 1, f"Expected at most 1 success, got {success_count}")
        self.assertLessEqual(
            PaymentTransaction.objects.filter(status=PaymentTransaction.SUCCESS).count(),
            1,
            "DB must never have more than one SUCCESS per request"
        )


class PaymentViewTests(TestCase):
    """API view tests: permissions, idempotency, decline flow."""

    def setUp(self):
        self.alice = make_user("alice", Profile.EMPLOYEE)
        self.faith = make_user("faith", Profile.FINANCE)
        self.client = APIClient()

    def _auth(self, user):
        self.client.force_authenticate(user)

    def test_finance_can_pay(self):
        req = make_approved_request(self.alice)
        self._auth(self.faith)
        response = self.client.post(f"/api/payments/payment-requests/{req.pk}/pay/")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], "SUCCESS")

    def test_employee_cannot_pay(self):
        req = make_approved_request(self.alice)
        self._auth(self.alice)
        response = self.client.post(f"/api/payments/payment-requests/{req.pk}/pay/")
        self.assertEqual(response.status_code, 403)

    def test_manager_cannot_pay(self):
        req = make_approved_request(self.alice)
        mgr = make_user(f"mgr_{uuid4().hex[:6]}", Profile.MANAGER)
        self._auth(mgr)
        response = self.client.post(f"/api/payments/payment-requests/{req.pk}/pay/")
        self.assertEqual(response.status_code, 403)

    def test_admin_cannot_pay(self):
        req = make_approved_request(self.alice)
        admin = make_user(f"admin_{uuid4().hex[:6]}", Profile.ADMIN)
        self._auth(admin)
        response = self.client.post(f"/api/payments/payment-requests/{req.pk}/pay/")
        self.assertEqual(response.status_code, 403)

    def test_unauthenticated_cannot_pay(self):
        req = make_approved_request(self.alice)
        response = self.client.post(f"/api/payments/payment-requests/{req.pk}/pay/")
        self.assertEqual(response.status_code, 401)

    def test_idempotency_key_replay(self):
        """Same key returns existing transaction with replayed=True."""
        req = make_approved_request(self.alice)
        self._auth(self.faith)
        key = str(uuid4())

        r1 = self.client.post(f"/api/payments/payment-requests/{req.pk}/pay/", {"idempotency_key": key})
        self.assertEqual(r1.status_code, 201)
        self.assertFalse(r1.data["replayed"])

        r2 = self.client.post(f"/api/payments/payment-requests/{req.pk}/pay/", {"idempotency_key": key})
        self.assertEqual(r2.status_code, 200)
        self.assertTrue(r2.data["replayed"])
        self.assertEqual(r1.data["id"], r2.data["id"])

    def test_different_key_same_request_denied(self):
        """Different key on already-paid request returns 409 with 'already been paid'."""
        req = make_approved_request(self.alice)
        self._auth(self.faith)

        r1 = self.client.post(f"/api/payments/payment-requests/{req.pk}/pay/", {"idempotency_key": str(uuid4())})
        self.assertEqual(r1.status_code, 201)

        r2 = self.client.post(f"/api/payments/payment-requests/{req.pk}/pay/", {"idempotency_key": str(uuid4())})
        self.assertEqual(r2.status_code, 409)
        self.assertIn("already been paid", r2.data["detail"])

    def test_mock_decline_via_api(self):
        """FAIL in description triggers mock decline and the request is marked FAILED."""
        req = make_approved_request(self.alice)
        self._auth(self.faith)

        response = self.client.post(
            f"/api/payments/payment-requests/{req.pk}/pay/",
            {"description": "FAIL this payment", "idempotency_key": str(uuid4())}
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], "FAILED")
        self.assertIn("FAIL", response.data["provider_reason"].upper())

        # Request is FAILED so the failure is visible (and FAILED is retryable)
        req.refresh_from_db()
        self.assertEqual(req.status, PaymentRequest.FAILED)

    def test_retry_from_failed_marks_request_paid(self):
        """A retry from FAILED with a new key succeeds and moves the request to PAID."""
        req = make_approved_request(self.alice)
        self._auth(self.faith)

        r1 = self.client.post(
            f"/api/payments/payment-requests/{req.pk}/pay/",
            {"description": "FAIL", "idempotency_key": str(uuid4())}
        )
        self.assertEqual(r1.data["status"], "FAILED")
        req.refresh_from_db()
        self.assertEqual(req.status, PaymentRequest.FAILED)

        r2 = self.client.post(
            f"/api/payments/payment-requests/{req.pk}/pay/",
            {"idempotency_key": str(uuid4())}
        )
        self.assertEqual(r2.status_code, 201)
        self.assertEqual(r2.data["status"], "SUCCESS")
        req.refresh_from_db()
        self.assertEqual(req.status, PaymentRequest.PAID)

    def test_retry_after_decline(self):
        """After FAILED, new payment with new key succeeds."""
        req = make_approved_request(self.alice)
        self._auth(self.faith)

        # First attempt fails
        r1 = self.client.post(
            f"/api/payments/payment-requests/{req.pk}/pay/",
            {"description": "FAIL", "idempotency_key": str(uuid4())}
        )
        self.assertEqual(r1.data["status"], "FAILED")

        # Retry succeeds
        r2 = self.client.post(
            f"/api/payments/payment-requests/{req.pk}/pay/",
            {"idempotency_key": str(uuid4())}
        )
        self.assertEqual(r2.status_code, 201)
        self.assertEqual(r2.data["status"], "SUCCESS")

    def test_non_payable_status_returns_409(self):
        """DRAFT, SUBMITTED, PENDING_APPROVAL, REJECTED -> 409."""
        for status in [PaymentRequest.DRAFT, PaymentRequest.SUBMITTED, PaymentRequest.PENDING_APPROVAL, PaymentRequest.REJECTED]:
            req = PaymentRequest.objects.create(
                vendor=Vendor.objects.create(name=f"V_{uuid4().hex[:8]}", service="S"),
                project=Project.objects.create(name=f"P_{uuid4().hex[:8]}"),
                requester=self.alice,
                amount=Decimal(10000),
                reason="Test",
                invoice_number=f"INV-{uuid4().hex[:8]}",
                payment_method=PaymentRequest.MPESA,
                date="2026-10-14",
                status=status,
            )
            self._auth(self.faith)
            response = self.client.post(f"/api/payments/payment-requests/{req.pk}/pay/")
            self.assertEqual(response.status_code, 409, f"Status {status} should be 409")

    def test_payment_transaction_list_finance_only(self):
        """Finance can list transactions; others get 403."""
        req = make_approved_request(self.alice)
        self._auth(self.faith)
        self.client.post(f"/api/payments/payment-requests/{req.pk}/pay/")

        self._auth(self.faith)
        r = self.client.get("/api/payments/transactions/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.data), 1)

        self._auth(self.alice)
        r = self.client.get("/api/payments/transactions/")
        self.assertEqual(r.status_code, 403)


class AmountOverrideTests(TestCase):
    """The approved amount is authoritative; the pay body cannot override it."""

    def setUp(self):
        self.alice = make_user("alice", Profile.EMPLOYEE)
        self.faith = make_user("faith", Profile.FINANCE)
        self.client = APIClient()

    def _auth(self, user):
        self.client.force_authenticate(user)

    def test_amount_in_body_is_rejected(self):
        """A request approved at 24,500 sending {"amount": "500000"} is rejected, not charged."""
        req = make_approved_request(self.alice, amount="24500.00")
        self._auth(self.faith)

        response = self.client.post(
            f"/api/payments/payment-requests/{req.pk}/pay/",
            {"amount": "500000"},
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("amount", response.data["detail"])

        # Nothing was written and the request is untouched.
        req.refresh_from_db()
        self.assertEqual(req.status, PaymentRequest.APPROVED)
        self.assertEqual(PaymentTransaction.objects.count(), 0)

    def test_charged_amount_is_the_approved_amount(self):
        """The transaction records (and the gateway is charged) the approved amount."""
        req = make_approved_request(self.alice, amount="24500.00")
        self._auth(self.faith)

        response = self.client.post(f"/api/payments/payment-requests/{req.pk}/pay/")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["amount"], "24500.00")
        self.assertEqual(Decimal(response.data["amount"]), Decimal("24500.00"))


class UnknownGatewayTests(TestCase):
    """An unknown gateway must fail cleanly without stranding the request."""

    def setUp(self):
        self.alice = make_user("alice", Profile.EMPLOYEE)
        self.faith = make_user("faith", Profile.FINANCE)
        self.client = APIClient()
        self._auth(self.faith)

    def _auth(self, user):
        self.client.force_authenticate(user)

    def test_unknown_gateway_returns_422_and_leaves_request_approved(self):
        req = make_approved_request(self.alice)
        response = self.client.post(
            f"/api/payments/payment-requests/{req.pk}/pay/",
            {"gateway": "nope"},
        )
        self.assertEqual(response.status_code, 422)

        req.refresh_from_db()
        self.assertEqual(req.status, PaymentRequest.APPROVED)
        self.assertFalse(
            PaymentTransaction.objects.filter(request=req).exists(),
            "No transaction should be created for an unknown gateway",
        )

    def test_retry_after_unknown_gateway_is_not_blocked(self):
        """Regression: unknown gateway must not leave a PROCESSING row that 409s retries."""
        req = make_approved_request(self.alice)

        bad = self.client.post(
            f"/api/payments/payment-requests/{req.pk}/pay/",
            {"gateway": "nope"},
        )
        self.assertEqual(bad.status_code, 422)

        good = self.client.post(
            f"/api/payments/payment-requests/{req.pk}/pay/",
            {"gateway": "mock"},
        )
        self.assertEqual(good.status_code, 201)
        self.assertEqual(good.data["status"], "SUCCESS")


class _BoomGateway:
    """Stub gateway whose charge always raises, to simulate a provider outage."""

    def charge(self, **kwargs):
        raise RuntimeError("provider unreachable")


class GatewayExceptionTests(TestCase):
    """A gateway exception records a FAILED attempt instead of stranding PROCESSING."""

    def setUp(self):
        self.alice = make_user("alice", Profile.EMPLOYEE)
        self.faith = make_user("faith", Profile.FINANCE)
        self.client = APIClient()
        self.client.force_authenticate(self.faith)

    def test_gateway_exception_returns_502_and_marks_failed(self):
        req = make_approved_request(self.alice)

        with mock.patch("apps.payments.services.get_gateway", return_value=_BoomGateway()):
            response = self.client.post(f"/api/payments/payment-requests/{req.pk}/pay/")

        self.assertEqual(response.status_code, 502)
        self.assertIn("transaction", response.data)

        # The attempt is FAILED (never PROCESSING) and the request shows FAILED.
        txn = PaymentTransaction.objects.get(request=req)
        self.assertEqual(txn.status, PaymentTransaction.FAILED)
        self.assertIn("provider unreachable", txn.provider_reason)

        req.refresh_from_db()
        self.assertEqual(req.status, PaymentRequest.FAILED)

    def test_retry_after_gateway_exception_succeeds(self):
        req = make_approved_request(self.alice)

        with mock.patch("apps.payments.services.get_gateway", return_value=_BoomGateway()):
            first = self.client.post(f"/api/payments/payment-requests/{req.pk}/pay/")
        self.assertEqual(first.status_code, 502)

        second = self.client.post(f"/api/payments/payment-requests/{req.pk}/pay/")
        self.assertEqual(second.status_code, 201)
        self.assertEqual(second.data["status"], "SUCCESS")


class PaymentTransactionModelTests(TestCase):
    """Model constraints and behavior."""

    def setUp(self):
        self.alice = make_user("alice", Profile.EMPLOYEE)
        self.faith = make_user("faith", Profile.FINANCE)

    def test_one_success_per_request_constraint(self):
        """DB constraint prevents two SUCCESS transactions for same request."""
        req = make_approved_request(self.alice)
        key1 = str(uuid4())
        key2 = str(uuid4())

        PaymentTransaction.objects.create(
            request=req, idempotency_key=key1, amount=Decimal(24500),
            status=PaymentTransaction.SUCCESS, provider_reference="REF1"
        )

        # Second SUCCESS should violate constraint
        with self.assertRaises(IntegrityError), transaction.atomic():
            PaymentTransaction.objects.create(
                request=req, idempotency_key=key2, amount=Decimal(24500),
                status=PaymentTransaction.SUCCESS, provider_reference="REF2"
            )

    def test_unique_idempotency_key_per_request(self):
        """Same (request, key) pair violates unique constraint."""
        req = make_approved_request(self.alice)
        key = str(uuid4())

        PaymentTransaction.objects.create(
            request=req, idempotency_key=key, amount=Decimal(24500),
            status=PaymentTransaction.PROCESSING
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            PaymentTransaction.objects.create(
                request=req, idempotency_key=key, amount=Decimal(24500),
                status=PaymentTransaction.PROCESSING
            )
