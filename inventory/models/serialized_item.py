import uuid

from django.db import models


class SerializedItem(models.Model):
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=255)
    serial = models.UUIDField(default=uuid.uuid4, editable=False)
    notes = models.TextField(blank=True, default="")

    def __str__(self):
        return f"{self.name} - {self.code} - {self.serial}"
