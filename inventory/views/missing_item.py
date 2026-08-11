from django.db import transaction
from django.db.models import Max, Q
from django.http import Http404
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from inventory.models import SerializedItem, Transaction
from inventory.models.serialized_item import DATE_MISSING_ANNOTATION
from inventory.serializers import MissingItemSerializer, SerializedItemSerializer


class MissingItemViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    # AC-1: list-only - resolution (AC-3/AC-4) happens through the two
    # detail actions below rather than a generic update/destroy, since
    # neither "found" nor "written off" is a plain field edit.
    permission_classes = [IsAuthenticated]
    serializer_class = MissingItemSerializer

    def get_queryset(self):
        # select_related avoids an N+1 for product_type_name/work_order_* per
        # row (matches SerializedItemViewSet's own product_type
        # select_related). The Max() annotation folds "most recent MISSING
        # transaction for this item" into the same query rather than one
        # extra query per row - matches ProductType's *_COUNT_ANNOTATION
        # convention (see DATE_MISSING_ANNOTATION's own comment).
        return (
            SerializedItem.objects.filter(status=SerializedItem.STATUS_MISSING)
            .select_related("product_type", "work_order_line_item__work_order")
            .annotate(
                **{
                    DATE_MISSING_ANNOTATION: Max(
                        "transactions__created_at",
                        filter=Q(
                            transactions__transaction_type=Transaction.TYPE_MISSING
                        ),
                    )
                }
            )
        )

    def _get_locked_missing_item(self, pk):
        # WRH-28 lesson: select_for_update() paired with select_related()
        # across a nullable FK (work_order_line_item) breaks on Postgres -
        # lock the bare row only; the rare "not missing" rejection path
        # below is the only place that reads the FK, so it costs one lazy
        # follow-up query only when actually hit.
        try:
            return SerializedItem.objects.select_for_update().get(pk=pk)
        except SerializedItem.DoesNotExist:
            raise Http404

    @action(detail=True, methods=["post"], url_path="mark-found")
    def mark_found(self, request, pk=None):
        # AC-3: a return transaction is created, status -> available. The
        # item's WO is already closed by the time it can go missing (only
        # close() ever sets STATUS_MISSING) - matches return_item()'s "don't
        # clear work_order_line_item" convention, no WO status re-derivation
        # needed since a closed WO stays closed.
        with transaction.atomic():
            item = self._get_locked_missing_item(pk)
            if item.status != SerializedItem.STATUS_MISSING:
                raise ValidationError(
                    {"status": [f"{item.serial_number} is not currently missing."]}
                )
            item.status = SerializedItem.STATUS_AVAILABLE
            item.save(update_fields=["status"])

            work_order_id = (
                item.work_order_line_item.work_order_id
                if item.work_order_line_item_id
                else None
            )
            Transaction.objects.create(
                transaction_type=Transaction.TYPE_RETURN,
                serialized_item=item,
                work_order_id=work_order_id,
                reference_number=f"WO-{work_order_id}" if work_order_id else "",
                user=request.user,
            )
        return Response(SerializedItemSerializer(item).data, status=200)

    @action(detail=True, methods=["post"], url_path="write-off")
    def write_off(self, request, pk=None):
        # AC-4: status -> written_off, permanently excluded from available
        # stock - ProductTypeStockSummarySerializer's Available formula
        # already subtracts STATUS_WRITTEN_OFF (WRH-48), and close()'s own
        # NOT_STILL_OUT_STATUSES already protects a written-off item from
        # being clobbered back to missing.
        with transaction.atomic():
            item = self._get_locked_missing_item(pk)
            if item.status != SerializedItem.STATUS_MISSING:
                raise ValidationError(
                    {"status": [f"{item.serial_number} is not currently missing."]}
                )
            item.status = SerializedItem.STATUS_WRITTEN_OFF
            item.save(update_fields=["status"])

            work_order_id = (
                item.work_order_line_item.work_order_id
                if item.work_order_line_item_id
                else None
            )
            Transaction.objects.create(
                transaction_type=Transaction.TYPE_WRITTEN_OFF,
                serialized_item=item,
                work_order_id=work_order_id,
                reference_number=f"WO-{work_order_id}" if work_order_id else "",
                user=request.user,
            )
        return Response(SerializedItemSerializer(item).data, status=200)
