from rest_framework import serializers

from inventory.models import ProductType, SerializedItem


class SerializedItemSerializer(serializers.ModelSerializer):
    product_type = serializers.PrimaryKeyRelatedField(
        queryset=ProductType.objects.all()
    )
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
