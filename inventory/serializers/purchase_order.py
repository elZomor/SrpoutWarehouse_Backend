from rest_framework import serializers

from inventory.models import ProductType, PurchaseOrder, PurchaseOrderLineItem
from inventory.models.purchase_order import RECEIVED_COUNT_ANNOTATION


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
        # truth) rather than a stored counter that could drift. Every
        # queryset that reaches this serializer (list, receive) attaches the
        # RECEIVED_COUNT_ANNOTATION annotation to avoid an N+1 COUNT per
        # line item; the plain obj.serialized_items.count() fallback only
        # exists for PurchaseOrderSerializer.create()'s response, where the
        # just-bulk_created line items are guaranteed zero received (no
        # SerializedItem can reference a line item that didn't exist a
        # moment ago), so the fallback and the annotation always agree.
        received = getattr(obj, RECEIVED_COUNT_ANNOTATION, None)
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
    #
    # AC-6/WRH-21: an archived product type shouldn't receive new stock
    # through this path either - restrict the write-side queryset the same
    # way SerializedItemSerializer.product_type and ProductTypeSerializer's
    # category field do, instead of a manual `if ... archived` check in the
    # view.
    line_item = serializers.PrimaryKeyRelatedField(
        queryset=PurchaseOrderLineItem.objects.filter(product_type__archived=False),
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
