from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models

# Shared between WorkOrderViewSet's scan()/complete() actions and
# WorkOrderLineItemSerializer.get_scanned_quantity() - both sides of this
# annotation need to agree on the same name, so it's named once here
# (rather than duplicated as a string literal in views/ and serializers/),
# matching PurchaseOrder's identical RECEIVED_COUNT_ANNOTATION convention.
SCANNED_COUNT_ANNOTATION = "scanned_count"

# WRH-55/AC-2: "per-type returned vs. still-out counts" on the active-WOs
# list - distinct from SCANNED_COUNT_ANNOTATION (fulfillment progress
# against the requested quantity). returned = items WorkOrderViewSet.
# return_item() (WRH-38) has flipped back to STATUS_AVAILABLE; still_out =
# every other status an issued item can be in (out/reserved/damaged/
# missing) - not just STATUS_OUT - so the two counts always sum to the line
# item's quantity instead of a damaged/missing item silently vanishing from
# the summary.
RETURNED_COUNT_ANNOTATION = "returned_count"
STILL_OUT_COUNT_ANNOTATION = "still_out_count"


class WorkOrder(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_FULFILLED = "fulfilled"
    # WRH-38/AC-1/AC-2: a fulfilled WO moves to "returned" once every issued
    # serial is back, or "partially_returned" if some remain out -
    # WorkOrderViewSet.return_item() is the only path that sets either.
    STATUS_RETURNED = "returned"
    STATUS_PARTIALLY_RETURNED = "partially_returned"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_IN_PROGRESS, "In progress"),
        (STATUS_FULFILLED, "Fulfilled"),
        (STATUS_RETURNED, "Returned"),
        (STATUS_PARTIALLY_RETURNED, "Partially returned"),
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
    # WRH-55/AC-1: a Primary WO's supplementaries are shown nested beneath
    # it. Supplementary *creation* (US-012a) has no ticket/UI yet - this
    # field only exists so the list/detail views have something to nest -
    # null means "this WO is itself a Primary". PROTECT: a Primary WO
    # shouldn't be deletable out from under its supplementaries.
    parent_work_order = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="supplementaries",
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["-expected_date_out", "-id"]

    def __str__(self):
        return f"WO-{self.id} ({self.job_name})"

    def clean(self):
        # WRH-33/AC-6: only a Primary WO (parent_work_order is null) can be
        # a parent - a supplementary can't itself have supplementaries.
        # There's no API path that sets parent_work_order yet (WRH-55's own
        # comment on the field above), but Django admin's default
        # ModelForm calls full_clean() and exposes every model field
        # unrestricted, so this guard is reachable today even with no
        # dedicated create endpoint - matches the WRH-56 lesson that admin
        # is a real caller, not just the registered API routes.
        super().clean()
        if self.parent_work_order_id and self.parent_work_order.parent_work_order_id:
            raise ValidationError(
                {
                    "parent_work_order": (
                        "A supplementary work order cannot itself be the"
                        " parent of another supplementary work order."
                    )
                }
            )
