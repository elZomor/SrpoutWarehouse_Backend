from django.db import IntegrityError, transaction
from django.db.models import Count, Prefetch, prefetch_related_objects
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from inventory.models import PurchaseOrder, PurchaseOrderLineItem, SerializedItem
from inventory.models.purchase_order import RECEIVED_COUNT_ANNOTATION
from inventory.serializers import (
    PurchaseOrderReceiveSerializer,
    PurchaseOrderSerializer,
)
from inventory.serializers.serialized_item import duplicate_serial_number_message


def _line_items_queryset():
    # Folds each line item's received count into the same query (one
    # COUNT-per-PO-list, not one per line item) - PurchaseOrderLineItemSerializer
    # reads it off via RECEIVED_COUNT_ANNOTATION instead of calling
    # .count() itself, which would silently issue a fresh query per line
    # item regardless of any prefetch_related on the outer queryset.
    return PurchaseOrderLineItem.objects.select_related("product_type").annotate(
        **{RECEIVED_COUNT_ANNOTATION: Count("serialized_items")}
    )


class PurchaseOrderViewSet(
    mixins.ListModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet
):
    # WRH-29 (PRD story US-009a) scoped create+list; WRH-56 (US-010a) adds
    # the receive action below - field/business-rule validation (WRH-30)
    # is still a separate story, so retrieve/update/destroy stay
    # unregistered for now.
    permission_classes = [IsAuthenticated]
    queryset = PurchaseOrder.objects.prefetch_related(
        Prefetch("line_items", queryset=_line_items_queryset())
    ).all()
    serializer_class = PurchaseOrderSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action == "receive":
            # receive() builds its own fresh, single-line-item-scoped query
            # for its response (see below) and never reads the prefetched
            # line_items from self.get_object() - the class-level
            # queryset's prefetch_related JOIN+Count would be fetched and
            # then discarded on every scan, so skip it for this one action.
            queryset = queryset.prefetch_related(None)
        return queryset

    @action(detail=True, methods=["post"])
    def receive(self, request, pk=None):
        # AC-1/AC-3/AC-4/AC-5: one scanned serial against one line item of
        # this PO creates a new "available" SerializedItem linked to it,
        # then the PO's status is recomputed from actual received counts.
        # The archived-product-type guard (WRH-21/AC-6) lives on
        # PurchaseOrderReceiveSerializer.line_item's queryset, not here -
        # matches the pattern used elsewhere in this codebase for the same
        # invariant instead of a one-off manual check.
        purchase_order = self.get_object()
        serializer = PurchaseOrderReceiveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        line_item = serializer.validated_data["line_item"]
        serial_number = serializer.validated_data["serial_number"]

        if line_item.purchase_order_id != purchase_order.id:
            raise ValidationError(
                {"line_item": ["Line item does not belong to this purchase order."]}
            )

        with transaction.atomic():
            # select_for_update() locks the *parent PurchaseOrder* row, not
            # just this line item - status is derived from ALL of a PO's
            # line items (recompute_status() below), so two concurrent
            # receive() calls against DIFFERENT line items of the same PO
            # need to serialize here too, not just concurrent scans of the
            # same line item. Without this, two such calls could each
            # compute status from a pre-commit snapshot of the other's
            # write and the later one to commit could persist a stale
            # status. (SQLite, used in tests, has no row-level locking and
            # silently no-ops this; Postgres, used in production, enforces
            # it.)
            purchase_order = PurchaseOrder.objects.select_for_update().get(
                pk=purchase_order.pk
            )
            # Re-fetch line_item now that the lock is held: the serializer
            # validated it (including the archived-product-type guard)
            # before the lock was acquired, so a concurrent archive in that
            # window needs to be caught again now, not just once at request
            # start. Same reasoning applies to the ownership check just
            # above - re-verify it against the now-locked purchase_order
            # rather than trusting the pre-lock read.
            line_item = PurchaseOrderLineItem.objects.select_related(
                "product_type"
            ).get(pk=line_item.pk)
            if line_item.purchase_order_id != purchase_order.id:
                raise ValidationError(
                    {"line_item": ["Line item does not belong to this purchase order."]}
                )
            if line_item.product_type.archived:
                raise ValidationError(
                    {
                        "line_item": [
                            "Product type is archived and cannot receive new" " items."
                        ]
                    }
                )
            received_count = SerializedItem.objects.filter(
                purchase_order_line_item=line_item
            ).count()
            if received_count >= line_item.expected_quantity:
                raise ValidationError(
                    {
                        "line_item": [
                            "This line item has already received its expected"
                            " quantity."
                        ]
                    }
                )

            try:
                SerializedItem.objects.create(
                    product_type=line_item.product_type,
                    serial_number=serial_number,
                    purchase_order_line_item=line_item,
                )
            except IntegrityError as exc:
                raise ValidationError(
                    {"serial_number": [duplicate_serial_number_message(serial_number)]}
                ) from exc

            # purchase_order was fetched with no prefetch at all (see
            # get_queryset() above), so this is the one place its
            # line_items relation gets populated - prefetch_related_objects
            # (Django's public API for this, unlike manually touching
            # _prefetched_objects_cache) runs a single fresh query and
            # caches the result on purchase_order.line_items, which both
            # recompute_status() below and the response serializer then
            # read with no further query.
            prefetch_related_objects(
                [purchase_order],
                Prefetch(
                    "line_items",
                    queryset=_line_items_queryset().filter(
                        purchase_order_id=purchase_order.id
                    ),
                ),
            )
            purchase_order.recompute_status(line_items=purchase_order.line_items.all())

        return Response(
            PurchaseOrderSerializer(purchase_order).data,
            status=201,
        )
