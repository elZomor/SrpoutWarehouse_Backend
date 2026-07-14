from django.db import models

from inventory.models.product_type import ProductType
from inventory.models.purchase_order import PurchaseOrder


class PurchaseOrderLineItem(models.Model):
    purchase_order = models.ForeignKey(
        PurchaseOrder, on_delete=models.CASCADE, related_name="line_items"
    )
    # PROTECT (not CASCADE): a Product Type with expected quantities on an
    # open PO shouldn't be able to vanish out from under it - matches
    # SerializedItem's identical guard on the same FK.
    product_type = models.ForeignKey(
        ProductType,
        on_delete=models.PROTECT,
        related_name="purchase_order_line_items",
    )
    expected_quantity = models.PositiveIntegerField()

    def __str__(self):
        return f"{self.product_type} x{self.expected_quantity}"
