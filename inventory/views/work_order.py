from django.db import transaction
from django.db.models import (
    Count,
    Exists,
    OuterRef,
    Prefetch,
    Q,
    prefetch_related_objects,
)
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
    # WRH-40: written off (admin-settable today per its own model comment;
    # a future write-off endpoint's target status) is a resolved terminal
    # outcome exactly like damaged, not "still pending" - without this,
    # close()'s bulk sweep would silently clobber a written-off item back
    # to STATUS_MISSING, destroying the write-off record.
    SerializedItem.STATUS_WRITTEN_OFF,
    # WRH-46: same reasoning as STATUS_WRITTEN_OFF above - an item pulled
    # into a Maintenance Order mid-WO is a resolved-for-this-WO outcome,
    # not "still pending" on it; without this, close()'s bulk sweep would
    # silently clobber it back to STATUS_MISSING, destroying the
    # maintenance claim (SerializedItem.maintenance_order is PROTECT and
    # never cleared, but its status would go stale and misleading).
    SerializedItem.STATUS_IN_MAINTENANCE,
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


def _reject_supplementary_return(work_order):
    # WRH-80/AC-2/AC-4: return is only ever initiated from a Primary WO -
    # a supplementary's own detail/QR can still be scanned by a manager, so
    # this is a real API-level guard (not just a hidden/disabled frontend
    # button). parent_work_order is set once at creation and never changed
    # by any other endpoint, so a single check before the row lock is
    # enough - no re-check-after-lock needed (nothing can race it).
    if work_order.parent_work_order_id:
        raise ValidationError(
            {
                "work_order": [
                    "Return must be initiated from the parent work order,"
                    " not a supplementary."
                ]
            }
        )


def _returnable_work_order_ids(work_order):
    # WRH-80/AC-1/AC-3: a return on a Primary WO also covers every item
    # issued on its supplementaries - the set of WO ids a scanned serial is
    # allowed to belong to for this return call, and (this same set) what
    # the Primary's own consolidated still-out status is finalized against.
    #
    # Scoped to RETURN_ELIGIBLE_STATUSES (fulfilled/partially_returned)
    # supplementaries only - excludes two different kinds of supplementary
    # that must not participate in a consolidated return at all:
    #
    # - A supplementary that's already reached a _TERMINAL_STATUSES status
    #   (e.g. manually close()'d directly, on its own, with items still out
    #   - close() has no _reject_supplementary_return() guard, since
    #   closing a straggling supplementary independently is legitimate
    #   WRH-40 behavior) is done; its own STATUS_MISSING leftovers are a
    #   permanent, resolved outcome for that WO the same way
    #   NOT_STILL_OUT_STATUSES already treats STATUS_DAMAGED/
    #   STATUS_WRITTEN_OFF/STATUS_IN_MAINTENANCE - counting them against
    #   the *Primary* forever would mean a closed supplementary permanently
    #   blocks the Primary from ever reaching STATUS_RETURNED. Matches the
    #   "active" list's identical non_terminal_supplementary exclusion
    #   (WRH-69).
    # - A supplementary still mid-fulfillment (draft/in_progress - WRH-53
    #   supplementaries are created and fulfilled independently of their
    #   Primary, so one can still be in_progress while the Primary is
    #   already fulfilled) hasn't been through complete() yet. Its items
    #   can be STATUS_RESERVED (not excluded by NOT_STILL_OUT_STATUSES) or
    #   even STATUS_OUT if something transfer()'d an item onto it directly
    #   (transfer() only rejects a _TERMINAL_STATUSES destination, not a
    #   draft/in_progress one) - accepting either as part of the Primary's
    #   return would either inflate the still-out count with a claim that
    #   was never "issued" in the return sense, or force this
    #   not-yet-fulfilled supplementary straight to STATUS_RETURNED/
    #   STATUS_PARTIALLY_RETURNED, skipping its own WRH-54 fulfillment
    #   lifecycle entirely.
    return [work_order.id] + list(
        WorkOrder.objects.filter(
            parent_work_order_id=work_order.id,
            status__in=RETURN_ELIGIBLE_STATUSES,
        ).values_list("id", flat=True)
    )


