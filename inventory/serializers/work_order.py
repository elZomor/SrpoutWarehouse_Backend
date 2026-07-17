from django.db import transaction
from rest_framework import serializers

from inventory.models import ProductType, SerializedItem, WorkOrder, WorkOrderLineItem
from inventory.models.work_order import (
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


class WorkOrderSerializer(serializers.ModelSerializer):
    # ModelSerializer's nested-write support stops at validation - create()
    # below has to build the line items itself, DRF won't do it implicitly.
    line_items = WorkOrderLineItemSerializer(many=True)
    created_by_username = serializers.CharField(
        source="created_by.username", read_only=True
    )

    class Meta:
        model = WorkOrder
        fields = [
            "id",
            "job_name",
            "client_name",
            "expected_date_out",
            "status",
            "created_by",
            "created_by_username",
            "line_items",
        ]
        read_only_fields = ["status", "created_by"]

    def validate_line_items(self, value):
        # AC-1: a WO is created "with ... line items (product type +
        # quantity)" - an empty list isn't a valid WO, matching
        # PurchaseOrderSerializer's identical requirement.
        if not value:
            raise serializers.ValidationError("At least one line item is required.")
        return value

    def create(self, validated_data):
        line_items_data = validated_data.pop("line_items")
        # Both writes must land together - a bulk_create() failure (e.g. a
        # DB-level constraint one of the line items happens to violate)
        # would otherwise leave a persisted WorkOrder with zero line items,
        # violating validate_line_items()'s "at least one" invariant.
        with transaction.atomic():
            work_order = WorkOrder.objects.create(**validated_data)
            WorkOrderLineItem.objects.bulk_create(
                WorkOrderLineItem(work_order=work_order, **line_item)
                for line_item in line_items_data
            )
        return work_order


class WorkOrderScanSerializer(serializers.Serializer):
    # Input-only (AC-2): one scanned serial against one line item per call,
    # matching PurchaseOrderReceiveSerializer's identical shape for the same
    # scan-gun-driven flow. Box-QR scanning (AC-3) needs a Box/Container
    # model that doesn't exist in this repo yet (PRD Epic 5, unbuilt, same
    # gap WRH-30 hit) - out of scope here, deferred to WRH-5 alongside it.
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


class WorkOrderActiveLineItemSerializer(serializers.ModelSerializer):
    # WRH-55/AC-2: "per-type returned vs. still-out counts" - see
    # RETURNED_COUNT_ANNOTATION/STILL_OUT_COUNT_ANNOTATION's comment for why
    # these differ from scanned_quantity/remaining_quantity.
    product_type_name = serializers.CharField(
        source="product_type.name", read_only=True
    )
    returned_quantity = serializers.SerializerMethodField()
    still_out_quantity = serializers.SerializerMethodField()

    class Meta:
        model = WorkOrderLineItem
        fields = [
            "id",
            "product_type",
            "product_type_name",
            "quantity",
            "returned_quantity",
            "still_out_quantity",
        ]

    def get_returned_quantity(self, obj):
        return getattr(obj, RETURNED_COUNT_ANNOTATION, 0)

    def get_still_out_quantity(self, obj):
        return getattr(obj, STILL_OUT_COUNT_ANNOTATION, 0)


class WorkOrderActiveSupplementarySerializer(serializers.ModelSerializer):
    # AC-1: a supplementary nested beneath its Primary - same per-WO summary
    # shape as WorkOrderActiveSerializer below, minus its own
    # "supplementaries" field (supplementaries are one level deep only -
    # there's no path to create a supplementary-of-a-supplementary).
    line_items = WorkOrderActiveLineItemSerializer(many=True, read_only=True)

    class Meta:
        model = WorkOrder
        fields = [
            "id",
            "job_name",
            "client_name",
            "expected_date_out",
            "status",
            "line_items",
        ]


class WorkOrderActiveSerializer(serializers.ModelSerializer):
    # WRH-55: the "Active Work Orders" list - Primary WOs (parent_work_order
    # is null) with their supplementaries nested beneath (AC-1) and
    # per-type returned/still-out summary counts (AC-2).
    line_items = WorkOrderActiveLineItemSerializer(many=True, read_only=True)
    supplementaries = WorkOrderActiveSupplementarySerializer(many=True, read_only=True)

    class Meta:
        model = WorkOrder
        fields = [
            "id",
            "job_name",
            "client_name",
            "expected_date_out",
            "status",
            "line_items",
            "supplementaries",
        ]


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

    class Meta:
        model = WorkOrder
        fields = [
            "id",
            "job_name",
            "client_name",
            "expected_date_out",
            "status",
            "created_by",
            "created_by_username",
            "parent_work_order",
            "line_items",
        ]
