from django.contrib import admin

from .models import PaymentGatewayConfig, PaymentTransaction


@admin.register(PaymentTransaction)
class PaymentTransactionAdmin(admin.ModelAdmin):
    list_display = ("pk", "request", "status", "amount", "currency", "gateway", "created_at", "completed_at")
    list_filter = ("status", "gateway", "currency")
    search_fields = ("request__invoice_number", "provider_reference", "idempotency_key")
    readonly_fields = ("created_at", "updated_at", "completed_at")
    ordering = ("-created_at",)


@admin.register(PaymentGatewayConfig)
class PaymentGatewayConfigAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "created_at", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("name",)
