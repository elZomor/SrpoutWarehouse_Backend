from django.http import HttpResponse
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated

from inventory.models import Box
from inventory.serializers import BoxSerializer


class BoxViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    # WRH-26 (US-007a) scopes register+list+QR; field/business-rule
    # validation (WRH-27) tightens BoxSerializer.item_ids further; WRH-71
    # adds retrieve (box detail, i.e. its packed items) for the box-detail
    # UI - box contents themselves are still fixed after creation (PRD v0.5
    # §2.2 Out of Scope: "Box contents modification"), so update/destroy
    # stay unregistered, matching this repo's "only the mixins the ticket
    # scopes" convention.
    permission_classes = [IsAuthenticated]
    queryset = Box.objects.select_related("product_type").prefetch_related("items")
    serializer_class = BoxSerializer

    @action(detail=True, methods=["get"], url_path="qr-code")
    def qr_code(self, request, pk=None):
        # AC-2/TC-02: not stored, regenerated on demand - matches
        # SerializedItemViewSet.qr_code()'s identical reasoning.
        box = self.get_object()
        return HttpResponse(box.generate_qr_code_png(), content_type="image/png")
