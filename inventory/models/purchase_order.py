from django.db import models


class PurchaseOrder(models.Model):
    STATUS_PENDING = "pending"
    STATUS_CHOICES = [(STATUS_PENDING, "Pending")]

    supplier_name = models.CharField(max_length=255)
    order_date = models.DateField()
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING
    )

    class Meta:
        ordering = ["-order_date", "-id"]

    def __str__(self):
        return f"PO-{self.id} ({self.supplier_name})"
