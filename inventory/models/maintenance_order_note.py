from django.contrib.auth.models import User
from django.db import models

from inventory.models.maintenance_order import MaintenanceOrder
from inventory.models.serialized_item import SerializedItem


class MaintenanceOrderNote(models.Model):
    # WRH-77/AC-5: one record per action (create/fixed/written_off), never
    # overwritten - matches DamageReport's identical "TextField, no
    # max_length" note shape (AC-6: long text must not truncate/error).
    ACTION_CREATED = "created"
    ACTION_FIXED = "fixed"
    ACTION_WRITTEN_OFF = "written_off"
    ACTION_CHOICES = [
        (ACTION_CREATED, "Created"),
        (ACTION_FIXED, "Fixed"),
        (ACTION_WRITTEN_OFF, "Written off"),
    ]

    maintenance_order = models.ForeignKey(
        MaintenanceOrder, on_delete=models.PROTECT, related_name="notes"
    )
    # Null for a "created" note (the MO itself, not any one line item, is
    # the subject) - set for "fixed"/"written_off" notes, which are always
    # about the specific item just resolved.
    item = models.ForeignKey(
        SerializedItem,
        on_delete=models.PROTECT,
        related_name="maintenance_notes",
        null=True,
        blank=True,
    )
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    text = models.TextField()
    # PROTECT matches DamageReport.user/Transaction.user's identical
    # "audit trail shouldn't lose its user" reasoning.
    user = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="maintenance_notes"
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.maintenance_order.reference} note ({self.action})"
