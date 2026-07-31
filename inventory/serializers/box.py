from django.db import transaction
from rest_framework import serializers

from inventory.models import Box, ProductType, SerializedItem


class BoxItemSerializer(serializers.ModelSerializer):
    # Read-only summary of a boxed item - just enough for the box detail/
    # QR-scan response to list what's inside, matching
    # WorkOrderDetailSerializedItemSerializer's identical minimal shape.
    class Meta:
        model = SerializedItem
        fields = ["id", "serial_number", "status"]


class BoxSerializer(serializers.ModelSerializer):
    # AC-6/WRH-21 convention: an archived product type isn't selectable for
    # a new Box, matching SerializedItemSerializer.product_type's identical
    # queryset restriction.
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
    # Write-only input of existing SerializedItem ids to place in this box
    # at creation time; `items` below is the read-only nested view of the
    # same relation (box.items, the reverse of SerializedItem.box) - two
    # field names sharing one underlying relation, one for each direction,
    # so a submitted id list never collides with the returned nested
    # summary in either the request or response payload.
    item_ids = serializers.PrimaryKeyRelatedField(
        queryset=SerializedItem.objects.all(),
        many=True,
        write_only=True,
        source="items",
    )
    items = BoxItemSerializer(many=True, read_only=True)

    class Meta:
        model = Box
        fields = [
            "id",
            "code",
            "uuid",
            "product_type",
            "product_type_name",
            "item_ids",
            "items",
        ]
        read_only_fields = ["uuid"]
        extra_kwargs = {
            "code": {
                "error_messages": {
                    "blank": "Box code is required.",
                    "required": "Box code is required.",
                },
            },
        }

    def create(self, validated_data):
        items = validated_data.pop("items")
        # Both writes must land together - matches WorkOrderSerializer
        # .create()'s identical reasoning for its own two-step nested write.
        with transaction.atomic():
            box = Box.objects.create(**validated_data)
            SerializedItem.objects.filter(pk__in=[item.pk for item in items]).update(
                box=box
            )
        return box
