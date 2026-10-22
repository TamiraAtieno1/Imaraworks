"""
Report totals for payment requests.

The functions take a queryset instead of a user, so they only add things up:
who may see which requests is decided once, by the caller, using the same
rule as the request list (visible_requests). A report can therefore never
show a request the user couldn't open.
"""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from django.db.models import Count, QuerySet, Sum

from apps.payment_requests.models import PaymentRequest
from apps.payments.models import PaymentTransaction

ZERO = Decimal('0.00')

# "Paid on 31 October" means 31 October in Kenya, where the money moves. The
# database stores UTC, so a payment at 01:00 on 1 November in Nairobi is
# 22:00 on 31 October in UTC and would land in the wrong month. Kenya has no
# daylight saving, so a fixed +3 offset is exact and needs no tz database.
REPORT_TZ = timezone(timedelta(hours=3), 'EAT')


def _money(value) -> str:
    """A 2dp string such as "24500.00", the same format the request API uses.

    Sum() gives None when there are no rows, which is shown as "0.00".
    Strings, not floats, so no amount is ever rounded on the way to the browser.
    """
    return str((value or ZERO).quantize(ZERO))


def filter_requests(
    qs: QuerySet[PaymentRequest],
    status: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    paid_from: date | None = None,
    paid_to: date | None = None,
) -> QuerySet[PaymentRequest]:
    """Narrow the requests before they are added up. None means "don't filter".

    Takes the already-visible queryset, so a filter can only narrow what the
    user may see, never widen it. Both date ranges are inclusive at both ends,
    so date_to=2026-10-31 includes 31 October.

    date_from/date_to use the request's own date ("requests raised in
    October"). paid_from/paid_to use when the successful payment completed
    ("money paid out in October"), so they only ever match paid requests.
    """
    if status is not None:
        qs = qs.filter(status=status)
    if date_from is not None:
        qs = qs.filter(date__gte=date_from)
    if date_to is not None:
        qs = qs.filter(date__lte=date_to)
    if paid_from is not None or paid_to is not None:
        qs = qs.filter(id__in=_paid_between(paid_from, paid_to))
    return qs


def _start_of_day(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=REPORT_TZ)


def _paid_between(paid_from: date | None, paid_to: date | None):
    """Ids of requests whose successful payment completed in the range (Kenyan days).

    A subquery (id__in) rather than a join, so the totals can never count a
    request twice, whatever transactions it has. Only SUCCESS counts: a failed
    attempt moved no money. paid_to is turned into "before midnight at the
    start of the next day", which includes the whole of the last day.
    """
    txns = PaymentTransaction.objects.filter(status=PaymentTransaction.SUCCESS)
    if paid_from is not None:
        txns = txns.filter(completed_at__gte=_start_of_day(paid_from))
    if paid_to is not None:
        txns = txns.filter(completed_at__lt=_start_of_day(paid_to + timedelta(days=1)))
    return txns.values('request_id')


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
