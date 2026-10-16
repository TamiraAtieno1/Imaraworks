from django.urls import path

from .views import AmountChangeView, ApprovalQueueView, ApproveView, RejectView

# Included at api/ (not api/payment-requests/) so this file doesn't touch
# Part A's payment_requests/urls.py.
urlpatterns = [
    path('approvals/queue/', ApprovalQueueView.as_view(), name='approval-queue'),
    path('payment-requests/<int:pk>/approve/', ApproveView.as_view(), name='payment-request-approve'),
    path('payment-requests/<int:pk>/reject/', RejectView.as_view(), name='payment-request-reject'),
    path('payment-requests/<int:pk>/amount/', AmountChangeView.as_view(), name='payment-request-amount'),
]
