"""Payment API URLs."""
from django.urls import path

from .views import pay_payment_request, payment_transaction_list

urlpatterns = [
    path("payment-requests/<int:pk>/pay/", pay_payment_request, name="pay-payment-request"),
    path("transactions/", payment_transaction_list, name="payment-transaction-list"),
]
