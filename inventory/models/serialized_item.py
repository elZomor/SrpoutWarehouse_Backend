import io
import uuid

from django.core.files.base import ContentFile
from django.db import models

import qrcode

from inventory.models.product_type import ProductType


class SerializedItem(models.Model):
    SEARCH_FIELDS = ("serial_number",)

    STATUS_AVAILABLE = "available"
    STATUS_CHOICES = [(STATUS_AVAILABLE, "Available")]

    serial = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    serial_number = models.CharField(max_length=255, unique=True, db_index=True)
    product_type = models.ForeignKey(
        ProductType, on_delete=models.PROTECT, related_name="serialized_items"
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_AVAILABLE
    )
    qr_code = models.ImageField(upload_to="qr_codes/", blank=True)
    notes = models.TextField(blank=True, default="")
    # No WorkOrder model exists yet (issuance is a later Delivery Plan phase);
    # kept as an always-blank placeholder so the list API contract (AC-3)
    # already carries the field name the frontend needs, populated once
    # WO issuance ships.
    last_work_order_reference = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ["serial_number"]

    def __str__(self):
        return self.serial_number

    def generate_qr_code(self):
        image = qrcode.make(str(self.serial))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        filename = f"{self.serial}.png"
        self.qr_code.save(filename, ContentFile(buffer.getvalue()), save=False)

    def save(self, *args, **kwargs):
        # Persist the row first: generate_qr_code() writes to storage
        # immediately, so generating it before this insert/update succeeds
        # would leave an orphaned file on disk if the DB write fails (e.g. a
        # racing duplicate serial_number hitting the unique constraint).
        super().save(*args, **kwargs)
        if not self.qr_code:
            self.generate_qr_code()
            super().save(update_fields=["qr_code"])
