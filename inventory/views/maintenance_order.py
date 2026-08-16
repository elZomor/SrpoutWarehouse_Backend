from django.db import transaction
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from inventory.models import MaintenanceOrder, SerializedItem
from inventory.serializers import (
    MaintenanceOrderResolveSerializer,
    MaintenanceOrderSerializer,
)


class MaintenanceOrderViewSet(
    mixins.ListModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet
):
    # WRH-46 (US-022a) scopes create+list; WRH-58 (US-022c) adds the
    # resolve() action below; WRH-47 (US-022b) adds the eligibility/
    # terminal-state business-rule guards on top of both.
    permission_classes = [IsAuthenticated]
    queryset = MaintenanceOrder.objects.prefetch_related("items")
    serializer_class = MaintenanceOrderSerializer

    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        # AC-1/AC-2: a line item's outcome flips it to "available" (fixed)
        # or "written_off" (not_fixable). AC-3: the MO's own status is
        # derived from all its items - "in_progress" once some but not all
        # are resolved, "completed" once every item is. WRH-47/AC-3 below
        # blocks re-resolving an item that already went through this once.
        maintenance_order = self.get_object()
        serializer = MaintenanceOrderResolveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item_id = serializer.validated_data["item"].id
        resolution = serializer.validated_data["resolution"]

        with transaction.atomic():
            # Lock the parent MO row first (matches WorkOrderViewSet's
            # "lock the row whose invariant you're protecting" convention -
            # the field this action ultimately writes, MO.status, is derived
            # from all of the MO's items), then the target item row, in that
            # fixed order so a concurrent resolve() against a different item
            # on the same MO can't deadlock against this one.
            maintenance_order = MaintenanceOrder.objects.select_for_update().get(
                pk=maintenance_order.pk
            )
            try:
                item = SerializedItem.objects.select_for_update().get(pk=item_id)
            except SerializedItem.DoesNotExist:
                raise ValidationError({"item_id": ["Item not found."]})
            if item.maintenance_order_id != maintenance_order.id:
                raise ValidationError(
                    {
                        "item_id": [
                            f"{item.serial_number} is not a line item on"
                            f" {maintenance_order.reference}"
                        ]
                    }
                )
            # WRH-47/AC-3: a resolution is final - resolve() flips status to
            # available/written_off and never back, so status no longer
            # being in_maintenance means this line item was already
            # resolved by an earlier call.
            if item.status != SerializedItem.STATUS_IN_MAINTENANCE:
                raise ValidationError(
                    {"item_id": [f"{item.serial_number} has already been resolved"]}
                )

            item.status = (
                SerializedItem.STATUS_AVAILABLE
                if resolution == MaintenanceOrderResolveSerializer.RESOLUTION_FIXED
                else SerializedItem.STATUS_WRITTEN_OFF
            )
            item.save(update_fields=["status"])

            self._finalize_resolution_status(maintenance_order)

        return Response(MaintenanceOrderSerializer(maintenance_order).data, status=200)

    @staticmethod
    def _finalize_resolution_status(maintenance_order):
        # Safe as plain unlocked .filter()/.count() reads only because the
        # parent MaintenanceOrder row is already locked by the caller -
        # matches WorkOrderViewSet._finalize_return_status's identical
        # "lock the parent, then read the children unlocked" reasoning.
        items = SerializedItem.objects.filter(maintenance_order=maintenance_order)
        total = items.count()
        resolved = items.filter(
            status__in=[
                SerializedItem.STATUS_AVAILABLE,
                SerializedItem.STATUS_WRITTEN_OFF,
            ]
        ).count()
        # total == 0 can't happen through the API (creation requires >=1
        # item, nothing ever removes one) but Django admin can reach a
        # degenerate state a currently-registered route can't - matches
        # WRH-56's identical "all()/count() over an empty collection" lesson
        # by leaving status untouched rather than mis-deriving it.
        if total == 0:
            return
        if resolved == total:
            maintenance_order.status = MaintenanceOrder.STATUS_COMPLETED
        elif resolved > 0:
            maintenance_order.status = MaintenanceOrder.STATUS_IN_PROGRESS
        maintenance_order.save(update_fields=["status"])
