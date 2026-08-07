from django.db import transaction
from django.db.models import Count, Prefetch, Q, prefetch_related_objects
from django.http import HttpResponse
from django.template.loader import render_to_string
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from weasyprint import HTML

from inventory.models import (
    Box,
    SerializedItem,
    Transaction,
    WorkOrder,
    WorkOrderLineItem,
)
from inventory.models.work_order import (
    DAMAGED_COUNT_ANNOTATION,
    RETURNED_COUNT_ANNOTATION,
    SCANNED_COUNT_ANNOTATION,
    STILL_OUT_COUNT_ANNOTATION,
)
from inventory.serializers import (
    WorkOrderActiveSerializer,
    WorkOrderDetailSerializer,
    WorkOrderReturnBoxSerializer,
    WorkOrderReturnScanSerializer,
    WorkOrderReturnSerializer,
    WorkOrderScanBoxSerializer,
    WorkOrderScanSerializer,
    WorkOrderSerializer,
    WorkOrderTransferSerializer,
)
from inventory.serializers.work_order import _work_order_reference

# WRH-38: a WO is eligible for return once it's fulfilled, or already
# partially returned (AC-4: completing a partially_returned WO reuses the
# same return_item action, no separate "reopen" step).
RETURN_ELIGIBLE_STATUSES = (
    WorkOrder.STATUS_FULFILLED,
    WorkOrder.STATUS_PARTIALLY_RETURNED,
)

# WRH-40/AC-1/AC-4: manual close is eligible from the same states a return
# is - a fully "returned" WO is already terminal and not eligible (AC-4's
# note), matching return_item()/return_box()'s identical eligibility rule
# rather than re-deriving it as a separate tuple.
CLOSE_ELIGIBLE_STATUSES = RETURN_ELIGIBLE_STATUSES

# WRH-40: both terminal WO states - a "returned" WO reached that state by
# every item genuinely coming back, a "closed" one by a manual close (with
# possibly some items moved to Missing Items instead). Shared by every site
# that treats "this WO is done" as a single condition (active-list
# exclusion, transfer's destination guard).
_TERMINAL_STATUSES = (WorkOrder.STATUS_RETURNED, WorkOrder.STATUS_CLOSED)

