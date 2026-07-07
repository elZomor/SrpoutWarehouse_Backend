from django.db import models


class ProductType(models.Model):
    name = models.CharField(max_length=255)
    model_code = models.CharField(max_length=255, blank=True, default="")
    description = models.TextField(blank=True, default="")

    def __str__(self):
        return self.name
