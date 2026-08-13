from django.db import transaction

from rest_framework import serializers

from inventory.models import DamageReport, SerializedItem, Transaction


class DamageReportSerializer(serializers.ModelSerializer):
    # AC-2: "DR number, date, serial, product type, note, user" - read shape
    # for the list action. serial_number/product_type_name mirror
    # TransactionSerializer's identical pattern for the same underlying
    # fields.
    reference = serializers.CharField(read_only=True)
    serial_number = serializers.CharField(
        source="serialized_item.serial_number", read_only=True
    )
    product_type_name = serializers.CharField(
        source="serialized_item.product_type.name", read_only=True
    )
    user_username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = DamageReport
        fields = [
            "id",
            "reference",
            "serial_number",
            "product_type_name",
            "note",
            "user_username",
            "created_at",
        ]


def _unknown_serial_message(serial_number):
    return f"Serial number {serial_number} was not found."


def _not_available_message(item):
    return f"{item.serial_number} is not available to report as damaged."


class DamageReportCreateSerializer(serializers.Serializer):
    # AC-1/AC-3: input-only - one scanned/entered serial plus an optional
    # note, matching PurchaseOrderReceiveSerializer's identical "plain
    # Serializer for input, view builds the read response separately"
    # shape (this create's read shape - reference/product_type_name/
    # user_username - genuinely differs from its write shape, unlike
    # BoxSerializer's single-serializer case).
    serial_number = serializers.CharField(
        error_messages={
            "blank": "Serial number is required.",
            "required": "Serial number is required.",
        },
    )
    note = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_serial_number(self, value):
        # Fast-path check before any row is locked - matches
        # BoxSerializer.validate()'s identical "check before lock, re-check
        # after lock" idiom; create() below re-validates against the locked
        # row.
        try:
            item = SerializedItem.objects.get(serial_number=value)
        except SerializedItem.DoesNotExist:
            raise serializers.ValidationError(_unknown_serial_message(value))
        if item.status != SerializedItem.STATUS_AVAILABLE:
            raise serializers.ValidationError(_not_available_message(item))
        return value

    def create(self, validated_data):
        serial_number = validated_data["serial_number"]
        note = validated_data["note"]
        user = self.context["request"].user

        with transaction.atomic():
            try:
                # select_related("product_type") - not part of any nullable
                # join (unlike WRH-28's work_order_line_item case), and the
                # response is rendered through DamageReportSerializer
                # (product_type_name), which would otherwise re-query it on
                # every create call.
                item = (
                    SerializedItem.objects.select_for_update()
                    .select_related("product_type")
                    .get(serial_number=serial_number)
                )
            except SerializedItem.DoesNotExist:
                raise serializers.ValidationError(
                    {"serial_number": [_unknown_serial_message(serial_number)]}
                )
            if item.status != SerializedItem.STATUS_AVAILABLE:
                raise serializers.ValidationError(
                    {"serial_number": [_not_available_message(item)]}
                )

            item.status = SerializedItem.STATUS_DAMAGED
            item.save(update_fields=["status"])

            report = DamageReport.objects.create(
                serialized_item=item, user=user, note=note
            )
            Transaction.objects.create(
                transaction_type=Transaction.TYPE_DAMAGED,
                serialized_item=item,
                reference_number=report.reference,
                user=user,
                note=note,
            )
        return report
