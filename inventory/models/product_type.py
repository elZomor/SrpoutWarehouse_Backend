from django.db import models

from inventory.models.category import Category


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
