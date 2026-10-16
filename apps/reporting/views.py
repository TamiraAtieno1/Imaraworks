from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Profile
from apps.accounts.permissions import get_role
from apps.payment_requests.views import visible_requests

from .services import summary

# Roles that can see payment requests. ADMIN is left out on purpose: the team
# agreed admins don't see financial data (they get the redacted audit log instead).
CAN_VIEW_REPORTS: tuple[str, ...] = (Profile.EMPLOYEE, Profile.MANAGER, Profile.FINANCE)


class CanViewReports(BasePermission):
    message = "Your role cannot view reports."

    def has_permission(self, request, view):
        return get_role(request.user) in CAN_VIEW_REPORTS


class ReportSummary(APIView):
    """GET /api/reports/summary/: totals over the requests this user can see.

    Managers and finance see every request, employees only their own,
    exactly as on GET /api/payment-requests/.
    """

    permission_classes = [IsAuthenticated, CanViewReports]

    def get(self, request):
        return Response(summary(visible_requests(request.user)))
