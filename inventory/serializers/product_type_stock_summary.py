from rest_framework import serializers

from inventory.models import ProductType
from inventory.models.product_type import (
    AVAILABLE_COUNT_ANNOTATION,
    DAMAGED_COUNT_ANNOTATION,
    IN_MAINTENANCE_COUNT_ANNOTATION,
    MISSING_COUNT_ANNOTATION,
    OUT_COUNT_ANNOTATION,
    TOTAL_COUNT_ANNOTATION,
    WRITTEN_OFF_COUNT_ANNOTATION,
)


class ProductTypeStockSummarySerializer(serializers.ModelSerializer):
    # WRH-48/AC-1: "total registered, out, damaged, missing, available" per
    # product type - each a SerializerMethodField reading its own DB-level
    # Count annotation (see ProductTypeViewSet.stock_summary()) rather than
    # deriving "available" by subtraction, so a future status added to
    # SerializedItem.STATUS_CHOICES can't silently miscount it the way an
    # arithmetic formula would - matches RETURNED_COUNT_ANNOTATION's
    # identical "count the status directly" reasoning on WorkOrder.
    total_registered = serializers.SerializerMethodField()
    out = serializers.SerializerMethodField()
    damaged = serializers.SerializerMethodField()
    missing = serializers.SerializerMethodField()
    available = serializers.SerializerMethodField()
    # WRH-46: added alongside IN_MAINTENANCE_COUNT_ANNOTATION so an
    # in-maintenance item stops silently falling out of the per-status
    # breakdown while still counted in total_registered.
    in_maintenance = serializers.SerializerMethodField()
    # WRH-74: dashboard grid gains a "Written-off" column.
    written_off = serializers.SerializerMethodField()

    class Meta:
        model = ProductType
        fields = [
            "id",
            "name",
            "total_registered",
            "out",
            "damaged",
            "missing",
            "available",
            "in_maintenance",
            "written_off",
        ]

    def get_total_registered(self, obj):
        return getattr(obj, TOTAL_COUNT_ANNOTATION, 0)

    def get_out(self, obj):
        return getattr(obj, OUT_COUNT_ANNOTATION, 0)

    def get_damaged(self, obj):
        return getattr(obj, DAMAGED_COUNT_ANNOTATION, 0)

    def get_missing(self, obj):
        return getattr(obj, MISSING_COUNT_ANNOTATION, 0)

    def get_available(self, obj):
        return getattr(obj, AVAILABLE_COUNT_ANNOTATION, 0)

    def get_in_maintenance(self, obj):
        return getattr(obj, IN_MAINTENANCE_COUNT_ANNOTATION, 0)

    def get_written_off(self, obj):
        return getattr(obj, WRITTEN_OFF_COUNT_ANNOTATION, 0)
