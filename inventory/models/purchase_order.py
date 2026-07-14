from django.db import models
from django.db.models import Count


class PurchaseOrder(models.Model):
    STATUS_PENDING = "pending"
    STATUS_PARTIALLY_RECEIVED = "partially_received"
    STATUS_RECEIVED = "received"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_PARTIALLY_RECEIVED, "Partially received"),
        (STATUS_RECEIVED, "Received"),
    ]

    supplier_name = models.CharField(max_length=255)
    order_date = models.DateField()
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING
    )

    class Meta:
        ordering = ["-order_date", "-id"]

    def __str__(self):
        return f"PO-{self.id} ({self.supplier_name})"

    def recompute_status(self):
        # AC-1/3/4/5: status is derived from how many SerializedItems each
        # line item has actually received against its expected_quantity -
        # never set directly by a client (status stays read_only on the
        # serializer), so this is the single source of truth for it.
        line_items = self.line_items.annotate(received=Count("serialized_items"))
        if all(item.received >= item.expected_quantity for item in line_items):
            self.status = self.STATUS_RECEIVED
        elif any(item.received > 0 for item in line_items):
            self.status = self.STATUS_PARTIALLY_RECEIVED
        else:
            self.status = self.STATUS_PENDING
        self.save(update_fields=["status"])
