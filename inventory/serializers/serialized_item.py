from rest_framework import serializers

from inventory.models import ProductType, SerializedItem


def duplicate_serial_number_message(serial_number):
    # Shared with views.serialized_item.perform_create()'s IntegrityError
    # fallback so the pre-check and DB-race paths can't drift apart.
    return f"Serial number {serial_number} is already registered."


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
            "does_not_exist": "Select a product type that exists and is not archived.",
            "required": "Product type is required.",
            "null": "Product type is required.",
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
        extra_kwargs = {
            "serial_number": {
                "error_messages": {
                    "blank": "Serial number is required.",
                    "required": "Serial number is required.",
                },
                # AC-1: the default UniqueValidator's message can't embed the
                # submitted value, and the ticket wants the exact serial
                # number echoed back - dropped here in favor of
                # validate_serial_number() below, which raises with the
                # value interpolated in.
                "validators": [],
            },
        }

    def validate_serial_number(self, value):
        # Exclude self.instance so a future update path (none exists yet -
        # this viewset is Create+List only) doesn't reject an item against
        # its own unchanged serial number.
        queryset = SerializedItem.objects.filter(serial_number=value)
        if self.instance is not None:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise serializers.ValidationError(duplicate_serial_number_message(value))
        return value
