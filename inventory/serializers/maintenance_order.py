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
            "empty": "Select at least one damaged item.",
        },
    )
    items = MaintenanceOrderItemSerializer(many=True, read_only=True)

    class Meta:
        model = MaintenanceOrder
        fields = ["id", "reference", "status", "item_ids", "items"]
        read_only_fields = ["status"]

    @staticmethod
    def _validate_item(item):
        # WRH-47/AC-4: guards against silently clobbering a claim this item
        # already has on another MaintenanceOrder, the same structural
        # corruption BoxSerializer._validate_item's "already in another box"
        # check guards against for its own claim FK. Deliberately does NOT
        # check item.box_id: unlike work_order_line_item (below) and this
        # model's own FK, Box membership is a permanent, orthogonal physical
        # tag, not a competing claim - a boxed item can legitimately also be
        # damaged and go to maintenance (see WorkOrderViewSet.return_box()'s
        # "boxed item marked damaged mid-box" handling), so box_id being set
        # must not block it. An earlier version of this check treated box_id
        # as a live claim and wrongly rejected every ever-boxed item.
        # maintenance_order_id, like work_order_line_item_id, is never
        # cleared by resolve() - so the claim only stays live while the MO
        # itself is still open/in_progress; an item resolved off a
        # *completed* MO and later damaged again must not be rejected just
        # because it has MO history.
        if item.maintenance_order_id is not None and item.maintenance_order.status in (
            MaintenanceOrder.STATUS_OPEN,
            MaintenanceOrder.STATUS_IN_PROGRESS,
        ):
            raise serializers.ValidationError(
                {
                    "item_ids": [
                        f"{item.serial_number} is already on maintenance order"
                        f" {item.maintenance_order.reference}"
                    ]
                }
            )
        # WorkOrderLineItem's own comment: "current claim only, no history" -
        # work_order_line_item_id stays set forever once an item is ever
        # issued, even long after return_item() flips it back to available.
        # So the *current* claim has to be read off status
        # (reserved/out), not FK presence - an item that cycled through a
        # WO and came back (e.g. now damaged) is fully eligible for an MO
        # and must not be rejected just because it has WO history.
        if item.status in (
            SerializedItem.STATUS_RESERVED,
            SerializedItem.STATUS_OUT,
        ):
            raise serializers.ValidationError(
                {
                    "item_ids": [
                        f"{item.serial_number} is currently claimed on a work order"
                    ]
                }
            )
        # WRH-47/AC-1: eligibility is restricted to damaged items - the two
        # checks above raise more specific reasons first for the statuses
        # they cover (reserved/out); everything else that isn't "damaged"
        # (available, missing, written_off, or still in_maintenance without
        # a live MO claim) falls through to this generic rejection.
        if item.status != SerializedItem.STATUS_DAMAGED:
            raise serializers.ValidationError(
                {
                    "item_ids": [
                        f"{item.serial_number} must be damaged to be added to a"
                        " maintenance order"
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


class MaintenanceOrderResolveSerializer(serializers.Serializer):
    # Input-only (AC-1/AC-2): which line item is being resolved, and to
    # which outcome - matches WorkOrderReturnScanSerializer's identical
    # plain-Serializer input shape for a detail action's request body.
    RESOLUTION_FIXED = "fixed"
    RESOLUTION_NOT_FIXABLE = "not_fixable"
    RESOLUTION_CHOICES = [RESOLUTION_FIXED, RESOLUTION_NOT_FIXABLE]

    item_id = serializers.PrimaryKeyRelatedField(
        queryset=SerializedItem.objects.all(),
        source="item",
        error_messages={"does_not_exist": "Item not found."},
    )
    resolution = serializers.ChoiceField(choices=RESOLUTION_CHOICES)
