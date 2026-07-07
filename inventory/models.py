from django.db import models


class ProductType(models.Model):
    SEARCH_FIELDS = ("name", "model_code")

    name = models.CharField(max_length=255, db_index=True)
    model_code = models.CharField(max_length=255, blank=True, default="", db_index=True)
    description = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name
