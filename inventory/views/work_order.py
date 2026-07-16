from rest_framework import mixins, viewsets
from rest_framework.permissions import IsAuthenticated

from inventory.models import WorkOrder
from inventory.serializers import WorkOrderSerializer


class WorkOrderViewSet(
    mixins.ListModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet
):
    # WRH-31 (US-011a) scopes create+list only - field validation (WRH-32),
    # supplementary creation, fulfillment, and the active list view are
    # separate stories, so retrieve/update/destroy stay unregistered.
    permission_classes = [IsAuthenticated]
    queryset = WorkOrder.objects.select_related("created_by").prefetch_related(
        "line_items__product_type"
    )
    serializer_class = WorkOrderSerializer

    def perform_create(self, serializer):
        # AC-1: "the creating user is recorded" - taken from the
        # authenticated session, never client-supplied (created_by is
        # read_only on the serializer).
        serializer.save(created_by=self.request.user)