def _item_owner_id(item):
    # WRH-82: the "read the owning WO id off a nullable FK" pattern
    # repeated verbatim (peeked-owner, actual-owner, box owner-set) across
    # return_item()/return_box() - a shared one-liner instead of
    # re-deriving it at each call site.
    return (
        item.work_order_line_item.work_order_id
        if item.work_order_line_item_id
        else None
    )


def _return_ownership_error(
    item, work_order, actual_owner_id, peeked_owner_id, allowed_wo_ids
):
    # WRH-82: shared classification for return_item()/return_box()'s
    # identical post-lock ownership re-check - out of scope entirely
    # (never issued on this Primary/its supplementaries) vs. reassigned to
    # a different (still in-scope) WO in the window between the unlocked
    # peek and the row lock. Returns None when ownership still checks out.
    if actual_owner_id is None or actual_owner_id not in allowed_wo_ids:
        return f"{item.serial_number} was not issued on WO-{work_order.id}"
    if actual_owner_id != peeked_owner_id:
        return (
            f"{item.serial_number} changed work orders while this return"
            " was in progress - please retry"
        )
    return None


def _refresh_return_summary(work_order):
    # WRH-80/AC-3: populates both work_order.line_items and
    # work_order.supplementaries (each with their own fresh line_items) in
    # one prefetch, for WorkOrderReturnSerializer's consolidated response -
    # matches _refresh_line_items()'s "populate the relation the response
    # serializer reads, no further query" reasoning, extended to also cover
    # the nested supplementaries relation.
    prefetch_related_objects(
        [work_order],
        Prefetch(
            "line_items",
            queryset=_active_line_items_queryset().filter(work_order_id=work_order.id),
        ),
        Prefetch(
            "supplementaries",
            queryset=WorkOrder.objects.order_by(
                "supplementary_sequence"
            ).prefetch_related(
                Prefetch("line_items", queryset=_active_line_items_queryset())
            ),
        ),
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
            # WRH-69: a terminal Primary is kept visible anyway if it still
            # has a non-terminal Supplementary nested under it - otherwise
            # close()/return_item() on the Primary alone silently drops a
            # still-active Supplementary off this list entirely, since
            # supplementaries are only ever reachable by nesting under
            # their Primary's row here.
            non_terminal_supplementary = WorkOrder.objects.filter(
                parent_work_order_id=OuterRef("pk")
            ).exclude(status__in=_TERMINAL_STATUSES)
            return (
                WorkOrder.objects.filter(parent_work_order__isnull=True)
                .annotate(has_active_supplementary=Exists(non_terminal_supplementary))
                .filter(
                    Q(has_active_supplementary=True) | ~Q(status__in=_TERMINAL_STATUSES)
                )
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
        _reject_supplementary_return(work_order)
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

            # WRH-80/AC-1/AC-3: a scanned serial is accepted if it was
            # issued on this Primary WO OR any of its supplementaries - a
            # return initiated from the Primary consolidates all of them
            # into one flow.
            allowed_wo_ids = _returnable_work_order_ids(work_order)

            # WRH-28/WRH-68: no select_related("work_order_line_item") here -
            # that FK is nullable, so pairing it with select_for_update()
            # produces a LEFT OUTER JOIN Postgres refuses to lock (crashes
            # on every call, not just a stale-FK one) - invisible on this
            # repo's SQLite-backed CI. Matches return_box()/transfer()'s
            # identical fix; this call site was flagged but left unfixed by
            # WRH-28 itself. This is an *unlocked* peek only, purely to
            # learn which WO owns the item before locking anything else -
            # see the WRH-80 lock-order comment right below.
            try:
                item_peek = SerializedItem.objects.select_related(
                    "work_order_line_item"
                ).get(serial_number=serial_number)
            except SerializedItem.DoesNotExist:
                raise ValidationError({"serial_number": ["Serial not found"]})
            peeked_owner_id = _item_owner_id(item_peek)

            # WRH-80: when the item is owned by a supplementary (not the
            # already-locked Primary), lock that supplementary's
            # WorkOrder row *before* the item row - transfer() (WRH-68)
            # and close() both already lock their WorkOrder row(s) before
            # any item row; locking the item first here would reverse
            # that order for exactly this owning-WO's row and risk an
            # AB-BA deadlock against a concurrent close() on it. Only lock
            # it if it's actually in scope (Primary + its supplementaries)
            # - an item owned by some unrelated WO is rejected below
            # without ever locking that unrelated WO's row, matching
            # return_box()'s identical scoping.
            if (
                peeked_owner_id is not None
                and peeked_owner_id in allowed_wo_ids
                and peeked_owner_id != work_order.id
            ):
                owning_work_order = WorkOrder.objects.select_for_update().get(
                    pk=peeked_owner_id
                )
            else:
                owning_work_order = work_order

            item = SerializedItem.objects.select_for_update().get(pk=item_peek.pk)

            # Re-check ownership against the now-locked row - it can have
            # changed in the window between the unlocked peek above and
            # this lock (e.g. a concurrent scan() reassigning it), same
            # "re-check after acquiring the lock" convention as the
            # archived-product-type guard (WRH-56). WRH-80: "was not
            # issued" message text deliberately unchanged from before that
            # ticket (no "or its supplementaries" suffix) - the frontend's
            # NOT_ISSUED_PATTERN regex is end-anchored on exactly this
            # "...WO-<id>" shape. The "changed work orders" branch covers
            # reassignment to a different (still allowed) WO between the
            # peek and the lock - owning_work_order was locked for the
            # wrong row, and rather than lock another row inside this
            # already-open transaction (which could itself deadlock), this
            # rejects the scan cleanly; a retry re-peeks and locks
            # correctly.
            actual_owner_id = _item_owner_id(item)
            ownership_error = _return_ownership_error(
                item, work_order, actual_owner_id, peeked_owner_id, allowed_wo_ids
            )
            if ownership_error:
                raise ValidationError({"serial_number": [ownership_error]})
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
                _refresh_return_summary(work_order)
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

            # WRH-80/AC-6: attribute the Transaction to the item's actual
            # owning WO (Primary or the specific supplementary it was
            # issued on), not always to the Primary the return was
            # initiated from - keeps history traceable per AC-6 even
            # though the scan itself came in through the Primary.
            # reference_number uses _work_order_reference() (not a bare
            # f"WO-{id}") - owning_work_order can now genuinely be a
            # supplementary, and transfer() already establishes that a
            # supplementary's reference_number must render as its display
            # identifier ("WO-<primary>-S<seq>"), not its own raw pk,
            # since TransactionFilterSet.reference_number is an exact
            # match against that display string.
            Transaction.objects.create(
                transaction_type=(
                    Transaction.TYPE_DAMAGED if damaged else Transaction.TYPE_RETURN
                ),
                serialized_item=item,
                work_order=owning_work_order,
                reference_number=_work_order_reference(owning_work_order),
                user=request.user,
            )

            # WRH-80: the owning supplementary's own status reflects only
            # its own items (unchanged from before this ticket - other
            # actions/the active list read it that way). The entry-point
            # Primary's status is finalized separately from the
            # *consolidated* count across itself and every supplementary -
            # not just its own items - otherwise the Primary could reach
            # STATUS_RETURNED while a supplementary still has items out,
            # which then fails this action's own RETURN_ELIGIBLE_STATUSES
            # re-check on the next call and blocks the rest of the
            # consolidated return entirely.
            # refresh=False on both calls: neither owning_work_order's nor
            # work_order's own line_items cache is what the response reads
            # from - _refresh_return_summary(work_order) below repopulates
            # work_order.line_items and a fresh work_order.supplementaries
            # (including this owning WO, if it's one) from scratch anyway.
            # WRH-82: reuse allowed_wo_ids (computed once above) rather
            # than re-querying it - owning_work_order's own finalize just
            # above can only move it to STATUS_RETURNED (0 items left out,
            # so its membership in this list no longer affects the count
            # either way) or leave it at STATUS_PARTIALLY_RETURNED
            # (unchanged membership), so a stale list here is equivalent
            # to a freshly recomputed one.
            if owning_work_order.id != work_order.id:
                self._finalize_return_status(owning_work_order, refresh=False)
            self._finalize_return_status(
                work_order,
                allowed_wo_ids,
                refresh=False,
            )
            _refresh_return_summary(work_order)

        return Response(WorkOrderReturnSerializer(work_order).data, status=200)

    def _finalize_return_status(self, work_order, work_order_ids=None, refresh=True):
        # Matches RETURNED_COUNT_ANNOTATION/STILL_OUT_COUNT_ANNOTATION's
        # definition (still out = anything but available or damaged -
        # WRH-57/AC-3) so a missing item doesn't silently vanish from this
        # count instead of keeping the WO at partially_returned, while a
        # damaged one no longer keeps the WO out of "returned" either. Safe
        # as a plain unlocked .count() for `work_order` itself - its own
        # row is always locked by the caller before this runs. WRH-80's
        # consolidated Primary call additionally counts across every
        # RETURN_ELIGIBLE_STATUSES supplementary (see
        # _returnable_work_order_ids()) - only the specific
        # supplementary(ies) this request actually touched are locked, not
        # every supplementary in scope, so an untouched one isn't fully
        # serialized against by this count. A concurrent transfer() off an
        # untouched supplementary (it only locks its *destination* WO, not
        # the source) is one gap this doesn't close - an ordinary
        # read-committed race, not a corrupted value, since the recompute
        # just lands on whichever committed state happens to be visible;
        # acceptable today, flagged rather than silently assumed away.
        # Shared by return_item() and return_box() (WRH-26) so both stay on
        # one recompute.
        #
        # WRH-80: work_order_ids defaults to just this WO's own id (the
        # original, pre-consolidation behavior - what a supplementary's own
        # status, or a standalone WO's, is always finalized against). The
        # entry-point Primary of a consolidated return instead finalizes
        # against _returnable_work_order_ids() (see return_item()/
        # return_box()'s own callers) - otherwise the Primary could reach
        # STATUS_RETURNED from only its own items while a supplementary
        # still has items out, which would then fail this action's
        # RETURN_ELIGIBLE_STATUSES re-check on the very next call and block
        # the rest of the consolidated return. refresh=False lets a caller
        # that's about to run _refresh_return_summary() anyway (which
        # re-populates the same line_items relation, plus supplementaries)
        # skip this method's own redundant prefetch.
        ids = work_order_ids if work_order_ids is not None else [work_order.id]
        still_out = (
            SerializedItem.objects.filter(work_order_line_item__work_order_id__in=ids)
            .exclude(status__in=NOT_STILL_OUT_STATUSES)
            .count()
        )
        work_order.status = (
            WorkOrder.STATUS_RETURNED
            if still_out == 0
            else WorkOrder.STATUS_PARTIALLY_RETURNED
        )
        work_order.save(update_fields=["status"])
        if refresh:
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
        _reject_supplementary_return(work_order)
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

            # WRH-80/AC-1/AC-3: same Primary+supplementaries scope as
            # return_item(); a box can hold items issued on more than one of
            # them. locked_wos accumulates every distinct owning WO this box
            # actually touches, so each gets its own status finalized below -
            # keyed by id, seeded with the already-locked Primary.
            allowed_wo_ids = _returnable_work_order_ids(work_order)
            locked_wos = {work_order.id: work_order}
            # WRH-80: distinct from locked_wos - only WOs that actually had
            # an item returned this call, not every WO merely peeked/locked
            # (see the finalize loop below for why that distinction
            # matters).
            actually_returned_wo_ids = set()

            # select_related() is safe here (unlike the select_for_update()
            # calls elsewhere in this file) - this is a plain unlocked
            # read, not paired with a lock, so the nullable-FK/outer-join
            # restriction that rules it out there doesn't apply.
            box_items = list(
                box.items.select_related("work_order_line_item").order_by(
                    "serial_number"
                )
            )

            # WRH-80: unlocked peek of every box item's owning WO first, so
            # every distinct owning WO can be locked *before* any item row -
            # transfer() (WRH-68) and close() both already lock their
            # WorkOrder row(s) before any item row; locking an item first
            # and only then a not-yet-locked owning WO (as a single-item
            # return_item() scan would if it worked the same way) risks an
            # AB-BA deadlock against a concurrent close() on that WO. Locked
            # in ascending id order so two concurrent return_box() calls
            # touching an overlapping set of WOs can't deadlock against each
            # other either.
            # Only WOs in allowed_wo_ids (the Primary + its supplementaries)
            # get locked here - an item belonging to some unrelated WO is
            # out of scope for this return call entirely (rejected below,
            # same as return_item()'s identical rejection), so that WO must
            # never be locked or have its status touched by this call.
            # WRH-82: kept per-item (not just the deduped id set below) so
            # each item's own post-lock recheck can tell "reassigned since
            # the peek" apart from "never in scope" - same distinction
            # return_item() draws via its single peeked_owner_id.
            peeked_owner_by_item_id = {
                item.pk: _item_owner_id(item) for item in box_items
            }
            peeked_owner_ids = {
                owner_id
                for owner_id in peeked_owner_by_item_id.values()
                if owner_id is not None
            }
            for owning_wo_id in sorted(peeked_owner_ids):
                if owning_wo_id in allowed_wo_ids and owning_wo_id not in locked_wos:
                    locked_wos[owning_wo_id] = (
                        WorkOrder.objects.select_for_update().get(pk=owning_wo_id)
                    )

            for item in box_items:
                locked_item = SerializedItem.objects.select_for_update().get(pk=item.pk)
                # Re-check ownership against the now-locked row - it can
                # have changed since the unlocked peek above (e.g. a
                # concurrent scan() reassigning it), same "re-check after
                # acquiring the lock" convention as the archived-guard
                # (WRH-56) and the same shared classification return_item()
                # uses (WRH-82) - "not issued" for out-of-scope, "changed
                # work orders...retry" for a reassignment within scope that
                # this call didn't peek/lock for. A reassignment to a WO
                # this call didn't peek (and so didn't lock) is rejected
                # rather than locked here, for the same reason return_item()
                # rejects instead of locking mid-loop - it necessarily
                # differs from its own peeked_owner_id, so
                # _return_ownership_error catches it without needing an
                # explicit "not in locked_wos" check.
                actual_owner_id = _item_owner_id(locked_item)
                ownership_error = _return_ownership_error(
                    locked_item,
                    work_order,
                    actual_owner_id,
                    peeked_owner_by_item_id[item.pk],
                    allowed_wo_ids,
                )
                if ownership_error:
                    results.append(
                        {
                            "serial_number": locked_item.serial_number,
                            "added": False,
                            "reason": ownership_error,
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

                # WRH-80/AC-6: attribute this Transaction to the item's
                # actual owning WO (Primary or the specific supplementary) -
                # already locked above, before any item row, alongside
                # every other owning WO this box touches. reference_number
                # uses _work_order_reference() - see return_item()'s
                # identical comment for why a bare f"WO-{id}" is wrong once
                # owning_work_order can be a supplementary.
                owning_work_order = locked_wos[actual_owner_id]
                actually_returned_wo_ids.add(actual_owner_id)

                Transaction.objects.create(
                    transaction_type=Transaction.TYPE_RETURN,
                    serialized_item=locked_item,
                    work_order=owning_work_order,
                    reference_number=_work_order_reference(owning_work_order),
                    user=request.user,
                )
                results.append(
                    {
                        "serial_number": locked_item.serial_number,
                        "added": True,
                        "reason": "",
                    }
                )

            # WRH-80: same split as return_item() - each supplementary that
            # actually had an item returned this call finalizes against its
            # own items only, while the entry Primary finalizes against the
            # consolidated Primary+supplementaries count (see
            # _finalize_return_status()'s own comment for why that split
            # matters). Deliberately narrower than locked_wos - a
            # supplementary merely *peeked* (an item of its shared this box
            # but was rejected, e.g. already returned/closed elsewhere)
            # never had this call add or change anything on it and must not
            # have its status recomputed - a closed supplementary in
            # particular would get silently un-closed by a recompute that
            # sees its pre-existing STATUS_MISSING items as still-out.
            # refresh=False throughout: none of these instances' own
            # line_items caches are what the response reads from -
            # _refresh_return_summary(work_order) below repopulates
            # work_order.line_items and a fresh work_order.supplementaries
            # (including every touched supplementary) from scratch anyway.
            # WRH-82: reuse allowed_wo_ids (computed once above) - same
            # reasoning as return_item()'s identical reuse.
            for touched_id in actually_returned_wo_ids:
                if touched_id != work_order.id:
                    self._finalize_return_status(locked_wos[touched_id], refresh=False)
            self._finalize_return_status(
                work_order,
                allowed_wo_ids,
                refresh=False,
            )
            _refresh_return_summary(work_order)

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
            # everything but available/damaged. of=("self",) restricts the
            # lock to SerializedItem rows - unlike every other
            # select_for_update() in this file (a plain single-table
            # .get(pk=...)/.get(serial_number=...)), this filter joins to
            # WorkOrderLineItem, and FOR UPDATE locks every joined table by
            # default without it.
            still_out_candidates = list(
                SerializedItem.objects.select_for_update(of=("self",))
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
                # Deliberately ascending (oldest first), NOT
                # Transaction.Meta.ordering (WRH-78 made that
                # newest-first) - iterating oldest-to-newest and
                # overwriting per item_id leaves each item's most recent
                # (TRANSFER-vs-ISSUE) row standing. Do not drop this
                # explicit order_by() to rely on the model default; that
                # would silently flip iteration order and corrupt this
                # dict with each item's earliest type instead of latest.
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
            # WRH-80: WorkOrderReturnSerializer (used below) now also reads
            # a supplementaries field - _refresh_return_summary() populates
            # both that and line_items in one prefetch, instead of leaving
            # supplementaries to fall back to an unannotated default-manager
            # query (which would render its returned/damaged/still_out
            # counts as 0 via WorkOrderActiveLineItemSerializer's getattr
            # fallback, not this WO's real numbers).
            _refresh_return_summary(work_order)

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
        # Supplementary.
        # WRH-68: destination_line_item (required, see the serializer's own
        # comment) is now reassigned onto item.work_order_line_item, not
        # just item.last_work_order_reference - leaving the FK stale meant
        # return_item()'s ownership check (the only thing that reads it)
        # rejected a correct return on the true (destination) WO and
        # silently accepted an incorrect one on the stale source WO.
        source_work_order = self.get_object()
        if source_work_order.status not in RETURN_ELIGIBLE_STATUSES:
            raise ValidationError(
                {"status": ["Work order is not eligible for transfer."]}
            )

        serializer = WorkOrderTransferSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serial_number = serializer.validated_data["serial_number"]
        destination_work_order = serializer.validated_data["destination_work_order"]
        destination_line_item = serializer.validated_data["destination_line_item"]

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
            # WRH-68: lock the destination WorkOrder row *before* the item
            # row - every other lock-acquiring action in this file
            # (scan()/return_item()/return_box()/close()) locks its WO row
            # first, then the item row; locking the item first here would
            # invert that ordering and open a deadlock cycle against e.g. a
            # concurrent scan() on the same destination WO (which locks the
            # WO, then blocks on the item) racing this transfer() (which
            # would lock the item, then block on the WO). Also re-checked
            # for terminal status under the lock, matching scan()'s "guard
            # re-checked after lock" pattern - the pre-lock check above only
            # proves it wasn't terminal before this transaction started.
            destination_work_order = WorkOrder.objects.select_for_update().get(
                pk=destination_work_order.pk
            )
            if destination_work_order.status in _TERMINAL_STATUSES:
                raise ValidationError(
                    {
                        "destination_work_order": [
                            "Destination work order is closed and cannot"
                            " receive transfers."
                        ]
                    }
                )

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

            # No select_related("product_type") - only product_type_id is
            # read below, never the related row itself.
            destination_line_item = WorkOrderLineItem.objects.get(
                pk=destination_line_item.pk
            )
            if destination_line_item.work_order_id != destination_work_order.id:
                raise ValidationError(
                    {
                        "destination_line_item": [
                            "Destination line item does not belong to the"
                            " destination work order."
                        ]
                    }
                )
            if destination_line_item.product_type_id != item.product_type_id:
                raise ValidationError(
                    {
                        "destination_line_item": [
                            "Destination line item's product type does not"
                            " match the transferred item."
                        ]
                    }
                )
            # AC-2/AC-4 parity with scan(): reject once the destination line
            # item's requested quantity is already claimed.
            claimed_count = SerializedItem.objects.filter(
                work_order_line_item=destination_line_item
            ).count()
            if claimed_count >= destination_line_item.quantity:
                raise ValidationError(
                    {
                        "destination_line_item": [
                            "This line item has already reached its requested"
                            " quantity."
                        ]
                    }
                )

            source_reference = _work_order_reference(source_work_order)
            destination_reference = _work_order_reference(destination_work_order)

            item.work_order_line_item = destination_line_item
            item.last_work_order_reference = destination_reference
            item.save(
                update_fields=["work_order_line_item", "last_work_order_reference"]
            )

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
