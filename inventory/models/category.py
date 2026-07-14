from django.db import models


class Category(models.Model):
    SEARCH_FIELDS = ("name",)

    name = models.CharField(max_length=255, unique=True, db_index=True)
    description = models.TextField(blank=True, default="")
    archived = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name
