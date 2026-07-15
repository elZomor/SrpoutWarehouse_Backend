from django.db import models
from django.db.models import Count

# Shared between PurchaseOrderViewSet's queryset/receive() action and
# PurchaseOrderLineItemSerializer.get_received_quantity() - both sides of
# this annotation need to agree on the same name, so it's named once here
# (rather than duplicated as a string literal in views/ and serializers/)
# and required, not silently optional: any queryset that serializes a
# PurchaseOrderLineItem for this endpoint must supply it.
RECEIVED_COUNT_ANNOTATION = "received_count"


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

    def recompute_status(self, line_items=None):
        # AC-1/3/4/5: status is derived from how many SerializedItems each
        # line item has actually received against its expected_quantity -
        # never set directly by a client (status stays read_only on the
        # serializer), so this is the single source of truth for it.
        # Callers that already fetched the line items with a received-count
        # annotation (e.g. the receive() action, which needs the same rows
        # for its response) can pass them in to avoid a second COUNT query.
        if line_items is None:
            line_items = self.line_items.annotate(
                **{RECEIVED_COUNT_ANNOTATION: Count("serialized_items")}
            )
        line_items = list(line_items)
        if not line_items:
            # No line items to have received anything against yet - matches
            # a freshly-created PO's status rather than vacuously "received"
            # (all() over an empty iterable is True).
            self.status = self.STATUS_PENDING
        elif all(
            getattr(item, RECEIVED_COUNT_ANNOTATION) >= item.expected_quantity
            for item in line_items
        ):
            self.status = self.STATUS_RECEIVED
        elif any(getattr(item, RECEIVED_COUNT_ANNOTATION) > 0 for item in line_items):
            self.status = self.STATUS_PARTIALLY_RECEIVED
        else:
            self.status = self.STATUS_PENDING
        self.save(update_fields=["status"])
