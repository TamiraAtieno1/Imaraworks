"""
Report totals for payment requests.

The functions take a queryset instead of a user, so they only add things up:
who may see which requests is decided once, by the caller, using the same
rule as the request list (visible_requests). A report can therefore never
show a request the user couldn't open.
"""

from decimal import Decimal

from django.db.models import Count, QuerySet, Sum

from apps.payment_requests.models import PaymentRequest

ZERO = Decimal('0.00')


def _money(value) -> str:
    """A 2dp string such as "24500.00", the same format the request API uses.

    Sum() gives None when there are no rows, which is shown as "0.00".
    Strings, not floats, so no amount is ever rounded on the way to the browser.
    """
    return str((value or ZERO).quantize(ZERO))


def _totals(qs: QuerySet[PaymentRequest]) -> dict:
    agg = qs.aggregate(count=Count('id'), total=Sum('amount'))
    return {'count': agg['count'], 'total': _money(agg['total'])}


def totals_by_status(qs: QuerySet[PaymentRequest]) -> list[dict]:
    """Every status in lifecycle order, including ones with no requests,
    so a dashboard always has the same rows."""
    rows = {
        row['status']: row
        for row in qs.order_by().values('status').annotate(count=Count('id'), total=Sum('amount'))
    }
    return [
        {
            'status': status,
            'label': label,
            'count': rows.get(status, {}).get('count', 0),
            'total': _money(rows.get(status, {}).get('total')),
        }
        for status, label in PaymentRequest.STATUS_CHOICES
    ]


def _totals_by(qs: QuerySet[PaymentRequest], field: str) -> list[dict]:
    """Totals per project or vendor, biggest total first, then by name."""
    rows = (
        qs.order_by()
        .values(f'{field}_id', f'{field}__name')
        .annotate(count=Count('id'), total=Sum('amount'))
        .order_by('-total', f'{field}__name')
    )
    return [
        {
            'id': row[f'{field}_id'],
            'name': row[f'{field}__name'],
            'count': row['count'],
            'total': _money(row['total']),
        }
        for row in rows
    ]


def totals_by_project(qs: QuerySet[PaymentRequest]) -> list[dict]:
    return _totals_by(qs, 'project')


def totals_by_vendor(qs: QuerySet[PaymentRequest]) -> list[dict]:
    return _totals_by(qs, 'vendor')


def summary(qs: QuerySet[PaymentRequest]) -> dict:
    """The whole report: overall, the two figures a dashboard headlines, and the breakdowns."""
    return {
        'overall': _totals(qs),
        'pending_approval': _totals(qs.filter(status=PaymentRequest.PENDING_APPROVAL)),
        'paid': _totals(qs.filter(status=PaymentRequest.PAID)),
        'by_status': totals_by_status(qs),
        'by_project': totals_by_project(qs),
        'by_vendor': totals_by_vendor(qs),
    }
