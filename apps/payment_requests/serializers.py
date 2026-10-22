from decimal import Decimal

from rest_framework import serializers

from apps.vendors.models import Vendor
from .models import PaymentRequest, Project


class PaymentRequestSerializer(serializers.ModelSerializer):
    vendor_name = serializers.CharField(source='vendor.name', read_only=True)
    project_name = serializers.CharField(source='project.name', read_only=True)
    requester_username = serializers.CharField(source='requester.username', read_only=True)
    requester_name = serializers.SerializerMethodField()
    approval = serializers.SerializerMethodField()

    class Meta:
        model = PaymentRequest
        fields = '__all__'
        read_only_fields = ['requester', 'status', 'created_at', 'submitted_at', 'updated_at']

    def get_requester_name(self, obj: PaymentRequest) -> str:
        return obj.requester.get_full_name() or obj.requester.username

    def get_approval(self, obj: PaymentRequest) -> dict:
        # Imported here, not at the top: apps.approvals already imports this
        # module, so a top-level import would be circular.
        from apps.approvals.services import approval_summary
        return approval_summary(obj)

    def validate_amount(self, value: Decimal) -> Decimal:
        if value <= 0:
            raise serializers.ValidationError("Amount must be greater than zero.")
        return value

    def validate_invoice_number(self, value: str) -> str:
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Invoice number is required.")
        return value

    def validate_vendor(self, value: Vendor) -> Vendor:
        if not value.is_active:
            raise serializers.ValidationError("This vendor is no longer active.")
        return value

class ProjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = Project
        fields = ['id', 'name']