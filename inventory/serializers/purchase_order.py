from rest_framework import serializers

from inventory.models import ProductType, PurchaseOrder, PurchaseOrderLineItem


class PurchaseOrderLineItemSerializer(serializers.ModelSerializer):
    product_type = serializers.PrimaryKeyRelatedField(
        queryset=ProductType.objects.all(),
        error_messages={"required": "Product type is required."},
    )
    product_type_name = serializers.CharField(
        source="product_type.name", read_only=True
    )
    received_quantity = serializers.SerializerMethodField()
    remaining_quantity = serializers.SerializerMethodField()

    class Meta:
        model = PurchaseOrderLineItem
        fields = [
            "id",
            "product_type",
            "product_type_name",
            "expected_quantity",
            "received_quantity",
            "remaining_quantity",
        ]
        extra_kwargs = {
            "expected_quantity": {
                "error_messages": {"required": "Expected quantity is required."},
            },
        }

    def get_received_quantity(self, obj):
        # AC-3: "remaining quantity (20) is visible on the PO" - derived from
        # the linked SerializedItems (recompute_status()'s own source of
        # truth) rather than a stored counter that could drift. A plain
        # obj.serialized_items.count() would always issue a fresh COUNT and
        # ignore the queryset's prefetch_related cache, so prefer the
        # "received_count" annotation the viewset's queryset attaches; only
        # line items built outside that queryset (e.g. bulk_create() in
        # PurchaseOrderSerializer.create()) fall back to a plain count.
        received = getattr(obj, "received_count", None)
        return received if received is not None else obj.serialized_items.count()

    def get_remaining_quantity(self, obj):
        return max(obj.expected_quantity - self.get_received_quantity(obj), 0)


class PurchaseOrderSerializer(serializers.ModelSerializer):
    # ModelSerializer's nested-write support stops at validation - create()
    # below has to build the line items itself, DRF won't do it implicitly.
    line_items = PurchaseOrderLineItemSerializer(many=True)

    class Meta:
        model = PurchaseOrder
        fields = ["id", "supplier_name", "order_date", "status", "line_items"]
        read_only_fields = ["status"]
        extra_kwargs = {
            "supplier_name": {
                "error_messages": {
                    "blank": "Supplier name is required.",
                    "required": "Supplier name is required.",
                },
            },
            "order_date": {
                "error_messages": {"required": "Order date is required."},
            },
        }

    def validate_line_items(self, value):
        # AC-1: a PO needs one or more line items.
        if not value:
            raise serializers.ValidationError("At least one line item is required.")
        return value

    def create(self, validated_data):
        line_items_data = validated_data.pop("line_items")
        purchase_order = PurchaseOrder.objects.create(**validated_data)
        PurchaseOrderLineItem.objects.bulk_create(
            PurchaseOrderLineItem(purchase_order=purchase_order, **line_item)
            for line_item in line_items_data
        )
        return purchase_order


class PurchaseOrderReceiveSerializer(serializers.Serializer):
    # Input-only (AC-1/AC-3): one scanned serial against one line item per
    # call, matching how a scan gun actually feeds the UI - box-QR scanning
    # (AC-2) needs a Box/Container model that doesn't exist in this repo
    # yet (PRD Epic 5, unbuilt), so it's out of scope here.
    line_item = serializers.PrimaryKeyRelatedField(
        queryset=PurchaseOrderLineItem.objects.all(),
        error_messages={"required": "Line item is required."},
    )
    serial_number = serializers.CharField(
        error_messages={
            "blank": "Serial number is required.",
            "required": "Serial number is required.",
        },
    )
