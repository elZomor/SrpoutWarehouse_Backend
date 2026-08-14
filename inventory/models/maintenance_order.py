from django.db import models


class MaintenanceOrder(models.Model):
    # WRH-46/AC-1: creation-time status. WRH-58/AC-3 adds the two states
    # this MO reaches as its line items get resolved - "open" (creation
    # default) stays until the first item is resolved.
    STATUS_OPEN = "open"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_COMPLETED = "completed"
    STATUS_CHOICES = [
        (STATUS_OPEN, "Open"),
        (STATUS_IN_PROGRESS, "In progress"),
        (STATUS_COMPLETED, "Completed"),
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
