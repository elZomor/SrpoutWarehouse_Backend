import io
import uuid

from django.db import models

import qrcode

from inventory.models.product_type import ProductType
from inventory.models.purchase_order_line_item import PurchaseOrderLineItem
from inventory.models.work_order_line_item import WorkOrderLineItem


class SerializedItem(models.Model):
    SEARCH_FIELDS = ("serial_number",)

    STATUS_AVAILABLE = "available"
    # WRH-54/AC-2: an item scanned against an in-progress WO is "claimed" -
    # no longer available for another scan/WO - but not yet "out" until the
    # WO's fulfillment is confirmed (AC-4). Distinct from STATUS_AVAILABLE
    # so scan()'s "status available" validation naturally rejects a second
    # scan of the same item without a separate business-rule check.
    STATUS_RESERVED = "reserved"
    STATUS_OUT = "out"
    STATUS_CHOICES = [
        (STATUS_AVAILABLE, "Available"),
        (STATUS_RESERVED, "Reserved"),
        (STATUS_OUT, "Out"),
    ]

    serial = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    serial_number = models.CharField(max_length=255, unique=True, db_index=True)
    product_type = models.ForeignKey(
        ProductType, on_delete=models.PROTECT, related_name="serialized_items"
    )
    # Only set when this item was created via WRH-56's PO receive flow -
    # items registered through the generic WRH-22 flow have no PO origin.
    # PROTECT: a line item with items already received against it shouldn't
    # be able to vanish out from under them (matches product_type's guard).
    purchase_order_line_item = models.ForeignKey(
        PurchaseOrderLineItem,
        on_delete=models.PROTECT,
        related_name="serialized_items",
        null=True,
        blank=True,
    )
    # Set by WorkOrderViewSet.scan() (WRH-54/AC-2) when this item is claimed
    # against a WO's line item - PROTECT for the same "shouldn't vanish out
    # from under it" reason as purchase_order_line_item above.
    work_order_line_item = models.ForeignKey(
        WorkOrderLineItem,
        on_delete=models.PROTECT,
        related_name="serialized_items",
        null=True,
        blank=True,
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_AVAILABLE
    )
    notes = models.TextField(blank=True, default="")
    # Populated by WorkOrderViewSet.complete() (WRH-54/AC-4) once a WO's
    # fulfillment is confirmed; blank until then.
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
