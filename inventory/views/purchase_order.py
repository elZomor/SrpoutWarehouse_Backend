from django.db import IntegrityError
from django.db.models import Count, Prefetch
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from inventory.models import PurchaseOrder, PurchaseOrderLineItem, SerializedItem
from inventory.serializers import (
    PurchaseOrderReceiveSerializer,
    PurchaseOrderSerializer,
)
from inventory.serializers.serialized_item import duplicate_serial_number_message


def _line_items_with_received_count():
    # Folds each line item's received count into the same prefetch query
    # (one COUNT-per-PO-list, not one per line item) - PurchaseOrderLineItemSerializer
    # reads it off as "received_count" instead of calling .count() itself,
    # which would silently issue a fresh query per line item regardless of
    # any prefetch_related on the outer queryset.
    return Prefetch(
        "line_items",
        queryset=PurchaseOrderLineItem.objects.select_related("product_type").annotate(
            received_count=Count("serialized_items")
        ),
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
        _line_items_with_received_count()
    ).all()
    serializer_class = PurchaseOrderSerializer

    @action(detail=True, methods=["post"])
    def receive(self, request, pk=None):
        # AC-1/AC-3/AC-4/AC-5: one scanned serial against one line item of
        # this PO creates a new "available" SerializedItem linked to it,
        # then the PO's status is recomputed from actual received counts.
        purchase_order = self.get_object()
        serializer = PurchaseOrderReceiveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        line_item = serializer.validated_data["line_item"]
        serial_number = serializer.validated_data["serial_number"]

        if line_item.purchase_order_id != purchase_order.id:
            raise ValidationError(
                {"line_item": ["Line item does not belong to this purchase order."]}
            )
        # Mirrors SerializedItemSerializer's WRH-21/AC-6 guard on the
        # generic register flow - an archived product type shouldn't grow
        # new stock through the PO receive path either.
        if line_item.product_type.archived:
            raise ValidationError(
                {
                    "line_item": [
                        "Product type is archived and cannot receive new items."
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

        purchase_order.recompute_status()
        # purchase_order was fetched (and its line_items prefetched, with
        # each one's received_count annotated) before the item above was
        # created, so that cache is one scan stale. django.db.models.query.
        # prefetch_related_objects() will NOT fix this in place: it treats
        # a relation already present in _prefetched_objects_cache as fetched
        # and skips re-querying, silently returning the same stale cache.
        # Re-fetching the row via the same prefetching queryset is what
        # actually forces a fresh COUNT.
        purchase_order = self.get_queryset().get(pk=purchase_order.pk)
        return Response(
            PurchaseOrderSerializer(purchase_order).data,
            status=201,
        )
