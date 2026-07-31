import io
import uuid

from django.db import models

import qrcode

from inventory.models.product_type import ProductType


class Box(models.Model):
    # WRH-26/AC-1: "code" is the manager-entered, human-scannable value (the
    # Box equivalent of SerializedItem.serial_number - what a scan-gun
    # actually reads back on a transaction screen), distinct from the
    # separately auto-generated `uuid` below, which only exists to back the
    # printable QR label (matches SerializedItem.serial's identical split).
    code = models.CharField(max_length=255, unique=True, db_index=True)
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    # PROTECT: a box's contents are all one product type (AC-1 of WRH-27) -
    # that product type shouldn't be able to vanish out from under an
    # existing box, matching SerializedItem.product_type's identical guard.
    product_type = models.ForeignKey(
        ProductType, on_delete=models.PROTECT, related_name="boxes"
    )

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return f"Box {self.code}"

    def generate_qr_code_png(self):
        # Regenerated on demand rather than stored - matches
        # SerializedItem.generate_qr_code_png()'s identical reasoning, the
        # payload is just this box's own stable UUID.
        image = qrcode.make(str(self.uuid))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()
