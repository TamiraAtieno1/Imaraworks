from decimal import Decimal

from rest_framework import serializers

from apps.payment_requests.serializers import PaymentRequestSerializer

from .services import approval_summary


class QueueItemSerializer(PaymentRequestSerializer):
    """A payment request plus its ``approval`` block.

    Extends Part A's serializer instead of listing fields again, so a queue item
    always has the same fields as GET /api/payment-requests/<id>/.
    """

    approval = serializers.SerializerMethodField()

    def get_approval(self, obj):
        return approval_summary(obj)


class AmountChangeSerializer(serializers.Serializer):
    """Body of PATCH /api/payment-requests/<id>/amount/: just the new amount.

    Same field limits as PaymentRequest.amount (12 digits, 2 decimal places)
    and the same "greater than zero" rule and message as Part A's create
    serializer, so an amount valid on create is valid on edit and vice versa.
    """

    amount = serializers.DecimalField(max_digits=12, decimal_places=2)

    def validate_amount(self, value: Decimal) -> Decimal:
        if value <= 0:
            raise serializers.ValidationError("Amount must be greater than zero.")
        return value
