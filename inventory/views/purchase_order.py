from django.db import IntegrityError
from django.db.models import Count, Prefetch
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


def _line_items_with_received_count(purchase_order=None):
    # Folds each line item's received count into the same query (one
    # COUNT-per-PO-list, not one per line item) - PurchaseOrderLineItemSerializer
    # reads it off via RECEIVED_COUNT_ANNOTATION instead of calling
    # .count() itself, which would silently issue a fresh query per line
    # item regardless of any prefetch_related on the outer queryset.
    # Without a purchase_order, returns a Prefetch for the viewset's
    # list/retrieve queryset; given one, returns a plain queryset scoped to
    # just that PO's rows, for receive() to reuse as a single fresh fetch
    # instead of re-fetching the whole PurchaseOrder row a second time.
    queryset = PurchaseOrderLineItem.objects.select_related("product_type").annotate(
        **{RECEIVED_COUNT_ANNOTATION: Count("serialized_items")}
    )
    if purchase_order is not None:
        return queryset.filter(purchase_order_id=purchase_order.id)
    return Prefetch("line_items", queryset=queryset)


class PurchaseOrderViewSet(
    mixins.ListModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet
):
    # WRH-29 (PRD story US-009a) scoped create+list; WRH-56 (US-010a) adds
    # the receive action below - field/business-rule validation (WRH-30)
    # is still a separate story, so retrieve/update/destroy stay
    # unregistered for now.
    permission_classes = [IsAuthenticated]
    queryset = PurchaseOrder.objects.prefetch_related(
        _line_items_with_received_count()
    ).all()
    serializer_class = PurchaseOrderSerializer

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

        # purchase_order.get_object() above may have prefetched line_items
        # from before this request's mutation, so read the current count
        # straight from the DB rather than trusting any cache on line_item.
        received_count = SerializedItem.objects.filter(
            purchase_order_line_item=line_item
        ).count()
        if received_count >= line_item.expected_quantity:
            raise ValidationError(
                {
                    "line_item": [
                        "This line item has already received its expected quantity."
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

        # One fresh query, after the create() above, serves both
        # recompute_status()'s decision and the response - avoids fetching
        # the PurchaseOrder row a second time (get_object() already fetched
        # it once above) and avoids computing the same Count() twice.
        # list() evaluates the queryset, which populates its own
        # _result_cache; storing that same queryset object (not a bare
        # list) in _prefetched_objects_cache is required, since
        # Manager.all() - what the serializer calls when it walks
        # purchase_order.line_items - returns the cached queryset itself
        # rather than re-fetching.
        line_items_queryset = _line_items_with_received_count(purchase_order)
        line_items = list(line_items_queryset)
        purchase_order.recompute_status(line_items=line_items)
        purchase_order._prefetched_objects_cache = {"line_items": line_items_queryset}
        return Response(
            PurchaseOrderSerializer(purchase_order).data,
            status=201,
        )
