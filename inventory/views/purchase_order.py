from django.db import IntegrityError
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from inventory.models import PurchaseOrder, SerializedItem
from inventory.serializers import (
    PurchaseOrderReceiveSerializer,
    PurchaseOrderSerializer,
)
from inventory.serializers.serialized_item import duplicate_serial_number_message


class PurchaseOrderViewSet(
    mixins.ListModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet
):
    # WRH-29 (PRD story US-009a) scoped create+list; WRH-56 (US-010a) adds
    # the receive action below - field/business-rule validation (WRH-30)
    # is still a separate story, so retrieve/update/destroy stay
    # unregistered for now.
    permission_classes = [IsAuthenticated]
    # select_related/prefetch_related avoids an N+1 per line item: the
    # serializer's product_type_name field reads product_type.name on every
    # nested line item, for every purchase order in the list.
    queryset = PurchaseOrder.objects.prefetch_related(
        "line_items__product_type", "line_items__serialized_items"
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
        # purchase_order was fetched (and its line_items/serialized_items
        # prefetched) before the item above was created, so its cache is
        # one scan stale - re-fetch so the response reflects the scan that
        # was just recorded, not the state before it.
        purchase_order = self.get_queryset().get(pk=purchase_order.pk)
        return Response(
            PurchaseOrderSerializer(purchase_order).data,
            status=201,
        )
