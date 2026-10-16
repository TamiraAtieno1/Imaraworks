"""Payment orchestration service.

Handles idempotency, locking, and transaction boundaries. The gateway is called
OUTSIDE the database transaction to avoid holding locks during external calls.
"""
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from apps.payment_requests.models import PaymentRequest
from apps.payments.gateways import ChargeResult, PaymentGateway, get_gateway
from apps.payments.models import PaymentTransaction


class PaymentDenied(Exception):
    """Raised when a payment cannot be initiated."""
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class PaymentGatewayError(Exception):
    """Raised when a gateway raises while charging.

    The failed attempt is committed as FAILED before this is raised, so the
    caller can report an infrastructure error without leaving the request
    stuck in PROCESSING.
    """
    def __init__(self, reason: str, transaction: PaymentTransaction | None = None):
        super().__init__(reason)
        self.reason = reason
        self.transaction = transaction


# Statuses that can transition to payment processing. FAILED is included so a
# declined attempt can be retried.
PAYABLE_STATUSES = {PaymentRequest.APPROVED, PaymentRequest.FAILED}


@dataclass(frozen=True)
class PaymentResult:
    """Result of a payment initiation."""
    transaction: PaymentTransaction
    replayed: bool  # True if this was a replay of an existing key
    charge_result: ChargeResult | None = None


def initiate_payment(
    *,
    payment_request: PaymentRequest,
    idempotency_key: str,
    gateway_name: str = "mock",
    currency: str = "KES",
    description: str = "",
) -> PaymentResult:
    """Initiate payment for an approved request.

    The amount charged is always the request's approved amount, read under the
    row lock; callers cannot override it.

    Idempotency:
    - If `idempotency_key` already exists for this request, return the existing
      transaction (replayed=True) without calling the gateway again.
    - If a different key is used for the same request, the DB constraint
      `one_successful_payment_per_request` prevents double SUCCESS.

    Concurrency:
    - The request row is locked with `select_for_update` while checking status
      and creating the PROCESSING transaction.
    - The gateway is called OUTSIDE the transaction to avoid holding locks.
    - A second transaction updates the result.

    Args:
        payment_request: The approved payment request.
        idempotency_key: Client-provided unique key (e.g., UUID). Must be unique per request.
        gateway_name: Registered gateway name (default: "mock").
        currency: ISO currency code (default: "KES").
        description: Description sent to gateway (can contain "FAIL" to trigger mock decline).

    Returns:
        PaymentResult with transaction, replayed flag, and charge result.

    Raises:
        PaymentDenied: If request is not payable, already paid, or key conflicts.
        PaymentGatewayError: If the gateway raises; the attempt is recorded FAILED first.
        KeyError: If `gateway_name` is not a registered gateway (no state is written).
    """
    # Resolve the gateway before writing anything. An unknown name raises KeyError
    # with no PROCESSING row created, so the request is never stranded.
    gateway: PaymentGateway = get_gateway(gateway_name)

    # --- Step 1: Lock request, check idempotency key FIRST, then status, create PROCESSING transaction ---
    with transaction.atomic():
        locked_request = PaymentRequest.objects.select_for_update().get(pk=payment_request.pk)

        # Check for existing idempotency key on THIS request (BEFORE status check)
        existing = PaymentTransaction.objects.filter(
            request=locked_request,
            idempotency_key=idempotency_key,
        ).first()
        if existing:
            # Replay: return existing transaction, don't call gateway again
            return PaymentResult(transaction=existing, replayed=True)

        # Check if already successfully paid (BEFORE status check - status might be PAID)
        if locked_request.payment_transactions.filter(status=PaymentTransaction.SUCCESS).exists():
            raise PaymentDenied("Request has already been paid successfully")

        # Check for in-flight payment (PROCESSING) to prevent concurrent attempts
        if locked_request.payment_transactions.filter(status=PaymentTransaction.PROCESSING).exists():
            raise PaymentDenied("A payment is already being processed for this request")

        if locked_request.status not in PAYABLE_STATUSES:
            raise PaymentDenied(f"Request is not payable (status: {locked_request.status})")

        # The approved amount is authoritative, read under the lock.
        charge_amount = locked_request.amount

        # Create PROCESSING transaction
        txn = PaymentTransaction.objects.create(
            request=locked_request,
            idempotency_key=idempotency_key,
            amount=charge_amount,
            currency=currency,
            status=PaymentTransaction.PROCESSING,
            gateway=gateway_name,
        )

        # Update request status to PROCESSING
        locked_request.status = PaymentRequest.PROCESSING
        locked_request.save(update_fields=["status", "updated_at"])

    # --- Step 2: Call gateway OUTSIDE the transaction ---
    # A gateway exception must not leave the attempt in PROCESSING: turn it into a
    # FAILED charge result, record it below, then signal 502 to the caller.
    gateway_error: str | None = None
    try:
        charge_result = gateway.charge(
            amount=charge_amount,
            currency=currency,
            reference=str(txn.pk),
            description=description or f"Request #{locked_request.pk} - {locked_request.vendor.name} - {locked_request.reason}",
            idempotency_key=idempotency_key,
        )
    except Exception as exc:  # noqa: BLE001 - any provider failure marks the attempt FAILED
        gateway_error = f"Gateway error: {exc}"
        charge_result = ChargeResult(reference=str(txn.pk), status="FAILED", reason=gateway_error)

    # --- Step 3: Update transaction with result in a new transaction ---
    with transaction.atomic():
        # Re-lock the transaction row
        txn = PaymentTransaction.objects.select_for_update().get(pk=txn.pk)

        if charge_result.status == "SUCCESS":
            txn.status = PaymentTransaction.SUCCESS
            txn.provider_reference = charge_result.reference
            txn.completed_at = timezone.now()

            # Update request to PAID
            locked_request = PaymentRequest.objects.select_for_update().get(pk=locked_request.pk)
            locked_request.status = PaymentRequest.PAID
            locked_request.save(update_fields=["status", "updated_at"])

        else:
            txn.status = PaymentTransaction.FAILED
            txn.provider_reason = charge_result.reason or "Provider declined"
            txn.completed_at = timezone.now()

            # Mark the request FAILED so Finance and the requester can see what
            # happened. FAILED is payable, so the request can still be retried.
            locked_request = PaymentRequest.objects.select_for_update().get(pk=locked_request.pk)
            locked_request.status = PaymentRequest.FAILED
            locked_request.save(update_fields=["status", "updated_at"])

        txn.save(update_fields=["status", "provider_reference", "provider_reason", "completed_at", "updated_at"])

    # The FAILED attempt is now committed; surface the infrastructure error.
    if gateway_error:
        raise PaymentGatewayError(gateway_error, transaction=txn)

    return PaymentResult(transaction=txn, replayed=False, charge_result=charge_result)
