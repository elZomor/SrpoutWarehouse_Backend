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
        # select_related) - the second-level __work_order join is genuinely
        # read by get_work_order_reference() (parent_work_order_id/
        # supplementary_sequence via _work_order_reference()), not just the
        # bare FK id. The Max() annotation folds "most recent MISSING
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
        # lock the bare row only, but still select_related("product_type")
        # (not part of that nullable join) since _resolve()'s response is
        # built via SerializedItemSerializer, whose product_type_name field
        # would otherwise re-query it. work_order_line_item itself is read
        # lazily by _resolve() below, only on the success path (the
        # rejection branch returns before touching it).
        try:
            return (
                SerializedItem.objects.select_for_update()
                .select_related("product_type")
                .get(pk=pk)
            )
        except SerializedItem.DoesNotExist:
            raise Http404

    def _resolve(self, request, pk, target_status, transaction_type):
        # Shared by mark_found()/write_off() below - AC-3 and AC-4 are the
        # same shape (lock, re-check still-missing, flip status, log a
        # Transaction) differing only in the target status/transaction type,
        # so keeping one implementation avoids the two drifting apart (e.g.
        # a future fix to the WO-reference logic only needing to land once).
        with transaction.atomic():
            item = self._get_locked_missing_item(pk)
            if item.status != SerializedItem.STATUS_MISSING:
                raise ValidationError(
                    {"status": [f"{item.serial_number} is not currently missing."]}
                )
            item.status = target_status
            item.save(update_fields=["status"])

            # Transaction.reference_number intentionally stays the bare
            # "WO-<id>" form here, not the supplementary-aware
            # _work_order_reference() the serializer uses for display -
            # matches every other Transaction.reference_number write in this
            # codebase (close()/return_item()/etc., see
            # _work_order_reference()'s own comment on this exact split).
            work_order_id = (
                item.work_order_line_item.work_order_id
                if item.work_order_line_item_id
                else None
            )
            Transaction.objects.create(
                transaction_type=transaction_type,
                serialized_item=item,
                work_order_id=work_order_id,
                reference_number=f"WO-{work_order_id}" if work_order_id else "",
                user=request.user,
            )
        return Response(SerializedItemSerializer(item).data, status=200)

    @action(detail=True, methods=["post"], url_path="mark-found")
    def mark_found(self, request, pk=None):
        # AC-3: a return transaction is created, status -> available. The
        # item's WO is already closed by the time it can go missing (only
        # close() ever sets STATUS_MISSING) - matches return_item()'s "don't
        # clear work_order_line_item" convention, no WO status re-derivation
        # needed since a closed WO stays closed.
        return self._resolve(
            request, pk, SerializedItem.STATUS_AVAILABLE, Transaction.TYPE_RETURN
        )

    @action(detail=True, methods=["post"], url_path="write-off")
    def write_off(self, request, pk=None):
        # AC-4: status -> written_off, permanently excluded from available
        # stock - ProductTypeStockSummarySerializer's Available formula
        # already subtracts STATUS_WRITTEN_OFF (WRH-48), and close()'s own
        # NOT_STILL_OUT_STATUSES already protects a written-off item from
        # being clobbered back to missing.
        return self._resolve(
            request, pk, SerializedItem.STATUS_WRITTEN_OFF, Transaction.TYPE_WRITTEN_OFF
        )
