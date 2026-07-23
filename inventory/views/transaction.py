import django_filters
from rest_framework import mixins, viewsets
from rest_framework.permissions import IsAuthenticated

from django_filters.rest_framework import DjangoFilterBackend

from inventory.models import Transaction
from inventory.serializers import TransactionSerializer


class TransactionFilterSet(django_filters.FilterSet):
    # AC-2: exact match on the item's own serial number, not icontains -
    # "filter by a specific serial number" means one item's history, not a
    # prefix search.
    serial_number = django_filters.CharFilter(
        field_name="serialized_item__serial_number", lookup_expr="exact"
    )
    # AC-3: matches Transaction.reference_number's own comment - exact
    # match against the stable "WO-<id>"/"PO-<id>" string written at
    # creation time.
    reference_number = django_filters.CharFilter(
        field_name="reference_number", lookup_expr="exact"
    )
    # AC-4: inclusive start/end date filters (date only, not datetime) over
    # created_at.
    date_from = django_filters.DateFilter(
        field_name="created_at", lookup_expr="date__gte"
    )
    date_to = django_filters.DateFilter(
        field_name="created_at", lookup_expr="date__lte"
    )

    class Meta:
        model = Transaction
        # AC-5/AC-6: django_filters.FilterSet ANDs every active filter
        # together for free, so transaction_type combines with any of the
        # above with no extra wiring.
        fields = ["transaction_type"]


class TransactionViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    # WRH-49 (US-024a) scopes list+filter only - transactions are written
    # internally by the actions that produce them (PurchaseOrderViewSet
    # .receive(), WorkOrderViewSet.complete()/.return_item()), never via a
    # direct create endpoint, and per PRD notes the immutability rule
    # (no edit/delete) is its own dedicated story (US-024b) - so this
    # viewset deliberately exposes list only.
    permission_classes = [IsAuthenticated]
    queryset = Transaction.objects.select_related(
        "serialized_item__product_type", "work_order", "user"
    ).all()
    serializer_class = TransactionSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = TransactionFilterSet
