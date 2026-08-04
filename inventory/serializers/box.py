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
    # WRH-27/AC-1/AC-2/AC-5: unlike product_type's static archived=False
    # restriction above, "already in another box"/"not available"/"wrong
    # product type" each need a message naming the specific offending item
    # (and, for AC-2, the specific box it's already in) - a queryset
    # restriction can only produce one generic does_not_exist message, so
    # those three checks live in validate()/create() below instead, against
    # an unrestricted queryset. allow_empty=False covers AC-4.
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

    @staticmethod
    def _validate_item(item, product_type):
        # WRH-27/AC-1/AC-2/AC-5, checked in this order (most specific
        # existing-constraint violation first, matching
        # WorkOrderViewSet.return_box()'s identical "issued elsewhere" ->
        # "current state" check ordering).
        if item.box_id is not None:
            raise serializers.ValidationError(
                {
                    "item_ids": [
                        f"{item.serial_number} is already in box {item.box.code}"
                    ]
                }
            )
        if item.status != SerializedItem.STATUS_AVAILABLE:
            raise serializers.ValidationError(
                {"item_ids": [f"{item.serial_number} is not available to box"]}
            )
        if item.product_type_id != product_type.id:
            raise serializers.ValidationError(
                {
                    "item_ids": [
                        f"{item.serial_number} does not match this box's product"
                        " type"
                    ]
                }
            )

    def validate(self, attrs):
        # Fast-path check, before any row is locked - see create()'s
        # identical re-check below for the race-safe, authoritative one.
        # Matches WorkOrderViewSet.scan()'s "check before lock, re-check
        # after lock" idiom.
        for item in attrs["items"]:
            self._validate_item(item, attrs["product_type"])
        return attrs

    def create(self, validated_data):
        items = validated_data.pop("items")
        # Both writes must land together - matches WorkOrderSerializer
        # .create()'s identical reasoning for its own two-step nested write.
        with transaction.atomic():
            # Re-check the archived guard post-lock too, not just the item
            # guards below - WRH-56 hit exactly this gap on
            # PurchaseOrderReceiveSerializer (product_type.archived checked
            # once pre-lock, a concurrent archive() in the window before
            # the lock slipped through) and its lesson generalizes: every
            # guard evaluated in the same pre-lock window needs the same
            # re-check once one of them gets a lock, not just the one that
            # motivated adding it.
            product_type = ProductType.objects.select_for_update().get(
                pk=validated_data["product_type"].pk
            )
            if product_type.archived:
                raise serializers.ValidationError(
                    {
                        "product_type": [
                            "Select a product type that exists and is not archived."
                        ]
                    }
                )
            # Lock the candidate item rows (order_by pins a deterministic
            # acquisition order across concurrent create() calls with
            # overlapping item pks, avoiding a lock-order deadlock -
            # matches scan_box()/return_box()'s identical
            # box.items.order_by("serial_number") when locking more than
            # one SerializedItem row in the same transaction) and
            # re-validate - validate()'s check above ran before any lock
            # was held, so a concurrent request could box/change the status
            # of one of these items in between. No select_related("box")
            # here even though _validate_item reads item.box.code on the
            # "already boxed" branch: SerializedItem.box is nullable, and
            # on Postgres SELECT ... FOR UPDATE can't be applied across the
            # resulting LEFT OUTER JOIN ("FOR UPDATE cannot be applied to
            # the nullable side of an outer join") - it would raise
            # NotSupportedError on every call, since box IS NULL is exactly
            # the normal case for a boxable item. The lazy follow-up query
            # only fires on that one rejection branch, not per item.
            locked_items = list(
                SerializedItem.objects.select_for_update()
                .filter(pk__in=[item.pk for item in items])
                .order_by("serial_number")
            )
            for item in locked_items:
                self._validate_item(item, product_type)
            box = Box.objects.create(**validated_data)
            SerializedItem.objects.filter(pk__in=[item.pk for item in items]).update(
                box=box
            )
        return box
