from django.db import transaction
from rest_framework import serializers

from inventory.models import MaintenanceOrder, SerializedItem


class MaintenanceOrderItemSerializer(serializers.ModelSerializer):
    # Read-only summary of a claimed item - matches
    # BoxItemSerializer/WorkOrderDetailSerializedItemSerializer's identical
    # minimal shape.
    class Meta:
        model = SerializedItem
        fields = ["id", "serial_number", "status"]


class MaintenanceOrderSerializer(serializers.ModelSerializer):
    # Matches DamageReportSerializer.reference's identical plain-CharField
    # read of a model property, rather than WorkOrderSerializer's
    # SerializerMethodField indirection - nothing here needs the extra
    # wrapping (no supplementary-vs-primary branching to hide).
    reference = serializers.CharField(read_only=True)
    # Write-only input of existing SerializedItem ids to place on this MO at
    # creation time; `items` below is the read-only nested view of the same
    # relation - matches BoxSerializer.item_ids/items' identical split.
    # allow_empty=False matches BoxSerializer.item_ids - "at least one item"
    # is inherent to the field's own shape, not the damaged-only eligibility
    # check that AC-1 assumes as a precondition and that WRH-47 (this
    # ticket's own notes) separately owns.
    item_ids = serializers.PrimaryKeyRelatedField(
        queryset=SerializedItem.objects.all(),
        many=True,
        write_only=True,
        source="items",
        allow_empty=False,
        error_messages={
            "does_not_exist": "Select an item that exists.",
            "empty": "Select at least one item.",
        },
    )
    items = MaintenanceOrderItemSerializer(many=True, read_only=True)

    class Meta:
        model = MaintenanceOrder
        fields = ["id", "reference", "status", "item_ids", "items"]
        read_only_fields = ["status"]

    @staticmethod
    def _validate_item(item):
        # Not the "only damaged items eligible" business rule (WRH-47's
        # scope) - these guard against silently clobbering a claim this
        # item already has elsewhere, the same structural corruption
        # BoxSerializer._validate_item's "already in another box" check
        # guards against for its own claim FK.
        if item.maintenance_order_id is not None:
            raise serializers.ValidationError(
                {
                    "item_ids": [
                        f"{item.serial_number} is already on maintenance order"
                        f" {item.maintenance_order.reference}"
                    ]
                }
            )
        if item.box_id is not None:
            raise serializers.ValidationError(
                {
                    "item_ids": [
                        f"{item.serial_number} is already in box {item.box.code}"
                    ]
                }
            )
        if item.work_order_line_item_id is not None:
            raise serializers.ValidationError(
                {
                    "item_ids": [
                        f"{item.serial_number} is already claimed on a work order"
                    ]
                }
            )

    def validate(self, attrs):
        # Fast-path check, before any row is locked - see create()'s
        # identical re-check below for the race-safe, authoritative one,
        # matching BoxSerializer.validate()'s identical idiom.
        for item in attrs["items"]:
            self._validate_item(item)
        return attrs

    def create(self, validated_data):
        items = validated_data.pop("items")
        # Both writes must land together - matches BoxSerializer.create()'s
        # identical reasoning for its own two-step nested write. Lock the
        # candidate item rows (order_by pins a deterministic acquisition
        # order across concurrent create() calls with overlapping item pks,
        # matching BoxSerializer.create()'s identical convention) so two
        # concurrent MO creates can't both claim the same item, then
        # re-validate against the locked rows - validate() above ran before
        # any lock was held, so a concurrent request could claim one of
        # these items in between.
        with transaction.atomic():
            locked_items = list(
                SerializedItem.objects.select_for_update()
                .filter(pk__in=[item.pk for item in items])
                .order_by("serial_number")
            )
            for item in locked_items:
                self._validate_item(item)
            maintenance_order = MaintenanceOrder.objects.create(**validated_data)
            SerializedItem.objects.filter(
                pk__in=[item.pk for item in locked_items]
            ).update(
                maintenance_order=maintenance_order,
                status=SerializedItem.STATUS_IN_MAINTENANCE,
            )
        return maintenance_order
