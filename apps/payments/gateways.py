"""Payment gateway adapter pattern.

Defines the abstract base class and a registry for plugging in different providers.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class ChargeResult:
    """Result of a charge attempt."""
    reference: str
    status: str  # "SUCCESS" | "FAILED"
    reason: str | None = None


class PaymentGateway(ABC):
    """Abstract base class for payment providers.

    Implementations must be stateless and thread-safe. The service layer handles
    idempotency and transaction boundaries; the gateway only executes the charge.
    """

    @abstractmethod
    def charge(
        self,
        *,
        amount: Decimal,
        currency: str,
        reference: str,
        description: str,
        idempotency_key: str,
    ) -> ChargeResult:
        """Attempt a payment.

        Args:
            amount: Amount to charge.
            currency: ISO currency code (e.g., "KES").
            reference: Provider-facing reference (our PaymentTransaction.id).
            description: Human-readable description for the provider dashboard.
            idempotency_key: Unique key for this payment attempt; safe to retry.

        Returns:
            ChargeResult with reference, status ("SUCCESS" | "FAILED"), and optional reason.
        """
        ...


_GATEWAY_REGISTRY: dict[str, type[PaymentGateway]] = {}


def register_gateway(name: str, gateway_cls: type[PaymentGateway]) -> None:
    """Register a gateway implementation by name."""
    _GATEWAY_REGISTRY[name] = gateway_cls


def get_gateway(name: str) -> PaymentGateway:
    """Get a gateway instance by registered name.

    Raises KeyError if the name is not registered.
    """
    try:
        return _GATEWAY_REGISTRY[name]()
    except KeyError:
        raise KeyError(f"Unknown payment gateway: {name}. Registered: {list(_GATEWAY_REGISTRY)}")


# Default: mock gateway (registered in apps.py ready() so it runs after migrations)
