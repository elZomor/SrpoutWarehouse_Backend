import io
import uuid

from django.db import models

import qrcode

from inventory.models.box import Box
from inventory.models.maintenance_order import MaintenanceOrder
from inventory.models.product_type import ProductType
from inventory.models.purchase_order_line_item import PurchaseOrderLineItem
from inventory.models.work_order_line_item import WorkOrderLineItem

# WRH-42/AC-1: the Missing Items list's "date missing" column - shared
# between MissingItemViewSet.get_queryset() (the annotate() that computes it)
# and MissingItemSerializer (the getattr() that reads it back), matching
# ProductType's identical *_COUNT_ANNOTATION convention for the same
# "name it once, read it from both sides" reason.
DATE_MISSING_ANNOTATION = "date_missing"


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
    # WRH-33/AC-3: an item can be flagged damaged/missing so fulfillment
    # scanning rejects it with a status-specific reason. Nothing yet sets an
    # item to either status (that's the separate, unbuilt Item Status
    # Lifecycle §6.2 workflow) - these values exist purely so scan()'s
    # rejection logic below has a real status to check against.
    STATUS_DAMAGED = "damaged"
    STATUS_MISSING = "missing"
    # WRH-45/AC-1: Direct Damage Report's status-specific rejection needs a
    # real value to check/display for this status, same "exists before any
    # endpoint sets it" reasoning as STATUS_WRITTEN_OFF below - the
    # in-maintenance workflow itself (what would set this) is unbuilt.
    STATUS_IN_MAINTENANCE = "in_maintenance"
    # WRH-48/AC-2: the stock dashboard's Available formula needs a real
    # status to subtract, the same way STATUS_DAMAGED/STATUS_MISSING existed
    # before any endpoint set them - nothing yet sets an item to
    # written_off either (that's a future write-off ticket); this exists so
    # the dashboard's available count is correct and complete now rather
    # than silently wrong once a write-off action ships later.
    STATUS_WRITTEN_OFF = "written_off"
    STATUS_CHOICES = [
        (STATUS_AVAILABLE, "Available"),
        (STATUS_RESERVED, "Reserved"),
        (STATUS_OUT, "Out"),
        (STATUS_DAMAGED, "Damaged"),
        (STATUS_MISSING, "Missing"),
        (STATUS_IN_MAINTENANCE, "In maintenance"),
        (STATUS_WRITTEN_OFF, "Written off"),
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
    # WRH-26/AC-1: set when this item is registered into a Box at box-
    # creation time (BoxSerializer.create()) - null for an item never
    # boxed. PROTECT for the same "shouldn't vanish out from under it"
    # reason as the other claim FKs above; box contents are fixed after
    # creation (PRD v0.5 §2.2 Out of Scope), so nothing ever clears this
    # once set - matches work_order_line_item's identical "current claim
    # only, no history" design.
    box = models.ForeignKey(
        Box,
        on_delete=models.PROTECT,
        related_name="items",
        null=True,
        blank=True,
    )
    # WRH-46/AC-1: set when this item is selected onto a Maintenance Order
    # at creation time (MaintenanceOrderSerializer.create()) - PROTECT for
    # the same "shouldn't vanish out from under it" reason as box/
    # work_order_line_item above; nothing clears this once set, matching
    # box's identical "current claim only, no history" design.
    maintenance_order = models.ForeignKey(
        MaintenanceOrder,
        on_delete=models.PROTECT,
        related_name="items",
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
