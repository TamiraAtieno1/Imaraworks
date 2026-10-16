from django.urls import path

from .views import ApprovalQueueView, ApproveView, RejectView, SubmitView

# Included at api/ (not api/payment-requests/) so this file doesn't touch
# Part A's payment_requests/urls.py.
urlpatterns = [
    path('approvals/queue/', ApprovalQueueView.as_view(), name='approval-queue'),
    path('payment-requests/<int:pk>/approve/', ApproveView.as_view(), name='payment-request-approve'),
    path('payment-requests/<int:pk>/reject/', RejectView.as_view(), name='payment-request-reject'),
    path('payment-requests/<int:pk>/submit/', SubmitView.as_view(), name='payment-request-submit'),
]
