from rest_framework import serializers

from inventory.models import SerializedItem
from inventory.models.serialized_item import DATE_MISSING_ANNOTATION


class MissingItemSerializer(serializers.ModelSerializer):
    # AC-1: "serial number, product type, linked WO, date missing, status" -
    # product_type_name/work_order_* mirror SerializedItemSerializer's own
    # product_type_name pattern rather than exposing raw FK ids the frontend
    # would have to resolve itself.
    product_type_name = serializers.CharField(
        source="product_type.name", read_only=True
    )
    work_order_id = serializers.SerializerMethodField()
    work_order_reference = serializers.SerializerMethodField()
    date_missing = serializers.SerializerMethodField()

    class Meta:
        model = SerializedItem
        fields = [
            "id",
            "serial_number",
            "product_type_name",
            "work_order_id",
            "work_order_reference",
            "date_missing",
            "status",
        ]

    def get_work_order_id(self, obj):
        # AC-1/TC-04: "linked WO" - work_order_line_item is the item's
        # current claim (see its own model comment: nothing clears it once
        # set), which is exactly the WO close() swept this item out of.
        if not obj.work_order_line_item_id:
            return None
        return obj.work_order_line_item.work_order_id

    def get_work_order_reference(self, obj):
        # Matches the "WO-<id>" convention Transaction.reference_number
        # already writes elsewhere in this codebase (close()/return_item()).
        work_order_id = self.get_work_order_id(obj)
        return f"WO-{work_order_id}" if work_order_id else ""

    def get_date_missing(self, obj):
        return getattr(obj, DATE_MISSING_ANNOTATION, None)
