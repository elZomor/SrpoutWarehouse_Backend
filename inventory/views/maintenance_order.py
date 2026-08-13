from rest_framework import mixins, viewsets
from rest_framework.permissions import IsAuthenticated

from inventory.models import MaintenanceOrder
from inventory.serializers import MaintenanceOrderSerializer


class MaintenanceOrderViewSet(
    mixins.ListModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet
):
    # WRH-46 (US-022a) scopes create+list only - resolving individual line
    # items is WRH-47/US-022c's separate scope, matching BoxViewSet's
    # identical "only the mixins the ticket scopes" convention.
    permission_classes = [IsAuthenticated]
    queryset = MaintenanceOrder.objects.prefetch_related("items")
    serializer_class = MaintenanceOrderSerializer
