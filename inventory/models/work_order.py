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
# every other status an issued item can be in (out/reserved/missing) - not
# just STATUS_OUT - so the three counts (returned/damaged/still_out) always
# sum to the line item's quantity. WRH-57/AC-3: STATUS_DAMAGED gets its own
# DAMAGED_COUNT_ANNOTATION and is excluded from STILL_OUT_COUNT_ANNOTATION -
# a damaged return is its own outcome, neither "returned to available stock"
# nor "still missing".
RETURNED_COUNT_ANNOTATION = "returned_count"
DAMAGED_COUNT_ANNOTATION = "damaged_count"
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
    # it. WRH-53/US-012a: also the field the create endpoint now writes to
    # link a new supplementary to its Primary - null means "this WO is
    # itself a Primary". PROTECT: a Primary WO shouldn't be deletable out
    # from under its supplementaries.
    parent_work_order = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="supplementaries",
        null=True,
        blank=True,
    )
    # WRH-53/AC-1/AC-2: 1-indexed position among this WO's siblings under
    # the same parent, assigned once at creation time (see
    # WorkOrderSerializer.create()) - combined with parent_work_order_id it
    # renders as "WO-<parent id>-S<sequence>". null for a Primary WO (it has
    # no parent to be numbered against). Stored rather than derived on read
    # so a supplementary's identifier stays stable even if the read-time
    # sibling ordering could ever change.
    supplementary_sequence = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["-expected_date_out", "-id"]
        constraints = [
            # Defense-in-depth alongside the select_for_update() lock in
            # WorkOrderSerializer.create() - a genuine duplicate would be a
            # bug in that locking, not something expected to fire in
            # practice. Two Primary WOs both having
            # (parent_work_order=None, supplementary_sequence=None) doesn't
            # collide - Postgres/SQLite both treat NULL as distinct from
            # NULL in a unique constraint.
            models.UniqueConstraint(
                fields=["parent_work_order", "supplementary_sequence"],
                name="unique_supplementary_sequence_per_parent",
            )
        ]

    def __str__(self):
        return f"WO-{self.id} ({self.job_name})"

    def clean(self):
        # WRH-33/AC-6: only a Primary WO (parent_work_order is null) can be
        # a parent - a supplementary can't itself have supplementaries.
        # Django admin's default ModelForm calls full_clean() and exposes
        # every model field unrestricted, so this guard stays needed there
        # regardless of the API - matches the WRH-56 lesson that admin is a
        # real caller, not just the registered API routes. The API path
        # (WRH-53) enforces the identical rule its own way, via
        # WorkOrderSerializer.parent_work_order's queryset restriction
        # rather than by calling full_clean() (ModelSerializer.create()
        # doesn't call it), so both callers are covered without duplicating
        # the query logic.
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
