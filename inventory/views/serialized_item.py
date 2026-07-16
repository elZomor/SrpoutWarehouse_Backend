import base64

from django.db import IntegrityError
from django.http import HttpResponse
from django.template.loader import render_to_string
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.filters import SearchFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from django_filters.rest_framework import DjangoFilterBackend
from weasyprint import HTML

from inventory.models import SerializedItem
from inventory.serializers import SerializedItemSerializer
from inventory.serializers.serialized_item import duplicate_serial_number_message


class SerializedItemViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    # WRH-22 (PRD story US-003a) scoped register/list/filter/search; WRH-66
    # (US-003e) adds destroy. WRH-54 (US-013a) later gave SerializedItem a
    # downstream FK (work_order_line_item) plus reserved/out statuses -
    # destroy() below blocks deleting a claimed item instead of the hard,
    # unconditional delete this viewset started with, since PROTECT on that
    # FK only guards the WorkOrderLineItem side, not this one.
    # retrieve/update are still separate, unscoped stories.
    permission_classes = [IsAuthenticated]
    # select_related avoids an N+1 query per row: the serializer's
    # product_type_name field reads product_type.name on every instance.
    queryset = SerializedItem.objects.select_related("product_type").all()
    serializer_class = SerializedItemSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ["product_type"]
    search_fields = SerializedItem.SEARCH_FIELDS

    def perform_create(self, serializer):
        # AC-2: the serializer's UniqueValidator on serial_number is a
        # SELECT-based pre-check, so two concurrent registrations of the
        # same serial number can both pass it and race to the INSERT - let
        # the DB's unique constraint be the real guard and translate its
        # IntegrityError into the same 400 the pre-check would have given.
        try:
            serializer.save()
        except IntegrityError as exc:
            serial_number = serializer.validated_data["serial_number"]
            raise ValidationError(
                {"serial_number": [duplicate_serial_number_message(serial_number)]}
            ) from exc

    def destroy(self, request, *args, **kwargs):
        # WRH-54: deleting a reserved (mid-scan) or out (already fulfilled)
        # item would silently corrupt a WorkOrder's live scanned_quantity
        # count and, for an "out" item, erase the fulfillment audit trail
        # last_work_order_reference records - matches
        # CategoryViewSet.destroy()'s "block instead of raw delete, return a
        # 400 with a translatable detail" convention.
        instance = self.get_object()
        if instance.status != SerializedItem.STATUS_AVAILABLE:
            return Response(
                {
                    "detail": (
                        "Cannot delete — this item is "
                        f"{instance.get_status_display().lower()} and linked"
                        " to a work order."
                    ),
                    "status": instance.status,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        self.perform_destroy(instance)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["get"], url_path="qr-code")
    def qr_code(self, request, pk=None):
        # Not stored (WRH-22 revision): the QR only ever encodes this item's
        # own stable UUID, so it's regenerated on every call rather than
        # kept as a rendered image blob - this is only hit on reprint
        # (damaged label), never on the normal list/detail path.
        item = self.get_object()
        return HttpResponse(item.generate_qr_code_png(), content_type="image/png")

    @action(detail=False, methods=["get"], url_path="qr-pdf")
    def qr_pdf(self, request):
        # AC-4/AC-5: DjangoFilterBackend directly (not self.filter_queryset,
        # which would also pull in SearchFilter) so ?product_type=<id>
        # scopes the PDF to one product type and the param omitted (the
        # "All" selection) covers every registered item - a stray ?search=
        # must not silently narrow a label batch the way it's intentionally
        # allowed to narrow the list view. Matches CategoryViewSet's/
        # ProductTypeViewSet's convention of keeping SearchFilter scoped to
        # the list action only.
        queryset = DjangoFilterBackend().filter_queryset(
            request, self.get_queryset(), self
        )
        items = [
            {
                "serial_number": item.serial_number,
                "product_type_name": item.product_type.name,
                "qr_code_base64": base64.b64encode(item.generate_qr_code_png()).decode(
                    "ascii"
                ),
            }
            for item in queryset
        ]
        if not items:
            detail = (
                "No serialized items found for the selected product type."
                if request.query_params.get("product_type")
                else "No serialized items are registered yet."
            )
            raise ValidationError({"detail": detail})
        html = render_to_string("inventory/qr_labels_pdf.html", {"items": items})
        response = HttpResponse(
            HTML(string=html).write_pdf(), content_type="application/pdf"
        )
        response["Content-Disposition"] = 'attachment; filename="qr-labels.pdf"'
        return response
