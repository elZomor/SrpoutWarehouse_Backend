from django.db import transaction
from rest_framework import serializers

from inventory.models import ProductType, SerializedItem, WorkOrder, WorkOrderLineItem
from inventory.models.work_order import (
    DAMAGED_COUNT_ANNOTATION,
    RETURNED_COUNT_ANNOTATION,
    SCANNED_COUNT_ANNOTATION,
    STILL_OUT_COUNT_ANNOTATION,
)


class WorkOrderLineItemSerializer(serializers.ModelSerializer):
    # Archived product types shouldn't be requestable on a new WO - matches
    # SerializedItemSerializer.product_type / PurchaseOrderReceiveSerializer's
    # line_item restriction on the same invariant (WRH-21).
    product_type = serializers.PrimaryKeyRelatedField(
        queryset=ProductType.objects.filter(archived=False),
        error_messages={
            "required": "Product type is required.",
            "null": "Product type is required.",
            "does_not_exist": "Select a product type that exists and is not archived.",
        },
    )
    product_type_name = serializers.CharField(
        source="product_type.name", read_only=True
    )
    scanned_quantity = serializers.SerializerMethodField()
    remaining_quantity = serializers.SerializerMethodField()

    class Meta:
        model = WorkOrderLineItem
        fields = [
            "id",
            "product_type",
            "product_type_name",
            "quantity",
            "scanned_quantity",
            "remaining_quantity",
        ]
        # WRH-32/AC-4: the model's PositiveIntegerField alone would still
        # accept 0 (Django's positive-integer validator floors at 0, not 1)
        # - min_value here is what actually rejects zero/negative
        # quantities, matching PurchaseOrderLineItemSerializer.
        # expected_quantity's identical extra_kwargs pattern for adding
        # validation to a model-derived field.
        extra_kwargs = {
            "quantity": {
                "min_value": 1,
                "error_messages": {
                    "required": "Quantity must be greater than zero.",
                    "min_value": "Quantity must be greater than zero.",
                },
            },
        }

    def get_scanned_quantity(self, obj):
        # AC-2: "a live counter updates per line item, e.g. 23/50 scanned" -
        # derived from linked SerializedItems (reserved or out both count -
        # a scan claims progress immediately, confirmation only flips their
        # status) rather than a stored counter, mirroring
        # PurchaseOrderLineItemSerializer.get_received_quantity()'s same
        # reasoning. Every queryset that reaches this serializer (list,
        # scan, complete) attaches SCANNED_COUNT_ANNOTATION to avoid an N+1
        # COUNT per line item; the plain .serialized_items.count() fallback
        # only exists for WorkOrderSerializer.create()'s response, where the
        # just-bulk_created line items are guaranteed zero scans.
        scanned = getattr(obj, SCANNED_COUNT_ANNOTATION, None)
        return scanned if scanned is not None else obj.serialized_items.count()

    def get_remaining_quantity(self, obj):
        return max(obj.quantity - self.get_scanned_quantity(obj), 0)


def _work_order_reference(obj):
    # WRH-53/AC-1/AC-2: the "WO-0042"/"WO-0042-S1" display identifier -
    # shared by every serializer below that surfaces it, so the format only
    # lives in one place. A Primary WO is just "WO-<id>" (matches the
    # pre-existing f"WO-{work_order.id}" convention used elsewhere, e.g.
    # Transaction.reference_number in views/work_order.py); a supplementary
    # appends "-S<sequence>" using its parent's id, not its own.
    if obj.parent_work_order_id:
        return f"WO-{obj.parent_work_order_id}-S{obj.supplementary_sequence}"
    return f"WO-{obj.id}"


