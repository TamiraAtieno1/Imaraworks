from rest_framework import serializers
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Profile
from apps.accounts.permissions import get_role
from apps.payment_requests.models import PaymentRequest
from apps.payment_requests.views import visible_requests

from .services import filter_requests, summary

# Roles that can see payment requests. ADMIN is left out on purpose: the team
# agreed admins don't see financial data (they get the redacted audit log instead).
CAN_VIEW_REPORTS: tuple[str, ...] = (Profile.EMPLOYEE, Profile.MANAGER, Profile.FINANCE)


class CanViewReports(BasePermission):
    message = "Your role cannot view reports."

    def has_permission(self, request, view):
        return get_role(request.user) in CAN_VIEW_REPORTS


class ReportFilters(serializers.Serializer):
    """Optional query parameters. An unknown value is a 400, not silently ignored,
    so a typo like ?status=paid can't quietly return totals for everything."""

    status = serializers.ChoiceField(choices=PaymentRequest.STATUS_CHOICES, required=False)
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)
    paid_from = serializers.DateField(required=False)
    paid_to = serializers.DateField(required=False)

    def validate(self, attrs):
        # A reversed range is almost certainly a mistake; an empty report would hide it.
        for start, end in (('date_from', 'date_to'), ('paid_from', 'paid_to')):
            if start in attrs and end in attrs and attrs[start] > attrs[end]:
                raise serializers.ValidationError({end: f'Must be on or after {start}.'})
        return attrs


class ReportSummary(APIView):
    """GET /api/reports/summary/: totals over the requests this user can see.

    Managers and finance see every request, employees only their own,
    exactly as on GET /api/payment-requests/. Filters apply after that.
    """

    permission_classes = [IsAuthenticated, CanViewReports]

    def get(self, request):
        filters = ReportFilters(data=request.query_params)
        filters.is_valid(raise_exception=True)
        qs = filter_requests(visible_requests(request.user), **filters.validated_data)
        return Response(summary(qs))
