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

    class Meta:
        model = PurchaseOrderLineItem
        fields = ["id", "product_type", "product_type_name", "expected_quantity"]
        extra_kwargs = {
            "expected_quantity": {
                "error_messages": {"required": "Expected quantity is required."},
            },
        }


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