class WorkOrderSerializer(serializers.ModelSerializer):
    # ModelSerializer's nested-write support stops at validation - create()
    # below has to build the line items itself, DRF won't do it implicitly.
    line_items = WorkOrderLineItemSerializer(many=True)
    created_by_username = serializers.CharField(
        source="created_by.username", read_only=True
    )
    # WRH-53/AC-1/AC-2: optional - creates a supplementary linked to an
    # existing Primary when provided. The queryset restriction
    # (parent_work_order__isnull=True) both scopes the field to real
    # Primaries and enforces WRH-33/AC-6 ("a supplementary cannot itself be
    # a parent") for free, matching the archived-product-type
    # queryset-restriction convention used elsewhere in this file rather
    # than a fresh manual check.
    parent_work_order = serializers.PrimaryKeyRelatedField(
        queryset=WorkOrder.objects.filter(parent_work_order__isnull=True),
        required=False,
        allow_null=True,
        error_messages={
            "does_not_exist": (
                "Select a work order that exists and is not itself a " "supplementary."
            ),
        },
    )
    reference = serializers.SerializerMethodField()

    class Meta:
        model = WorkOrder
        fields = [
            "id",
            "reference",
            "job_name",
            "client_name",
            "expected_date_out",
            "status",
            "created_by",
            "created_by_username",
            "parent_work_order",
            "line_items",
        ]
        read_only_fields = ["status", "created_by"]

    def get_reference(self, obj):
        return _work_order_reference(obj)

    def validate_line_items(self, value):
        # AC-1: a WO is created "with ... line items (product type +
        # quantity)" - an empty list isn't a valid WO, matching
        # PurchaseOrderSerializer's identical requirement.
        if not value:
            raise serializers.ValidationError("At least one line item is required.")
        return value

    def create(self, validated_data):
        line_items_data = validated_data.pop("line_items")
        parent_work_order = validated_data.pop("parent_work_order", None)
        # Both writes must land together - a bulk_create() failure (e.g. a
        # DB-level constraint one of the line items happens to violate)
        # would otherwise leave a persisted WorkOrder with zero line items,
        # violating validate_line_items()'s "at least one" invariant.
        with transaction.atomic():
            if parent_work_order is not None:
                # AC-1/AC-2: lock the Primary row so two concurrent
                # supplementary creates against the same Primary can't both
                # read the same sibling count and assign the same next
                # sequence number - matches this repo's "lock the row whose
                # invariant you're protecting" convention (WRH-56) rather
                # than a bare check-then-act count.
                parent_work_order = WorkOrder.objects.select_for_update().get(
                    pk=parent_work_order.pk
                )
                validated_data["parent_work_order"] = parent_work_order
                validated_data["supplementary_sequence"] = (
                    WorkOrder.objects.filter(
                        parent_work_order=parent_work_order
                    ).count()
                    + 1
                )
            work_order = WorkOrder.objects.create(**validated_data)
            WorkOrderLineItem.objects.bulk_create(
                WorkOrderLineItem(work_order=work_order, **line_item)
                for line_item in line_items_data
            )
        return work_order


class WorkOrderScanSerializer(serializers.Serializer):
    # Input-only (AC-2): one scanned serial against one line item per call,
    # matching PurchaseOrderReceiveSerializer's identical shape for the same
    # scan-gun-driven flow. Box-QR scanning (AC-3) is its own serializer/
    # action (WorkOrderScanBoxSerializer/WorkOrderViewSet.scan_box(), WRH-26)
    # rather than an alternate input shape here.
    #
    # Archived product types are excluded the same way
    # WorkOrderLineItemSerializer.product_type restricts them at create time
    # - a line item's product type can't be archived after the WO is
    # created (no archive-after-create path exists), but the queryset
    # mirrors the established pattern regardless.
    line_item = serializers.PrimaryKeyRelatedField(
        queryset=WorkOrderLineItem.objects.filter(product_type__archived=False),
        error_messages={
            "required": "Line item is required.",
            "does_not_exist": (
                "Select a line item whose product type exists and is not archived."
            ),
        },
    )
    serial_number = serializers.CharField(
        error_messages={
            "blank": "Serial number is required.",
            "required": "Serial number is required.",
        },
    )


class WorkOrderScanBoxSerializer(serializers.Serializer):
    # WRH-26/AC-2: scanning a Box's code on the fulfillment screen expands
    # to every item inside in one call, each validated individually the
    # same way WorkOrderScanSerializer validates a single serial - see
    # WorkOrderViewSet.scan_box(). Receive-context box scanning has no
    # equivalent serializer - a receive scan creates brand-new
    # SerializedItems, which can't have been pre-boxed as "available" first
    # (a box can only ever hold items that already exist), so it's out of
    # scope for this ticket.
    box_code = serializers.CharField(
        error_messages={
            "blank": "Box code is required.",
            "required": "Box code is required.",
        },
    )


