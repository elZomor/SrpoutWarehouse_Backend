from django.contrib.auth.models import User
from django.db import models

from inventory.models.serialized_item import SerializedItem
from inventory.models.work_order import WorkOrder


class Transaction(models.Model):
    # WRH-49/AC-1: every stock movement type the log must be able to show.
    # Only RECEIVE/ISSUE/RETURN are currently produced by a real action
    # (PurchaseOrderViewSet.receive(), WorkOrderViewSet.complete(),
    # WorkOrderViewSet.return_item()) - DAMAGED/TRANSFER/MISSING/WRITTEN_OFF
    # have no status-transition endpoint yet (see SerializedItem's own
    # comment on STATUS_DAMAGED/STATUS_MISSING) so they exist here purely so
    # the log/filter can represent them once a future ticket starts writing
    # them.
    TYPE_RECEIVE = "receive"
    TYPE_ISSUE = "issue"
    TYPE_RETURN = "return"
    TYPE_DAMAGED = "damaged"
    TYPE_TRANSFER = "transfer"
    TYPE_MISSING = "missing"
    TYPE_WRITTEN_OFF = "written_off"
    TYPE_CHOICES = [
        (TYPE_RECEIVE, "Receive"),
        (TYPE_ISSUE, "Issue"),
        (TYPE_RETURN, "Return"),
        (TYPE_DAMAGED, "Damaged"),
        (TYPE_TRANSFER, "Transfer"),
        (TYPE_MISSING, "Missing"),
        (TYPE_WRITTEN_OFF, "Written off"),
    ]

    transaction_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    # PROTECT: the log is an audit trail - the item it's about shouldn't be
    # able to vanish out from under a transaction row, matching every other
    # "shouldn't disappear out from under an audit/claim record" FK in this
    # codebase (SerializedItem.purchase_order_line_item,
    # WorkOrder.created_by, etc).
    serialized_item = models.ForeignKey(
        SerializedItem, on_delete=models.PROTECT, related_name="transactions"
    )
    # Null for transaction types with no WO context (e.g. receive).
    work_order = models.ForeignKey(
        WorkOrder,
        on_delete=models.PROTECT,
        related_name="transactions",
        null=True,
        blank=True,
    )
    # AC-3's filter target. A short, deterministic id-based string
    # ("WO-<id>"/"PO-<id>") written at creation time by whichever action
    # produced this row - deliberately not WorkOrder.__str__/PurchaseOrder
    # .__str__ (both include the job/supplier name), since a filterable
    # reference needs to stay a stable, exact-matchable value.
    reference_number = models.CharField(max_length=255, blank=True, default="")
    # PROTECT: matches WorkOrder.created_by's "audit trail shouldn't lose
    # its user" rationale.
    user = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="transactions"
    )
    note = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.get_transaction_type_display()} — {self.serialized_item}"
