from rest_framework import serializers

from inventory.models import SerializedItem


class SerializedItemSerializer(serializers.ModelSerializer):
    # No queryset restriction needed here (unlike ProductTypeSerializer's
    # category field) - ProductType has no archived/soft-delete state to
    # exclude, so ModelSerializer's auto-generated PrimaryKeyRelatedField
    # for this FK is already correct; no explicit declaration needed.
    product_type_name = serializers.CharField(
        source="product_type.name", read_only=True
    )

    class Meta:
        model = SerializedItem
        fields = [
            "id",
            "serial",
            "serial_number",
            "product_type",
            "product_type_name",
            "status",
            "qr_code",
            "last_work_order_reference",
            "notes",
        ]
        read_only_fields = ["serial", "status", "qr_code", "last_work_order_reference"]