class WorkOrderReturnBoxSerializer(serializers.Serializer):
    # WRH-26/AC-2: return-context counterpart to WorkOrderScanBoxSerializer -
    # see WorkOrderViewSet.return_box().
    box_code = serializers.CharField(
        error_messages={
            "blank": "Box code is required.",
            "required": "Box code is required.",
        },
    )


class WorkOrderReturnScanSerializer(serializers.Serializer):
    # Input-only (AC-1/AC-2/AC-4): one scanned serial per call against
    # whichever line item it was originally issued against - matches
    # WorkOrderScanSerializer's identical scan-gun-driven shape. Box-QR
    # return (AC-3 of WRH-38) is its own serializer/action
    # (WorkOrderReturnBoxSerializer/WorkOrderViewSet.return_box(), WRH-26)
    # rather than an alternate input shape here.
    serial_number = serializers.CharField(
        error_messages={
            "blank": "Serial number is required.",
            "required": "Serial number is required.",
        },
    )
    # WRH-57/AC-1: optional - defaults False so existing plain-return callers
    # (and WRH-38/WRH-39's own tests) are unaffected; True flags this unit
    # damaged instead of returning it to available stock.
    damaged = serializers.BooleanField(required=False, default=False)


class WorkOrderTransferSerializer(serializers.Serializer):
    # WRH-36/AC-1/AC-2: one scanned serial (currently out on the source WO
    # in the URL) plus its destination WO - any WO, Primary or Supplementary
    # (PRD v0.5 SS8 Open Question #6), matching WorkOrderSerializer.
    # parent_work_order's "no queryset restriction needed beyond existing"
    # reasoning: unlike parent_work_order, a transfer destination has no
    # "must be a Primary" rule to enforce, so the queryset is unrestricted.
    #
    # WRH-68: destination_line_item is required so the FK the item actually
    # carries (work_order_line_item) can be reassigned to the destination -
    # mirrors WorkOrderScanSerializer's identical explicit-selection shape
    # rather than scan_box()'s auto-match-by-product-type shape, since a
    # destination WO can have more than one line item of the transferred
    # item's product type and there's no unambiguous way to pick one.
    serial_number = serializers.CharField(
        error_messages={
            "blank": "Serial number is required.",
            "required": "Serial number is required.",
        },
    )
    destination_work_order = serializers.PrimaryKeyRelatedField(
        queryset=WorkOrder.objects.all(),
        error_messages={
            "required": "Destination work order is required.",
            "does_not_exist": "Select a work order that exists.",
        },
    )
    destination_line_item = serializers.PrimaryKeyRelatedField(
        queryset=WorkOrderLineItem.objects.filter(product_type__archived=False),
        error_messages={
            "required": "Destination line item is required.",
            "does_not_exist": (
                "Select a line item whose product type exists and is not archived."
            ),
        },
    )


class WorkOrderActiveLineItemSerializer(serializers.ModelSerializer):
    # WRH-55/AC-2 (returned/still_out), WRH-57/AC-2/AC-3 (damaged) - "per-
    # type returned vs. damaged vs. still-out counts" - see
    # RETURNED_COUNT_ANNOTATION's model-level comment for why these differ
    # from scanned_quantity/remaining_quantity.
    product_type_name = serializers.CharField(
        source="product_type.name", read_only=True
    )
    returned_quantity = serializers.SerializerMethodField()
    damaged_quantity = serializers.SerializerMethodField()
    still_out_quantity = serializers.SerializerMethodField()

    class Meta:
        model = WorkOrderLineItem
        fields = [
            "id",
            "product_type",
            "product_type_name",
            "quantity",
            "returned_quantity",
            "damaged_quantity",
            "still_out_quantity",
        ]

    def get_returned_quantity(self, obj):
        return getattr(obj, RETURNED_COUNT_ANNOTATION, 0)

    def get_damaged_quantity(self, obj):
        return getattr(obj, DAMAGED_COUNT_ANNOTATION, 0)

    def get_still_out_quantity(self, obj):
        return getattr(obj, STILL_OUT_COUNT_ANNOTATION, 0)


