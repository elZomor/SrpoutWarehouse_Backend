from django.db import models

from inventory.models.category import Category

# WRH-48/AC-1: the stock dashboard's per-product-type counts - shared
# between ProductTypeViewSet.stock_summary() and
# ProductTypeStockSummarySerializer, matching WorkOrder's identical
# *_COUNT_ANNOTATION convention for the same "name it once, read it from
# both sides" reason.
TOTAL_COUNT_ANNOTATION = "total_registered_count"
OUT_COUNT_ANNOTATION = "out_count"
DAMAGED_COUNT_ANNOTATION = "damaged_count"
MISSING_COUNT_ANNOTATION = "missing_count"
AVAILABLE_COUNT_ANNOTATION = "available_count"
# WRH-46: added once MaintenanceOrderSerializer.create() became the first
# code path to ever actually set STATUS_IN_MAINTENANCE on a real row -
# before that, the status existed but nothing reachable set it, so its
# absence from this breakdown was a dormant gap, not a live one. Without
# this bucket, an in-maintenance item stays counted in
# TOTAL_COUNT_ANNOTATION but vanishes from every per-status count, so the
# breakdown no longer sums to the total.
IN_MAINTENANCE_COUNT_ANNOTATION = "in_maintenance_count"
# WRH-74: dashboard grid gains a "Written-off" column showing items in
# STATUS_WRITTEN_OFF - that status existed since WRH-48 but nothing read it
# until now.
WRITTEN_OFF_COUNT_ANNOTATION = "written_off_count"


class ProductType(models.Model):
    SEARCH_FIELDS = ("name", "model_code")

    name = models.CharField(max_length=255, unique=True, db_index=True)
    model_code = models.CharField(max_length=255, default="")
    description = models.TextField(blank=True, default="")
    category = models.ForeignKey(
        Category, on_delete=models.PROTECT, related_name="product_types"
    )
    archived = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name
