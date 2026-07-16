from django.contrib.auth.models import User
from django.db import models


class WorkOrder(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
    ]

    job_name = models.CharField(max_length=255)
    client_name = models.CharField(max_length=255, blank=True, default="")
    expected_date_out = models.DateField()
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT
    )
    # AC-1: "the creating user is recorded" - PROTECT (not CASCADE/SET_NULL)
    # so a WO's audit trail can't silently lose its creator by deleting the
    # user account, matching PurchaseOrderLineItem.product_type's rationale
    # for the same kind of "shouldn't vanish out from under" FK.
    created_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="work_orders",
    )

    class Meta:
        ordering = ["-expected_date_out", "-id"]

    def __str__(self):
        return f"WO-{self.id} ({self.job_name})"
