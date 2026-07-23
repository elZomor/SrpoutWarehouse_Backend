from rest_framework import serializers

from inventory.models import Transaction


class TransactionSerializer(serializers.ModelSerializer):
    # AC-1: display fields the log needs beyond the model's raw FKs -
    # read-only since this viewset is list-only (transactions are only ever
    # created internally by the actions that produce them, never via a
    # direct write endpoint).
    transaction_type_display = serializers.CharField(
        source="get_transaction_type_display", read_only=True
    )
    serial_number = serializers.CharField(
        source="serialized_item.serial_number", read_only=True
    )
    product_type_name = serializers.CharField(
        source="serialized_item.product_type.name", read_only=True
    )
    user_username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = Transaction
        fields = [
            "id",
            "transaction_type",
            "transaction_type_display",
            "reference_number",
            "serial_number",
            "product_type_name",
            "created_at",
            "user_username",
            "note",
        ]
