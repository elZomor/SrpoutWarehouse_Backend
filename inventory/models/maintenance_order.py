from django.db import models


class MaintenanceOrder(models.Model):
    # WRH-46/AC-1: creation-time status only - resolving individual line
    # items (fixed/not_fixable) is US-022c/WRH-47's separate workflow, split
    # from this story per the ticket's re-slice note, so no other status
    # value exists yet for this model to reach.
    STATUS_OPEN = "open"
    STATUS_CHOICES = [
        (STATUS_OPEN, "Open"),
    ]

    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_OPEN
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return self.reference

    @property
    def reference(self):
        # PRD §6.5: sequential "MO-0001", "MO-0002", ... - the auto-increment
        # PK already is that sequence, zero-padded to 4 digits, matching
        # DamageReport.reference's identical convention.
        return f"MO-{self.id:04d}"