# Shared by _active_line_items_queryset(), _finalize_return_status(), and
# close() - "still out" means anything but available (genuinely returned) or
# damaged (its own outcome, WRH-57/AC-3), whichever of the three needs to
# express that same exclusion. Deliberately still counts a missing item as
# "still out" (WRH-55's own test_still_out_count_treats_a_missing_item_as_
# not_yet_returned) - on the active list this is exactly the point (an
# unaccounted-for item shouldn't silently vanish from the summary), so
# close()'s response inheriting the same reading ("N items still
# out/unaccounted-for") alongside its own missing_count is a second,
# consistent lens on the same items, not a contradiction worth a
# still-out definition that would undo WRH-55's own intentional choice.
NOT_STILL_OUT_STATUSES = (
    SerializedItem.STATUS_AVAILABLE,
    SerializedItem.STATUS_DAMAGED,
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
    # RETURNED_COUNT_ANNOTATION's model-level comment for why still_out
    # excludes AVAILABLE *and* DAMAGED (WRH-57/AC-3) rather than only
    # counting STATUS_OUT.
    return WorkOrderLineItem.objects.select_related("product_type").annotate(
        **{
            RETURNED_COUNT_ANNOTATION: Count(
                "serialized_items",
                filter=Q(serialized_items__status=SerializedItem.STATUS_AVAILABLE),
            ),
            DAMAGED_COUNT_ANNOTATION: Count(
                "serialized_items",
                filter=Q(serialized_items__status=SerializedItem.STATUS_DAMAGED),
            ),
            STILL_OUT_COUNT_ANNOTATION: Count(
                "serialized_items",
                filter=~Q(serialized_items__status__in=NOT_STILL_OUT_STATUSES),
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
    # (AC-3 drill-down) and the active action (AC-1/AC-2 list) below;
    # WRH-53 (US-012a) adds supplementary creation - same create endpoint,
    # WorkOrderSerializer.parent_work_order is just an optional field on it,
    # no new route needed. update/destroy stay unregistered - out of every
    # WO story's scope so far.
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
            # WorkOrderActiveSupplementarySerializer's comment). WRH-38:
            # a fully "returned" WO is done - excluded so it doesn't stay
            # on this list forever once return_item() closes it out.
            # WRH-40: a manually "closed" WO is equally done - excluded the
            # same way. "partially_returned" still needs attention, so it
            # stays.
            return (
                WorkOrder.objects.filter(parent_work_order__isnull=True)
                .exclude(status__in=_TERMINAL_STATUSES)
                .order_by("-expected_date_out", "-id")
                .prefetch_related(
                    Prefetch("line_items", queryset=_active_line_items_queryset()),
                    Prefetch(
                        "supplementaries",
                        # Same "a done WO is excluded" reasoning as the
                        # primary-level queryset above - a supplementary can
                        # independently reach STATUS_RETURNED/STATUS_CLOSED
                        # too, and should stop nesting under its
                        # (still-active) primary once it does.
                        queryset=WorkOrder.objects.exclude(
                            status__in=_TERMINAL_STATUSES
                        )
                        .order_by("-expected_date_out", "-id")
                        .prefetch_related(
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
        if self.action == "packing_list":
            # WRH-34/AC-2: same per-line-item serials shape as retrieve
            # above, but also nesting supplementaries (one level deep,
            # matching the "active" queryset's nesting) so packing_list()
            # can consolidate the Primary + every supplementary's line
            # items into one PDF from a single query per level.
            line_items_queryset = WorkOrderLineItem.objects.select_related(
                "product_type"
            ).prefetch_related(
                Prefetch(
                    "serialized_items",
                    queryset=SerializedItem.objects.order_by("serial_number"),
                )
            )
            return WorkOrder.objects.select_related("created_by").prefetch_related(
                Prefetch("line_items", queryset=line_items_queryset),
                Prefetch(
                    "supplementaries",
                    queryset=WorkOrder.objects.order_by(
                        "supplementary_sequence"
                    ).prefetch_related(
                        Prefetch("line_items", queryset=line_items_queryset)
                    ),
                ),
            )
        queryset = super().get_queryset()
        if self.action in (
            "scan",
            "scan_box",
            "complete",
            "return_item",
            "return_box",
            "close",
        ):
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

    def _refresh_line_items(self, work_order, queryset_builder=_line_items_queryset):
        # work_order was fetched with no prefetch at all (see
        # get_queryset() above), so this is the one place its line_items
        # relation gets populated - prefetch_related_objects (Django's
        # public API for this) runs a single fresh query and caches the
        # result, which the response serializer then reads with no further
        # query. Matches PurchaseOrderViewSet.receive()'s identical pattern.
        # queryset_builder defaults to the scan-progress shape (start/scan/
        # complete's responses); return_item() passes
        # _active_line_items_queryset for the returned/still-out shape
        # instead (AC-1/AC-2's "Returned / Still missing" summary).
        prefetch_related_objects(
            [work_order],
            Prefetch(
                "line_items",
                queryset=queryset_builder().filter(work_order_id=work_order.id),
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
        # is the separate scan_box() action below, not an alternate input
        # shape here.
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

    @action(detail=True, methods=["post"], url_path="scan-box")
    def scan_box(self, request, pk=None):
        # WRH-26/AC-2/AC-3: scanning a Box's code expands to every item
        # inside in one call, each validated and claimed against the WO's
        # line item matching the box's product type - same
        # available/quantity-cap/product-type/archived checks scan() applies
        # to a single serial, just looped so one item's rejection doesn't
        # block its box-mates. BoxSerializer now guarantees every item in a
        # box shares its declared product_type (WRH-27/AC-1), but the
        # per-item check below is kept anyway as defense in depth rather
        # than trusting that invariant transitively. Receive-context box
        # scanning is out of scope - see WorkOrderScanBoxSerializer's
        # comment.
        work_order = self.get_object()
        if work_order.status != WorkOrder.STATUS_IN_PROGRESS:
            raise ValidationError(
                {"status": ["Work order fulfillment has not been started."]}
            )

        serializer = WorkOrderScanBoxSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        box_code = serializer.validated_data["box_code"]

        try:
            box = Box.objects.select_related("product_type").get(code=box_code)
        except Box.DoesNotExist:
            raise ValidationError({"box_code": ["Box not found"]})

        results = []
        with transaction.atomic():
            # Lock the parent WorkOrder row - same reasoning as scan()'s
            # identical lock, since every item below is claimed against
            # this WO's line items.
            work_order = WorkOrder.objects.select_for_update().get(pk=work_order.pk)
            if work_order.status != WorkOrder.STATUS_IN_PROGRESS:
                raise ValidationError(
                    {"status": ["Work order fulfillment has not been started."]}
                )
            # Matches WorkOrderScanSerializer.line_item's identical
            # archived-product-type queryset restriction (WRH-21) - a single
            # scan can't be aimed at an archived-product-type line item, so
            # neither should a box scan.
            matching_line_items = list(
                WorkOrderLineItem.objects.select_related("product_type").filter(
                    work_order_id=work_order.id,
                    product_type_id=box.product_type_id,
                    product_type__archived=False,
                )
            )
            if not matching_line_items:
                raise ValidationError(
                    {
                        "box_code": [
                            "This work order has no line item for the box's"
                            " product type."
                        ]
                    }
                )
            if len(matching_line_items) > 1:
                # Nothing prevents a WO from having two line items of the
                # same product type (no such uniqueness constraint exists),
                # unlike scan() which always takes an explicit line_item id
                # from the caller - a box scan has no way to say which one
                # it means, so ask for individual scans instead of silently
                # picking one.
                raise ValidationError(
                    {
                        "box_code": [
                            "This work order has more than one line item for"
                            " the box's product type - scan its items"
                            " individually instead."
                        ]
                    }
                )
            line_item = matching_line_items[0]
            # Computed once, then tracked locally as items are claimed below
            # - matches _line_items_queryset()'s "one COUNT, not one per
            # item" convention rather than re-querying inside the loop.
            scanned_count = SerializedItem.objects.filter(
                work_order_line_item=line_item
            ).count()
            for item in box.items.order_by("serial_number"):
                if scanned_count >= line_item.quantity:
                    results.append(
                        {
                            "serial_number": item.serial_number,
                            "added": False,
                            "reason": (
                                "This line item has already reached its"
                                " requested quantity."
                            ),
                        }
                    )
                    continue
                # Lock the target SerializedItem row too - matches scan()'s
                # identical "claimed at most once" guard.
                locked_item = SerializedItem.objects.select_for_update().get(pk=item.pk)
                if locked_item.product_type_id != line_item.product_type_id:
                    # Defense in depth, not reachable in practice now that
                    # BoxSerializer guarantees a box holds only one product
                    # type (WRH-27/AC-1) - reject a mismatched item the same
                    # way scan() rejects one, instead of silently claiming it
                    # against the wrong line item.
                    results.append(
                        {
                            "serial_number": locked_item.serial_number,
                            "added": False,
                            "reason": (
                                "Item does not match this line item's" " product type."
                            ),
                        }
                    )
                    continue
                if locked_item.status != SerializedItem.STATUS_AVAILABLE:
                    results.append(
                        {
                            "serial_number": locked_item.serial_number,
                            "added": False,
                            "reason": _unavailable_item_message(locked_item),
                        }
                    )
                    continue
                locked_item.status = SerializedItem.STATUS_RESERVED
                locked_item.work_order_line_item = line_item
                locked_item.save(update_fields=["status", "work_order_line_item"])
                scanned_count += 1
                results.append(
                    {
                        "serial_number": locked_item.serial_number,
                        "added": True,
                        "reason": "",
                    }
                )

            self._refresh_line_items(work_order)

        return Response(
            {
                "work_order": WorkOrderSerializer(work_order).data,
                "box_summary": {
                    "code": box.code,
                    "added": sum(1 for result in results if result["added"]),
                    "results": results,
                },
            },
            status=201,
        )

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

            # WRH-49/AC-1: capture which items this bulk update is about to
            # flip to "out" before it runs, so a transaction row can be
            # logged for each one below - the update() call itself returns
            # only a row count, not the affected rows.
            issued_item_ids = list(
                SerializedItem.objects.filter(
                    work_order_line_item__work_order_id=work_order.id
                ).values_list("id", flat=True)
            )
            SerializedItem.objects.filter(
                work_order_line_item__work_order_id=work_order.id
            ).update(
                status=SerializedItem.STATUS_OUT,
                last_work_order_reference=str(work_order),
            )
            Transaction.objects.bulk_create(
                [
                    Transaction(
                        transaction_type=Transaction.TYPE_ISSUE,
                        serialized_item_id=item_id,
                        work_order=work_order,
                        reference_number=f"WO-{work_order.id}",
                        user=request.user,
                    )
                    for item_id in issued_item_ids
                ]
            )
            work_order.status = WorkOrder.STATUS_FULFILLED
            work_order.save(update_fields=["status"])
            self._refresh_line_items(work_order)

        return Response(WorkOrderSerializer(work_order).data, status=200)

    @action(detail=True, methods=["post"], url_path="return-item")
    def return_item(self, request, pk=None):
        # AC-1/AC-2/AC-4: a scanned serial currently "out" against this WO
        # flips back to "available"; once none remain out the WO moves to
        # "returned", otherwise "partially_returned" - re-scanning a
        # partially_returned WO's remaining items (AC-4) reuses this same
        # action, no separate "reopen" step needed. Box-QR return (AC-3 of
        # WRH-38) is the separate return_box() action below, not an
        # alternate input shape here.
        # WRH-39/AC-6: an item already flagged damaged is reflected as-is
        # (see the STATUS_DAMAGED branch below) rather than rejected
        # outright. WRH-57/AC-1: the same scan can instead flag the unit as
        # damaged (serializer's damaged=True) rather than returning it to
        # available stock - see the damaged branch further down.
        #
        # url_path is explicit (matches serialized_item.py's qr-code/qr-pdf
        # actions) since DRF's default would otherwise route this to
        # /return_item/ instead of this repo's hyphenated convention.
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
            if item.status == SerializedItem.STATUS_DAMAGED:
                # WRH-39/AC-6: already flagged damaged (by a prior scan
                # through this same action's damaged=True branch below, or
                # any other path) - reflect that state as a no-op instead of
                # rejecting the scan or flipping it back to available. No
                # Transaction is created here, so a WO already carrying a
                # damage record doesn't get a second one.
                # DAMAGED_COUNT_ANNOTATION/STILL_OUT_COUNT_ANNOTATION already
                # categorize this item correctly (damaged, not still-out) so
                # no extra bookkeeping is needed.
                self._refresh_line_items(work_order, _active_line_items_queryset)
                return Response(WorkOrderReturnSerializer(work_order).data, status=200)
            if item.status != SerializedItem.STATUS_OUT:
                raise ValidationError(
                    {
                        "serial_number": [
                            f"{item.serial_number} is not currently out on"
                            " this work order"
                        ]
                    }
                )

            # Doesn't clear work_order_line_item - matches this repo's
            # existing "current claim only, no history" design for this FK
            # (see SerializedItem.work_order_line_item's own comment and
            # _unavailable_item_message's identical reasoning). A later
            # scan() onto a different WO will reassign it there, which can
            # move this (by-then-closed) WO's own returned/still-out counts
            # after the fact - an accepted limitation of not having a
            # separate history/audit table, not something return_item
            # introduces on its own.
            #
            # WRH-57/AC-1: a manager can flag the returning unit as damaged
            # instead of a normal return - it moves to STATUS_DAMAGED (not
            # STATUS_AVAILABLE) and is logged as TYPE_DAMAGED, so it's
            # excluded from available stock and from the WO's "returned"
            # count, matching Transaction.TYPE_DAMAGED's own comment that it
            # was reserved for exactly this kind of future action.
            damaged = serializer.validated_data["damaged"]
            item.status = (
                SerializedItem.STATUS_DAMAGED
                if damaged
                else SerializedItem.STATUS_AVAILABLE
            )
            item.save(update_fields=["status"])

            Transaction.objects.create(
                transaction_type=(
                    Transaction.TYPE_DAMAGED if damaged else Transaction.TYPE_RETURN
                ),
                serialized_item=item,
                work_order=work_order,
                reference_number=f"WO-{work_order.id}",
                user=request.user,
            )

            self._finalize_return_status(work_order)

        return Response(WorkOrderReturnSerializer(work_order).data, status=200)

    def _finalize_return_status(self, work_order):
        # Matches RETURNED_COUNT_ANNOTATION/STILL_OUT_COUNT_ANNOTATION's
        # definition (still out = anything but available or damaged -
        # WRH-57/AC-3) so a missing item doesn't silently vanish from this
        # count instead of keeping the WO at partially_returned, while a
        # damaged one no longer keeps the WO out of "returned" either. Safe
        # as a plain unlocked .count() only because the parent WorkOrder row
        # is already locked by the caller - every status-mutating action on
        # this WO (start/scan/complete/return_item/return_box) takes that
        # same lock first, so concurrent writers against this WO's items are
        # already serialized by the time this runs. Shared by return_item()
        # and return_box() (WRH-26) so both stay on one recompute.
        still_out = (
            SerializedItem.objects.filter(
                work_order_line_item__work_order_id=work_order.id
            )
            .exclude(status__in=NOT_STILL_OUT_STATUSES)
            .count()
        )
        work_order.status = (
            WorkOrder.STATUS_RETURNED
            if still_out == 0
            else WorkOrder.STATUS_PARTIALLY_RETURNED
        )
        work_order.save(update_fields=["status"])
        self._refresh_line_items(work_order, _active_line_items_queryset)

    @action(detail=True, methods=["post"], url_path="return-box")
    def return_box(self, request, pk=None):
        # WRH-26/AC-2/AC-3: return-context counterpart to scan_box() above -
        # every item in the box currently out on this WO is returned in one
        # call, each validated individually so one item's rejection doesn't
        # block its box-mates. No damaged-flag support here (WRH-57's
        # damaged=True is a single-item-scan-only refinement, not part of
        # this ticket's scope).
        work_order = self.get_object()
        if work_order.status not in RETURN_ELIGIBLE_STATUSES:
            raise ValidationError(
                {"status": ["Work order is not eligible for return."]}
            )

        serializer = WorkOrderReturnBoxSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        box_code = serializer.validated_data["box_code"]

        try:
            box = Box.objects.get(code=box_code)
        except Box.DoesNotExist:
            raise ValidationError({"box_code": ["Box not found"]})

        results = []
        with transaction.atomic():
            work_order = WorkOrder.objects.select_for_update().get(pk=work_order.pk)
            if work_order.status not in RETURN_ELIGIBLE_STATUSES:
                raise ValidationError(
                    {"status": ["Work order is not eligible for return."]}
                )

            for item in box.items.order_by("serial_number"):
                # WRH-28: no select_related("work_order_line_item") here -
                # that FK is nullable, so pairing it with select_for_update()
                # produces a LEFT OUTER JOIN Postgres refuses to lock ("FOR
                # UPDATE cannot be applied to the nullable side of an outer
                # join"), invisible on this repo's SQLite-backed CI. The
                # lazy follow-up query below only fires on the rare
                # not-issued-on-this-WO rejection path, matching WRH-27's
                # identical fix for BoxSerializer.create().
                locked_item = SerializedItem.objects.select_for_update().get(pk=item.pk)
                if (
                    locked_item.work_order_line_item_id is None
                    or locked_item.work_order_line_item.work_order_id != work_order.id
                ):
                    results.append(
                        {
                            "serial_number": locked_item.serial_number,
                            "added": False,
                            "reason": (
                                f"{locked_item.serial_number} was not issued on"
                                f" WO-{work_order.id}"
                            ),
                        }
                    )
                    continue
                if locked_item.status == SerializedItem.STATUS_DAMAGED:
                    # WRH-28/AC-3/TC-05: an item externally marked damaged
                    # mid-box must be flagged like any other non-returnable
                    # item, not folded into "added" - it wasn't actually
                    # returned here (no Transaction, status left untouched),
                    # so counting it as a success would both inflate the
                    # "N items added" summary and hide it from the flagged
                    # review list the AC asks for.
                    results.append(
                        {
                            "serial_number": locked_item.serial_number,
                            "added": False,
                            "reason": (
                                f"{locked_item.serial_number} is already marked"
                                " damaged"
                            ),
                        }
                    )
                    continue
                if locked_item.status != SerializedItem.STATUS_OUT:
                    results.append(
                        {
                            "serial_number": locked_item.serial_number,
                            "added": False,
                            "reason": (
                                f"{locked_item.serial_number} is not currently"
                                " out on this work order"
                            ),
                        }
                    )
                    continue

                locked_item.status = SerializedItem.STATUS_AVAILABLE
                locked_item.save(update_fields=["status"])
                Transaction.objects.create(
                    transaction_type=Transaction.TYPE_RETURN,
                    serialized_item=locked_item,
                    work_order=work_order,
                    reference_number=f"WO-{work_order.id}",
                    user=request.user,
                )
                results.append(
                    {
                        "serial_number": locked_item.serial_number,
                        "added": True,
                        "reason": "",
                    }
                )

            self._finalize_return_status(work_order)

        return Response(
            {
                "work_order": WorkOrderReturnSerializer(work_order).data,
                "box_summary": {
                    "code": box.code,
                    "added": sum(1 for result in results if result["added"]),
                    "results": results,
                },
            },
            status=200,
        )

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        # AC-1/AC-2: manually close a fulfilled/partially_returned WO even
        # with items still out - every remaining out item moves to
        # SerializedItem.STATUS_MISSING (surfaced as "Missing Items" by the
        # stock dashboard - ProductTypeStockSummarySerializer.get_missing())
        # rather than a separate list this action has to maintain itself.
        # AC-3: closing makes the WO read-only - already true for free, since
        # STATUS_CLOSED isn't in STATUS_DRAFT/STATUS_IN_PROGRESS/
        # RETURN_ELIGIBLE_STATUSES, the only states start()/scan()/
        # scan_box()/complete()/return_item()/return_box()/transfer() ever
        # accept. AC-4: a WO that happens to have zero items out at close
        # time (all already returned normally) closes cleanly with no items
        # moved and no Transaction rows created.
        work_order = self.get_object()
        if work_order.status not in CLOSE_ELIGIBLE_STATUSES:
            raise ValidationError(
                {"status": ["Work order is not eligible for closing."]}
            )

        with transaction.atomic():
            # Lock the parent WorkOrder row - same "lock the row whose
            # invariant you're protecting" reasoning as every other
            # status-mutating action on this model.
            work_order = WorkOrder.objects.select_for_update().get(pk=work_order.pk)
            if work_order.status not in CLOSE_ELIGIBLE_STATUSES:
                raise ValidationError(
                    {"status": ["Work order is not eligible for closing."]}
                )

            # Same "still out" definition _finalize_return_status uses -
            # everything but available/damaged.
            still_out_candidates = list(
                SerializedItem.objects.select_for_update()
                .filter(work_order_line_item__work_order_id=work_order.id)
                .exclude(status__in=NOT_STILL_OUT_STATUSES)
            )

            # Excludes an item transfer()'d away from this WO to another
            # one: transfer() deliberately never reassigns
            # work_order_line_item (see its own "no destination line item"
            # comment), so such an item still matches the filter above by
            # that stale FK even though it's legitimately out on the
            # *destination* WO now, not lost. TYPE_TRANSFER Transaction rows
            # alone aren't a reliable signal on their own - transfer()
            # writes one against *both* the source and destination WO for
            # every call, so a WO that only ever received a transfer (was
            # the destination) would be wrongly excluded too, and a plain
            # "any TRANSFER row for this WO+item" check can't tell those
            # apart. What actually matters is *recency*: if this WO's own
            # complete() later re-issued the item fresh (a TYPE_ISSUE row
            # for this WO, necessarily created after any transfer - the two
            # never share a bulk_create() call, so there's no tie to break),
            # that overrides an earlier transfer and the item is genuinely,
            # currently out here again - reachable e.g. via return_item()'s
            # own pre-existing "trusts the stale FK" limitation (see its
            # comment) letting an already-transferred item be returned on
            # the wrong WO and then legitimately re-scanned onto this one.
            last_relevant_type_by_item = {}
            for item_id, transaction_type in (
                Transaction.objects.filter(
                    work_order_id=work_order.id,
                    transaction_type__in=[
                        Transaction.TYPE_TRANSFER,
                        Transaction.TYPE_ISSUE,
                    ],
                    serialized_item_id__in=[item.id for item in still_out_candidates],
                )
                .order_by("created_at", "id")
                .values_list("serialized_item_id", "transaction_type")
            ):
                # Matches Transaction.Meta.ordering exactly (its own
                # declared canonical chronological order) rather than id
                # alone. Iterating in that order and overwriting per
                # item_id leaves each item's most recent (TRANSFER-vs-ISSUE)
                # row standing.
                last_relevant_type_by_item[item_id] = transaction_type

            still_out_items = [
                item
                for item in still_out_candidates
                if last_relevant_type_by_item.get(item.id) != Transaction.TYPE_TRANSFER
            ]
            if still_out_items:
                SerializedItem.objects.filter(
                    id__in=[item.id for item in still_out_items]
                ).update(status=SerializedItem.STATUS_MISSING)
                Transaction.objects.bulk_create(
                    [
                        Transaction(
                            transaction_type=Transaction.TYPE_MISSING,
                            serialized_item_id=item.id,
                            work_order=work_order,
                            reference_number=f"WO-{work_order.id}",
                            user=request.user,
                        )
                        for item in still_out_items
                    ]
                )

            work_order.status = WorkOrder.STATUS_CLOSED
            work_order.save(update_fields=["status"])
            self._refresh_line_items(work_order, _active_line_items_queryset)

        return Response(
            {
                "work_order": WorkOrderReturnSerializer(work_order).data,
                "missing_count": len(still_out_items),
            },
            status=200,
        )

    @action(detail=True, methods=["post"])
    def transfer(self, request, pk=None):
        # WRH-36/AC-1/AC-2: reassign a currently-out item from this (source)
        # work order to another (destination) - any WO, Primary or
        # Supplementary. Unlike scan()/return_item(), no destination line
        # item is chosen (the ticket's ACs/Test Cases never ask for one) -
        # this only updates the item's last_work_order_reference and logs
        # the move; it deliberately leaves work_order_line_item untouched,
        # matching return_item()'s own "current claim only, no history"
        # comment for that FK, since a destination line item to reassign it
        # to doesn't exist in this flow.
        source_work_order = self.get_object()
        if source_work_order.status not in RETURN_ELIGIBLE_STATUSES:
            raise ValidationError(
                {"status": ["Work order is not eligible for transfer."]}
            )

        serializer = WorkOrderTransferSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serial_number = serializer.validated_data["serial_number"]
        destination_work_order = serializer.validated_data["destination_work_order"]

        if destination_work_order.id == source_work_order.id:
            raise ValidationError(
                {"destination_work_order": ["Item is already on this work order."]}
            )

        # WRH-37/AC-4, WRH-40/AC-3: both of this model's terminal states
        # (_TERMINAL_STATUSES) are read-only, so neither can be a transfer
        # destination.
        if destination_work_order.status in _TERMINAL_STATUSES:
            raise ValidationError(
                {
                    "destination_work_order": [
                        "Destination work order is closed and cannot receive"
                        " transfers."
                    ]
                }
            )

        with transaction.atomic():
            # WRH-28: no select_related("work_order_line_item") here - that
            # FK is nullable, so pairing it with select_for_update() produces
            # a LEFT OUTER JOIN Postgres refuses to lock, invisible on this
            # repo's SQLite-backed CI. The lazy follow-up query below only
            # fires on the rejection paths, matching return_box()'s identical
            # fix.
            try:
                item = SerializedItem.objects.select_for_update().get(
                    serial_number=serial_number
                )
            except SerializedItem.DoesNotExist:
                raise ValidationError({"serial_number": ["Serial not found"]})

            if (
                item.work_order_line_item_id is None
                or item.work_order_line_item.work_order_id != source_work_order.id
            ):
                raise ValidationError(
                    {
                        "serial_number": [
                            f"{item.serial_number} is not currently out on"
                            f" WO-{source_work_order.id}"
                        ]
                    }
                )
            if item.status != SerializedItem.STATUS_OUT:
                raise ValidationError(
                    {
                        "serial_number": [
                            f"{item.serial_number} is not currently out and"
                            " cannot be transferred"
                        ]
                    }
                )

            source_reference = _work_order_reference(source_work_order)
            destination_reference = _work_order_reference(destination_work_order)

            item.last_work_order_reference = destination_reference
            item.save(update_fields=["last_work_order_reference"])

            # AC-3: two rows, not one - TransactionFilterSet.reference_number
            # is a single-field exact match (see its own comment), so "the
            # transfer entry appears in both WOs' logs" is only reachable by
            # writing one row per WO, each cross-referencing the other via
            # its note.
            Transaction.objects.bulk_create(
                [
                    Transaction(
                        transaction_type=Transaction.TYPE_TRANSFER,
                        serialized_item=item,
                        work_order=source_work_order,
                        reference_number=source_reference,
                        user=request.user,
                        note=f"Transferred to {destination_reference}",
                    ),
                    Transaction(
                        transaction_type=Transaction.TYPE_TRANSFER,
                        serialized_item=item,
                        work_order=destination_work_order,
                        reference_number=destination_reference,
                        user=request.user,
                        note=f"Transferred from {source_reference}",
                    ),
                ]
            )

        return Response(
            {
                "serial_number": item.serial_number,
                "status": item.status,
                "source_work_order": source_reference,
                "destination_work_order": destination_reference,
            },
            status=201,
        )

    @action(detail=True, methods=["get"], url_path="packing-list")
    def packing_list(self, request, pk=None):
        # WRH-34/AC-1/AC-3/AC-4: available from "in_progress" onward - PRD's
        # listed statuses (in_progress, fulfilled, partially_returned,
        # returned, closed) are every non-draft status this model can carry,
        # so the gate is "not draft" rather than an explicit allowlist -
        # WRH-40's STATUS_CLOSED satisfies this with no code change here too.
        # WRH-35/AC-2: requested on a supplementary directly - resolve to its
        # Primary and generate the identical consolidated document, rather
        # than rejecting (WRH-34's original behavior). Re-fetched through
        # get_queryset() so the Primary row carries the same
        # supplementaries+line_items prefetch shape the code below expects,
        # regardless of which row this action was entered on.
        work_order = self.get_object()
        if work_order.parent_work_order_id:
            work_order = self.get_queryset().get(pk=work_order.parent_work_order_id)
        if work_order.status == WorkOrder.STATUS_DRAFT:
            raise ValidationError(
                {"detail": "Packing list is available once fulfillment has started."}
            )

        def _section(wo):
            # WRH-34/AC-1/AC-3: serials "issued" against a line item are
            # read off work_order_line_item's reverse FK regardless of the
            # item's current status - matches this repo's existing "current
            # claim only, no history" design (see SerializedItem
            # .work_order_line_item's own comment) so a scan reflects
            # immediately (AC-3) and a later return/damage flag doesn't
            # retroactively erase it from what was issued (AC-4).
            return {
                "reference": _work_order_reference(wo),
                "job_name": wo.job_name,
                "client_name": wo.client_name,
                "expected_date_out": wo.expected_date_out,
                "line_items": [
                    {
                        "product_type_name": line_item.product_type.name,
                        "quantity": line_item.quantity,
                        "serial_numbers": [
                            item.serial_number
                            for item in line_item.serialized_items.all()
                        ],
                    }
                    for line_item in wo.line_items.all()
                ],
            }

        primary_reference = _work_order_reference(work_order)
        sections = [_section(work_order)] + [
            _section(supplementary)
            for supplementary in work_order.supplementaries.all()
        ]
        html = render_to_string(
            "inventory/packing_list_pdf.html",
            {"primary_reference": primary_reference, "sections": sections},
        )
        response = HttpResponse(
            HTML(string=html).write_pdf(), content_type="application/pdf"
        )
        response["Content-Disposition"] = (
            f'attachment; filename="packing-list-{primary_reference}.pdf"'
        )
        return response
