from django.db import models

from inventory.models.product_type import ProductType
from inventory.models.work_order import WorkOrder


class WorkOrderLineItem(models.Model):
    work_order = models.ForeignKey(
        WorkOrder, on_delete=models.CASCADE, related_name="line_items"
    )
    # PROTECT: a Product Type requested on a draft WO shouldn't be able to
    # vanish out from under it - matches PurchaseOrderLineItem.product_type's
    # identical guard on the same FK.
    product_type = models.ForeignKey(
        ProductType,
        on_delete=models.PROTECT,
        related_name="work_order_line_items",
    )
    quantity = models.PositiveIntegerField()

    def __str__(self):
        return f"{self.product_type} x{self.quantity}"
