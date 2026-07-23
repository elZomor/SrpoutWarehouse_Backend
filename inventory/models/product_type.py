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
