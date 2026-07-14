from rest_framework import mixins, viewsets
from rest_framework.permissions import IsAuthenticated

from inventory.models import PurchaseOrder
from inventory.serializers import PurchaseOrderSerializer


class PurchaseOrderViewSet(
    mixins.ListModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet
):
    # WRH-29 (PRD story US-009a) only scopes create (+list, so a manager can
    # see the PO they just created) - field/business-rule validation
    # (WRH-30) and the receive-by-scan flow (US-010a) are separate stories,
    # so retrieve/update/destroy stay unregistered for now.
    permission_classes = [IsAuthenticated]
    # select_related/prefetch_related avoids an N+1 per line item: the
    # serializer's product_type_name field reads product_type.name on every
    # nested line item, for every purchase order in the list.
    queryset = PurchaseOrder.objects.prefetch_related("line_items__product_type").all()
    serializer_class = PurchaseOrderSerializer
