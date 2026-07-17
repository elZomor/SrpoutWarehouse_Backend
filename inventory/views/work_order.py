from django.db import transaction
from django.db.models import Count, Prefetch, prefetch_related_objects
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from inventory.models import SerializedItem, WorkOrder, WorkOrderLineItem
from inventory.models.work_order import SCANNED_COUNT_ANNOTATION
from inventory.serializers import WorkOrderScanSerializer, WorkOrderSerializer


def _line_items_queryset():
    # Folds each line item's scanned count into the same query (one
    # COUNT-per-WO-list, not one per line item) - WorkOrderLineItemSerializer
    # reads it off via SCANNED_COUNT_ANNOTATION instead of calling .count()
    # itself, matching PurchaseOrder's identical _line_items_queryset().
    return WorkOrderLineItem.objects.select_related("product_type").annotate(
        **{SCANNED_COUNT_ANNOTATION: Count("serialized_items")}
    )


class WorkOrderViewSet(
    mixins.ListModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet
):
    # WRH-31 (US-011a) scopes create+list; WRH-54 (US-013a) adds the
    # start/scan/complete actions below - field validation (WRH-32),
    # supplementary creation (US-012a), and the active list view (US-014a)
    # are separate stories, so retrieve/update/destroy stay unregistered.
    permission_classes = [IsAuthenticated]
    queryset = WorkOrder.objects.select_related("created_by").prefetch_related(
        Prefetch("line_items", queryset=_line_items_queryset())
    )
    serializer_class = WorkOrderSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action in ("scan", "complete"):
            # Both actions build their own fresh, request-scoped query for
            # their response (see below) and never read the prefetched
            # line_items from self.get_object() - matches
            # PurchaseOrderViewSet.get_queryset()'s identical reasoning.
            queryset = queryset.prefetch_related(None)
        return queryset

    def perform_create(self, serializer):
        # AC-1: "the creating user is recorded" - taken from the
        # authenticated session, never client-supplied (created_by is
        # read_only on the serializer).
        serializer.save(created_by=self.request.user)

    def _refresh_line_items(self, work_order):
        # work_order was fetched with no prefetch at all (see
        # get_queryset() above), so this is the one place its line_items
        # relation gets populated - prefetch_related_objects (Django's
        # public API for this) runs a single fresh query and caches the
        # result, which the response serializer then reads with no further
        # query. Matches PurchaseOrderViewSet.receive()'s identical pattern.
        prefetch_related_objects(
            [work_order],
            Prefetch(
                "line_items",
                queryset=_line_items_queryset().filter(work_order_id=work_order.id),
            ),
        )

    @action(detail=True, methods=["post"])
    def start(self, request, pk=None):
        # AC-1: a draft WO moves to "in_progress" when the manager begins
        # scanning.
        work_order = self.get_object()
        if work_order.status != WorkOrder.STATUS_DRAFT:
            raise ValidationError(
                {"status": ["Only a draft work order can start fulfillment."]}
            )

        with transaction.atomic():
            work_order = WorkOrder.objects.select_for_update().get(pk=work_order.pk)
            if work_order.status != WorkOrder.STATUS_DRAFT:
                raise ValidationError(
                    {"status": ["Only a draft work order can start fulfillment."]}
                )
            work_order.status = WorkOrder.STATUS_IN_PROGRESS
            work_order.save(update_fields=["status"])
            self._refresh_line_items(work_order)

        return Response(WorkOrderSerializer(work_order).data, status=200)

    @action(detail=True, methods=["post"])
    def scan(self, request, pk=None):
        # AC-2: a scanned serial is validated (exists, correct product
        # type, status available) and claimed against the target line item,
        # advancing that line item's live counter. Box-QR scanning (AC-3)
        # is out of scope - see WorkOrderScanSerializer's comment.
        work_order = self.get_object()
        if work_order.status != WorkOrder.STATUS_IN_PROGRESS:
            raise ValidationError(
                {"status": ["Work order fulfillment has not been started."]}
            )

        serializer = WorkOrderScanSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        line_item = serializer.validated_data["line_item"]
        serial_number = serializer.validated_data["serial_number"]

        if line_item.work_order_id != work_order.id:
            raise ValidationError(
                {"line_item": ["Line item does not belong to this work order."]}
            )

        with transaction.atomic():
            # Lock the parent WorkOrder row - AC-1's status re-check below
            # needs to serialize against a concurrent complete()/start()
            # call, matching PurchaseOrderViewSet.receive()'s identical
            # "lock the row whose invariant you're protecting" reasoning
            # (status is a WO-level field, not a line-item-level one).
            work_order = WorkOrder.objects.select_for_update().get(pk=work_order.pk)
            if work_order.status != WorkOrder.STATUS_IN_PROGRESS:
                raise ValidationError(
                    {"status": ["Work order fulfillment has not been started."]}
                )
            # Re-fetch line_item now that the parent is locked - the
            # serializer validated its archived-product-type guard and the
            # ownership check above both ran before the lock was acquired.
            line_item = WorkOrderLineItem.objects.select_related("product_type").get(
                pk=line_item.pk
            )
            if line_item.work_order_id != work_order.id:
                raise ValidationError(
                    {"line_item": ["Line item does not belong to this work order."]}
                )
            # AC-2/AC-4: reject a scan once this line item's requested
            # quantity has already been reached, rather than over-scanning -
            # matches PurchaseOrderViewSet.receive()'s identical
            # WRH-30/AC-4 cap check on the same kind of "claim against a
            # requested quantity" flow.
            scanned_count = SerializedItem.objects.filter(
                work_order_line_item=line_item
            ).count()
            if scanned_count >= line_item.quantity:
                raise ValidationError(
                    {
                        "line_item": [
                            "This line item has already reached its requested"
                            " quantity."
                        ]
                    }
                )

            # Lock the target SerializedItem row too - it's the row whose
            # own invariant ("claimed at most once") this action protects,
            # independent of which WO or line item is doing the claiming.
            try:
                item = SerializedItem.objects.select_for_update().get(
                    serial_number=serial_number
                )
            except SerializedItem.DoesNotExist:
                raise ValidationError(
                    {"serial_number": ["No item found with this serial number."]}
                )
            if item.product_type_id != line_item.product_type_id:
                raise ValidationError(
                    {
                        "serial_number": [
                            "Item does not match this line item's product type."
                        ]
                    }
                )
            if item.status != SerializedItem.STATUS_AVAILABLE:
                raise ValidationError(
                    {"serial_number": ["Item is not available to scan."]}
                )

            item.status = SerializedItem.STATUS_RESERVED
            item.work_order_line_item = line_item
            item.save(update_fields=["status", "work_order_line_item"])

            self._refresh_line_items(work_order)

        return Response(WorkOrderSerializer(work_order).data, status=201)

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        # AC-4/AC-5: once every line item has reached its requested
        # quantity, the WO moves to "fulfilled" and every item scanned
        # against it moves from "reserved" to "out".
        work_order = self.get_object()
        if work_order.status != WorkOrder.STATUS_IN_PROGRESS:
            raise ValidationError(
                {"status": ["Work order fulfillment has not been started."]}
            )

        with transaction.atomic():
            work_order = WorkOrder.objects.select_for_update().get(pk=work_order.pk)
            if work_order.status != WorkOrder.STATUS_IN_PROGRESS:
                raise ValidationError(
                    {"status": ["Work order fulfillment has not been started."]}
                )

            line_items = list(
                _line_items_queryset().filter(work_order_id=work_order.id)
            )
            if any(
                getattr(item, SCANNED_COUNT_ANNOTATION) < item.quantity
                for item in line_items
            ):
                raise ValidationError(
                    {
                        "status": [
                            "All line items must reach their requested quantity"
                            " before fulfillment can be completed."
                        ]
                    }
                )

            SerializedItem.objects.filter(
                work_order_line_item__work_order_id=work_order.id
            ).update(
                status=SerializedItem.STATUS_OUT,
                last_work_order_reference=str(work_order),
            )
            work_order.status = WorkOrder.STATUS_FULFILLED
            work_order.save(update_fields=["status"])
            self._refresh_line_items(work_order)

        return Response(WorkOrderSerializer(work_order).data, status=200)
