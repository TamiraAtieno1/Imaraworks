"""Mock payment gateway for development and testing.

Behavior:
- If `description` contains "FAIL" (case-insensitive), returns FAILED with reason.
- Otherwise returns SUCCESS with a generated reference.
- Stateless and thread-safe (no internal cache; idempotency handled by service layer).
"""
from decimal import Decimal

from .gateways import ChargeResult, PaymentGateway, register_gateway


class MockPaymentGateway(PaymentGateway):
    """Mock gateway that succeeds unless description contains 'FAIL'."""

    def charge(
        self,
        *,
        amount: Decimal,
        currency: str,
        reference: str,
        description: str,
        idempotency_key: str,
    ) -> ChargeResult:
        if "FAIL" in description.upper():
            return ChargeResult(
                reference=reference,
                status="FAILED",
                reason="Mock decline triggered by 'FAIL' in description",
            )
        return ChargeResult(
            reference=f"MOCK-{reference}",
            status="SUCCESS",
        )


# Register on import
register_gateway("mock", MockPaymentGateway)
