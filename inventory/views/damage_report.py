from rest_framework import mixins, status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from inventory.models import DamageReport
from inventory.serializers import DamageReportCreateSerializer, DamageReportSerializer


class DamageReportViewSet(
    mixins.ListModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet
):
    # WRH-44 (US-021a) scopes create+list only, matching every other
    # first-story resource in this codebase (Category/ProductType/Box).
    permission_classes = [IsAuthenticated]
    queryset = DamageReport.objects.select_related(
        "serialized_item__product_type", "user"
    ).all()
    serializer_class = DamageReportSerializer

    def create(self, request, *args, **kwargs):
        # AC-1/AC-3: write shape (serial_number + optional note) genuinely
        # differs from the read shape (reference/product_type_name/
        # user_username) - DamageReportCreateSerializer.create() does the
        # actual lookup/lock/status-flip/Transaction logging, then the
        # response is rendered through the same DamageReportSerializer the
        # list action uses, matching PurchaseOrderViewSet.receive()'s
        # identical "validate with an input-only serializer, respond with
        # the resource's own read serializer" shape.
        input_serializer = DamageReportCreateSerializer(
            data=request.data, context={"request": request}
        )
        input_serializer.is_valid(raise_exception=True)
        report = input_serializer.save()
        return Response(
            DamageReportSerializer(report).data, status=status.HTTP_201_CREATED
        )
