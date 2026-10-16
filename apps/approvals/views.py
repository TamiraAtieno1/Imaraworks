"""HTTP endpoints for approvals. See docs/APPROVALS_API.md.

These views only translate: HTTP in, a services.py call, HTTP out. Every
approval rule lives in logic.py / services.py (golden rule 2).
"""

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import get_role
from apps.payment_requests.views import visible_requests

from . import logic
from .serializers import QueueItemSerializer
from .services import (
    MSG_ALREADY_SUBMITTED,
    MSG_NOT_REQUESTER,
    MSG_REASON_REQUIRED,
    ApprovalDenied,
    SubmitDenied,
    approval_queue,
    approval_summary,
    approve,
    reject,
    submit,
)

# Which HTTP status each refusal becomes:
#   403 "not you"          -- the user can never do this
#   409 "not now"          -- the request's state changed; reload it
#   400 "fix your input"
DENIAL_STATUS = {
    logic.MSG_ROLE_CANNOT: status.HTTP_403_FORBIDDEN,
    logic.MSG_NOT_PENDING: status.HTTP_409_CONFLICT,
    logic.MSG_SELF_APPROVAL: status.HTTP_403_FORBIDDEN,
    logic.MSG_NOT_REQUIRED: status.HTTP_403_FORBIDDEN,
    logic.MSG_ALREADY_APPROVED: status.HTTP_409_CONFLICT,
    MSG_REASON_REQUIRED: status.HTTP_400_BAD_REQUEST,
    MSG_NOT_REQUESTER: status.HTTP_403_FORBIDDEN,
    MSG_ALREADY_SUBMITTED: status.HTTP_409_CONFLICT,
}


def denied_response(exc):
    return Response({"detail": exc.reason}, status=DENIAL_STATUS[exc.reason])


class IsApprover(BasePermission):
    message = logic.MSG_ROLE_CANNOT

    def has_permission(self, request, view):
        return logic.approver_level(get_role(request.user)) is not None


class ApprovalQueueView(APIView):
    permission_classes = [IsAuthenticated, IsApprover]

    def get(self, request):
        items = approval_queue(request.user)
        return Response(QueueItemSerializer(items, many=True).data)


class ApproveView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        # 404 first: a request you can't see doesn't exist as far as you know.
        payment_request = get_object_or_404(visible_requests(request.user), pk=pk)
        try:
            approve(payment_request, request.user)
        except ApprovalDenied as exc:
            return denied_response(exc)
        payment_request.refresh_from_db()
        return Response({
            "id": payment_request.id,
            "status": payment_request.status,
            "approval": approval_summary(payment_request),
        })


class RejectView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        payment_request = get_object_or_404(visible_requests(request.user), pk=pk)
        reason = request.data.get("reason")
        if not isinstance(reason, str):
            reason = None   # a number or list is treated as no reason
        try:
            decision = reject(payment_request, request.user, reason)
        except ApprovalDenied as exc:
            return denied_response(exc)
        payment_request.refresh_from_db()
        return Response({
            "id": payment_request.id,
            "status": payment_request.status,
            "reason": decision.reason,
            "approval": approval_summary(payment_request),
        })


class SubmitView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        # 404 first, as for approve: you can't submit what you can't see.
        payment_request = get_object_or_404(visible_requests(request.user), pk=pk)
        try:
            submit(payment_request, request.user)
        except SubmitDenied as exc:
            return denied_response(exc)
        payment_request.refresh_from_db()
        # The full request plus its approval block (asked for on PR #5), so the
        # page can update the row without fetching it again.
        return Response(QueueItemSerializer(payment_request).data)
