from django.db import transaction
from django.db.models import Count, Prefetch, Q, prefetch_related_objects
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from inventory.models import SerializedItem, WorkOrder, WorkOrderLineItem
from inventory.models.work_order import (
    RETURNED_COUNT_ANNOTATION,
    SCANNED_COUNT_ANNOTATION,
    STILL_OUT_COUNT_ANNOTATION,
)
from inventory.serializers import (
    WorkOrderActiveSerializer,
    WorkOrderDetailSerializer,
    WorkOrderReturnScanSerializer,
    WorkOrderReturnSerializer,
    WorkOrderScanSerializer,
    WorkOrderSerializer,
)

# WRH-38: a WO is eligible for return once it's fulfilled, or already
# partially returned (AC-4: completing a partially_returned WO reuses the
# same return_item action, no separate "reopen" step).
RETURN_ELIGIBLE_STATUSES = (
    WorkOrder.STATUS_FULFILLED,
    WorkOrder.STATUS_PARTIALLY_RETURNED,
)


def _unavailable_item_message(item):
    # WRH-33/AC-1/AC-3: scan() rejects a non-available item with a reason
    # specific to *why* - "out"/"reserved" name the WO holding it (still
    # readable off work_order_line_item, which nothing clears once set -
    # see SerializedItem.work_order_line_item's own comment), "damaged"/
    # "missing" name the item's own condition. The bare fallback only
    # covers a status this function doesn't otherwise know about.
    if item.status == SerializedItem.STATUS_OUT and item.work_order_line_item_id:
        wo_id = item.work_order_line_item.work_order_id
        return f"{item.serial_number} is currently out on WO-{wo_id}"
    if item.status == SerializedItem.STATUS_RESERVED and item.work_order_line_item_id:
        wo_id = item.work_order_line_item.work_order_id
        return f"{item.serial_number} is already reserved on WO-{wo_id}"
    if item.status == SerializedItem.STATUS_DAMAGED:
        return f"{item.serial_number} is damaged and cannot be issued"
    if item.status == SerializedItem.STATUS_MISSING:
        return f"{item.serial_number} is missing and cannot be issued"
    return f"{item.serial_number} is not available to scan"


def _line_items_queryset():
    # Folds each line item's scanned count into the same query (one
    # COUNT-per-WO-list, not one per line item) - WorkOrderLineItemSerializer
    # reads it off via SCANNED_COUNT_ANNOTATION instead of calling .count()
    # itself, matching PurchaseOrder's identical _line_items_queryset().
    return WorkOrderLineItem.objects.select_related("product_type").annotate(
        **{SCANNED_COUNT_ANNOTATION: Count("serialized_items")}
    )


def _active_line_items_queryset():
    # WRH-55/AC-2: same one-query-per-list shape as _line_items_queryset(),
    # but counting by SerializedItem.status instead of raw scan count - see
    # RETURNED_COUNT_ANNOTATION's model-level comment for why "returned" is
    # always 0 today.
    return WorkOrderLineItem.objects.select_related("product_type").annotate(
        **{
            RETURNED_COUNT_ANNOTATION: Count(
                "serialized_items",
                filter=Q(serialized_items__status=SerializedItem.STATUS_AVAILABLE),
            ),
            STILL_OUT_COUNT_ANNOTATION: Count(
                "serialized_items",
                filter=Q(serialized_items__status=SerializedItem.STATUS_OUT),
            ),
        }
    )


class WorkOrderViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    # WRH-31 (US-011a) scopes create+list; WRH-54 (US-013a) adds the
    # start/scan/complete actions below; WRH-55 (US-014a) adds retrieve
    # (AC-3 drill-down) and the active action (AC-1/AC-2 list) below -
    # field validation (WRH-32) and supplementary creation (US-012a, still
    # no ticket/UI) are the remaining unscoped gaps, so update/destroy stay
    # unregistered.
    permission_classes = [IsAuthenticated]
    queryset = WorkOrder.objects.select_related("created_by").prefetch_related(
        Prefetch("line_items", queryset=_line_items_queryset())
    )
    serializer_class = WorkOrderSerializer

    def get_serializer_class(self):
        if self.action == "active":
            return WorkOrderActiveSerializer
        if self.action == "retrieve":
            return WorkOrderDetailSerializer
        return super().get_serializer_class()

    def get_queryset(self):
        if self.action == "active":
            # AC-1: Primary WOs only at the top level, each with its own
            # supplementaries nested beneath (one level deep - see
            # WorkOrderActiveSupplementarySerializer's comment).
            return (
                WorkOrder.objects.filter(parent_work_order__isnull=True)
                .order_by("-expected_date_out", "-id")
                .prefetch_related(
                    Prefetch("line_items", queryset=_active_line_items_queryset()),
                    Prefetch(
                        "supplementaries",
                        queryset=WorkOrder.objects.order_by(
                            "-expected_date_out", "-id"
                        ).prefetch_related(
                            Prefetch(
                                "line_items", queryset=_active_line_items_queryset()
                            )
                        ),
                    ),
                )
            )
        if self.action == "retrieve":
            # AC-3: the exact serials issued on this WO and their current
            # statuses - a different shape from the list actions' counts.
            return WorkOrder.objects.select_related("created_by").prefetch_related(
                Prefetch(
                    "line_items",
                    queryset=WorkOrderLineItem.objects.select_related(
                        "product_type"
                    ).prefetch_related(
                        Prefetch(
                            "serialized_items",
                            queryset=SerializedItem.objects.order_by("serial_number"),
                        )
                    ),
                )
            )
        queryset = super().get_queryset()
        if self.action in ("scan", "complete", "return_item"):
            # Both actions build their own fresh, request-scoped query for
            # their response (see below) and never read the prefetched
            # line_items from self.get_object() - matches
            # PurchaseOrderViewSet.get_queryset()'s identical reasoning.
            queryset = queryset.prefetch_related(None)
        return queryset

    @action(detail=False, methods=["get"])
    def active(self, request):
        # AC-1/AC-2: nested Primary+supplementary list with per-type
        # returned/still-out summary counts - get_queryset()/
        # get_serializer_class() above do the actual shaping, this just
        # reuses ListModelMixin's list() (pagination included) rather than
        # duplicating it. AC-4 (empty state) needs nothing special here -
        # an empty queryset already serializes to [].
        return self.list(request)

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

    def _refresh_active_line_items(self, work_order):
        # Same reasoning as _refresh_line_items() above, but populated with
        # the returned/still-out counts (AC-1/AC-2's "Returned / Still
        # missing" summary) instead of scan progress.
        prefetch_related_objects(
            [work_order],
            Prefetch(
                "line_items",
                queryset=_active_line_items_queryset().filter(
                    work_order_id=work_order.id
                ),
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
                # AC-4: exact text the fulfillment UI shows verbatim.
                raise ValidationError({"serial_number": ["Serial not found"]})
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
                    {"serial_number": [_unavailable_item_message(item)]}
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

    @action(detail=True, methods=["post"])
    def return_item(self, request, pk=None):
        # AC-1/AC-2/AC-4: a scanned serial currently "out" against this WO
        # flips back to "available"; once none remain out the WO moves to
        # "returned", otherwise "partially_returned" - re-scanning a
        # partially_returned WO's remaining items (AC-4) reuses this same
        # action, no separate "reopen" step needed. Box-QR return (AC-3)
        # needs a Box/Container model that doesn't exist yet - deferred to
        # WRH-5 (see WorkOrderReturnScanSerializer's comment). Business-rule
        # guards (item not issued on this WO's exact wording, already-
        # available rejection wording, closed-WO guard) are WRH-39's scope
        # per this ticket's own notes - only the minimal checks needed to
        # compute a correct transition without corrupting another WO's
        # items are included here.
        work_order = self.get_object()
        if work_order.status not in RETURN_ELIGIBLE_STATUSES:
            raise ValidationError(
                {"status": ["Work order is not eligible for return."]}
            )

        serializer = WorkOrderReturnScanSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serial_number = serializer.validated_data["serial_number"]

        with transaction.atomic():
            # Lock the parent WorkOrder row - same "lock the row whose
            # invariant you're protecting" reasoning as scan()/complete()
            # above, since the status this action re-checks and writes is a
            # WO-level field.
            work_order = WorkOrder.objects.select_for_update().get(pk=work_order.pk)
            if work_order.status not in RETURN_ELIGIBLE_STATUSES:
                raise ValidationError(
                    {"status": ["Work order is not eligible for return."]}
                )

            try:
                item = (
                    SerializedItem.objects.select_for_update()
                    .select_related("work_order_line_item")
                    .get(serial_number=serial_number)
                )
            except SerializedItem.DoesNotExist:
                raise ValidationError({"serial_number": ["Serial not found"]})

            if (
                item.work_order_line_item_id is None
                or item.work_order_line_item.work_order_id != work_order.id
            ):
                raise ValidationError(
                    {
                        "serial_number": [
                            f"{item.serial_number} was not issued on"
                            f" WO-{work_order.id}"
                        ]
                    }
                )
            if item.status != SerializedItem.STATUS_OUT:
                raise ValidationError(
                    {
                        "serial_number": [
                            f"{item.serial_number} is not currently out on"
                            " this work order"
                        ]
                    }
                )

            item.status = SerializedItem.STATUS_AVAILABLE
            item.save(update_fields=["status"])

            still_out = SerializedItem.objects.filter(
                work_order_line_item__work_order_id=work_order.id,
                status=SerializedItem.STATUS_OUT,
            ).count()
            work_order.status = (
                WorkOrder.STATUS_RETURNED
                if still_out == 0
                else WorkOrder.STATUS_PARTIALLY_RETURNED
            )
            work_order.save(update_fields=["status"])
            self._refresh_active_line_items(work_order)

        return Response(WorkOrderReturnSerializer(work_order).data, status=200)
