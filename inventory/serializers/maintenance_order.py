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
    reference = serializers.SerializerMethodField()
    # Write-only input of existing SerializedItem ids to place on this MO at
    # creation time; `items` below is the read-only nested view of the same
    # relation - matches BoxSerializer.item_ids/items' identical split.
    # AC-1 assumes its precondition (items are already "damaged") holds;
    # the business-rule validation for that (and for "at least one item
    # required") is WRH-47's separate scope per this ticket's notes, so no
    # status/emptiness check is applied here.
    item_ids = serializers.PrimaryKeyRelatedField(
        queryset=SerializedItem.objects.all(),
        many=True,
        write_only=True,
        source="items",
        error_messages={"does_not_exist": "Select an item that exists."},
    )
    items = MaintenanceOrderItemSerializer(many=True, read_only=True)

    class Meta:
        model = MaintenanceOrder
        fields = ["id", "reference", "status", "item_ids", "items"]
        read_only_fields = ["status"]

    def get_reference(self, obj):
        return obj.reference

    def create(self, validated_data):
        items = validated_data.pop("items")
        # Both writes must land together - matches BoxSerializer.create()'s
        # identical reasoning for its own two-step nested write. Lock the
        # candidate item rows (order_by pins a deterministic acquisition
        # order across concurrent create() calls with overlapping item pks,
        # matching BoxSerializer.create()'s identical convention) so two
        # concurrent MO creates can't both claim the same item.
        with transaction.atomic():
            locked_item_ids = list(
                SerializedItem.objects.select_for_update()
                .filter(pk__in=[item.pk for item in items])
                .order_by("serial_number")
                .values_list("pk", flat=True)
            )
            maintenance_order = MaintenanceOrder.objects.create(**validated_data)
            SerializedItem.objects.filter(pk__in=locked_item_ids).update(
                maintenance_order=maintenance_order,
                status=SerializedItem.STATUS_IN_MAINTENANCE,
            )
        return maintenance_order
