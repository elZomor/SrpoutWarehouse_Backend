from rest_framework import serializers

from inventory.models import ProductType, WorkOrder, WorkOrderLineItem


class WorkOrderLineItemSerializer(serializers.ModelSerializer):
    product_type = serializers.PrimaryKeyRelatedField(
        queryset=ProductType.objects.all()
    )
    product_type_name = serializers.CharField(
        source="product_type.name", read_only=True
    )

    class Meta:
        model = WorkOrderLineItem
        fields = ["id", "product_type", "product_type_name", "quantity"]


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
        work_order = WorkOrder.objects.create(**validated_data)
        WorkOrderLineItem.objects.bulk_create(
            WorkOrderLineItem(work_order=work_order, **line_item)
            for line_item in line_items_data
        )
        return work_order
