import io
import uuid

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

    def generate_qr_code_png(self):
        # Regenerated on demand (reprint) rather than stored: the payload is
        # just this item's own stable UUID, so re-running the deterministic
        # encode is cheaper than keeping a rendered image blob that's only
        # ever read again if a printed label is damaged.
        image = qrcode.make(str(self.serial))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()
