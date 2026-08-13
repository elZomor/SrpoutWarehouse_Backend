from django.contrib.auth.models import User
from django.db import models

from inventory.models.serialized_item import SerializedItem


class DamageReport(models.Model):
    # WRH-44/AC-1: created when a manager reports an available item damaged
    # directly, not via WRH-38/39's return-flow damage branch (that path
    # already writes its own Transaction.TYPE_DAMAGED row with no DR
    # reference of its own and no dedicated list). PROTECT matches
    # Transaction.serialized_item's identical "shouldn't vanish out from
    # under an audit/claim record" reasoning.
    serialized_item = models.ForeignKey(
        SerializedItem, on_delete=models.PROTECT, related_name="damage_reports"
    )
    note = models.TextField(blank=True, default="")
    # PROTECT matches Transaction.user's identical "audit trail shouldn't
    # lose its user" reasoning.
    user = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="damage_reports"
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created_at", "id"]

    @property
    def reference(self):
        # PRD §6.5: sequential "DR-0001", "DR-0002", ... - the auto-increment
        # PK already is that sequence (matches WorkOrder/PurchaseOrder's
        # "WO-<id>"/"PO-<id>" convention), zero-padded to 4 digits per this
        # story's specific format.
        return f"DR-{self.id:04d}"

    def __str__(self):
        return self.reference
