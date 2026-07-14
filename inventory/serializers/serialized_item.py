from rest_framework import serializers

from inventory.models import ProductType, SerializedItem


class SerializedItemSerializer(serializers.ModelSerializer):
    # AC-6/WRH-21: an archived product type isn't a valid parent for a new
    # SerializedItem - restrict the write-side queryset so archived product
    # types aren't selectable for new registrations (TC-06), mirroring
    # ProductTypeSerializer's category field. to_representation() doesn't
    # consult this queryset, so an existing SerializedItem whose product
    # type gets archived later still reads back fine.
    product_type = serializers.PrimaryKeyRelatedField(
        queryset=ProductType.objects.filter(archived=False),
        error_messages={
            "does_not_exist": "Select a product type that exists and is not archived."
        },
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
            "last_work_order_reference",
            "notes",
        ]
        read_only_fields = ["serial", "status", "last_work_order_reference"]