class WorkOrderActiveSupplementarySerializer(serializers.ModelSerializer):
    # AC-1: a supplementary nested beneath its Primary - same per-WO summary
    # shape as WorkOrderActiveSerializer below, minus its own
    # "supplementaries" field (supplementaries are one level deep only -
    # there's no path to create a supplementary-of-a-supplementary).
    line_items = WorkOrderActiveLineItemSerializer(many=True, read_only=True)
    # WRH-53/AC-1/AC-2: "WO-0042-S1" shown alongside the Primary it's nested
    # under - no extra query, reads plain columns already on the fetched
    # row (see _work_order_reference).
    reference = serializers.SerializerMethodField()

    class Meta:
        model = WorkOrder
        fields = [
            "id",
            "reference",
            "job_name",
            "client_name",
            "expected_date_out",
            "status",
            "line_items",
        ]

    def get_reference(self, obj):
        return _work_order_reference(obj)


class WorkOrderActiveSerializer(serializers.ModelSerializer):
    # WRH-55: the "Active Work Orders" list - Primary WOs (parent_work_order
    # is null) with their supplementaries nested beneath (AC-1) and
    # per-type returned/still-out summary counts (AC-2).
    line_items = WorkOrderActiveLineItemSerializer(many=True, read_only=True)
    supplementaries = WorkOrderActiveSupplementarySerializer(many=True, read_only=True)
    reference = serializers.SerializerMethodField()

    class Meta:
        model = WorkOrder
        fields = [
            "id",
            "reference",
            "job_name",
            "client_name",
            "expected_date_out",
            "status",
            "line_items",
            "supplementaries",
        ]

    def get_reference(self, obj):
        return _work_order_reference(obj)


class WorkOrderReturnSerializer(serializers.ModelSerializer):
    # WRH-38/AC-1/AC-2: "a summary is shown: Returned / Still missing";
    # WRH-57/AC-2: the summary "clearly separates Returned / Damaged / Still
    # missing" - reuses WorkOrderActiveLineItemSerializer's
    # returned_quantity/damaged_quantity/still_out_quantity fields
    # (WRH-55/AC-2, WRH-57/AC-2), populated for real once return_item()
    # starts flipping items back to available or damaged.
    line_items = WorkOrderActiveLineItemSerializer(many=True, read_only=True)
    # WRH-80/AC-3: a return initiated on a parent WO is consolidated across
    # it and every supplementary - same nested shape the "active" list
    # already uses, so a caller sees each supplementary's own summary
    # (including its own now-updated status) alongside the parent's.
    supplementaries = WorkOrderActiveSupplementarySerializer(many=True, read_only=True)

    class Meta:
        model = WorkOrder
        fields = ["id", "job_name", "status", "line_items", "supplementaries"]


class WorkOrderDetailSerializedItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = SerializedItem
        fields = ["id", "serial_number", "status"]


class WorkOrderDetailLineItemSerializer(serializers.ModelSerializer):
    # WRH-55/AC-3: drill-down - the exact serials issued on this line item
    # and their current statuses, not just aggregate counts.
    product_type_name = serializers.CharField(
        source="product_type.name", read_only=True
    )
    serialized_items = WorkOrderDetailSerializedItemSerializer(
        many=True, read_only=True
    )

    class Meta:
        model = WorkOrderLineItem
        fields = [
            "id",
            "product_type",
            "product_type_name",
            "quantity",
            "serialized_items",
        ]


class WorkOrderDetailSerializer(serializers.ModelSerializer):
    line_items = WorkOrderDetailLineItemSerializer(many=True, read_only=True)
    created_by_username = serializers.CharField(
        source="created_by.username", read_only=True
    )
    # TC-03: a supplementary's detail view shows its own reference, distinct
    # from the Primary's.
    reference = serializers.SerializerMethodField()

    class Meta:
        model = WorkOrder
        fields = [
            "id",
            "reference",
            "job_name",
            "client_name",
            "expected_date_out",
            "status",
            "created_by",
            "created_by_username",
            "parent_work_order",
            "line_items",
        ]

    def get_reference(self, obj):
        return _work_order_reference(obj)
