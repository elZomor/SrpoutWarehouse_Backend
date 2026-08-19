from django.db import IntegrityError
from django.db.models import Count, ProtectedError, Q
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.filters import SearchFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from inventory.models import ProductType, SerializedItem
from inventory.models.product_type import (
    AVAILABLE_COUNT_ANNOTATION,
    DAMAGED_COUNT_ANNOTATION,
    IN_MAINTENANCE_COUNT_ANNOTATION,
    MISSING_COUNT_ANNOTATION,
    OUT_COUNT_ANNOTATION,
    TOTAL_COUNT_ANNOTATION,
    WRITTEN_OFF_COUNT_ANNOTATION,
)
from inventory.serializers import (
    ProductTypeSerializer,
    ProductTypeStockSummarySerializer,
)


class ProductTypeViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    # WRH-20 (PRD story US-002a) scoped create/list/search. WRH-21
    # (US-002b) added delete-protection; retrieve/update still aren't
    # needed by any story yet, so those routes stay unregistered. The
    # archive action (also from WRH-21) was removed by WRH-73; the
    # `archived` field/filter stays as the only way to retire a product
    # type that still has SerializedItems registered (delete is
    # PROTECT-blocked).
    permission_classes = [IsAuthenticated]
    # select_related avoids an N+1 query per row: CategorySerializer's
    # category_name field reads category.name on every serialized instance.
    queryset = ProductType.objects.select_related("category").all()
    serializer_class = ProductTypeSerializer
    filter_backends = [SearchFilter]
    search_fields = ProductType.SEARCH_FIELDS

    def perform_create(self, serializer):
        # AC-2: the serializer's UniqueValidator on name is a SELECT-based
        # pre-check, so two concurrent creates with the same name can both
        # pass it and race to the INSERT - let the DB's unique constraint be
        # the real guard and translate its IntegrityError into the same 400
        # the pre-check would have given (mirrors SerializedItemViewSet's
        # identical handling of the serial_number race).
        try:
            serializer.save()
        except IntegrityError as exc:
            raise ValidationError(
                {"name": "A product type with this name already exists."}
            ) from exc

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action in ("list", "stock_summary"):
            # AC-4/AC-6/TC-06 (list); WRH-48/TC-07 (stock_summary) - archived
            # product types are hidden the same way everywhere they're
            # reachable, matching the SerializedItem registration form's
            # product-type selector and this repo's "exclude archived
            # everywhere, not just the default list" convention.
            queryset = queryset.filter(archived=False)
        if self.action == "stock_summary":
            # WRH-48/AC-1/AC-2/AC-5: one row per product type, every count a
            # direct DB-level Count(filter=...) rather than arithmetic - see
            # ProductTypeStockSummarySerializer's comment for why. AC-5 (a
            # product type with zero items) falls out for free: Count's SQL
            # aggregate returns 0 (not NULL) over an empty related set,
            # unlike Sum/Avg.
            queryset = queryset.annotate(
                **{
                    TOTAL_COUNT_ANNOTATION: Count("serialized_items"),
                    OUT_COUNT_ANNOTATION: Count(
                        "serialized_items",
                        filter=Q(serialized_items__status=SerializedItem.STATUS_OUT),
                    ),
                    DAMAGED_COUNT_ANNOTATION: Count(
                        "serialized_items",
                        filter=Q(
                            serialized_items__status=SerializedItem.STATUS_DAMAGED
                        ),
                    ),
                    MISSING_COUNT_ANNOTATION: Count(
                        "serialized_items",
                        filter=Q(
                            serialized_items__status=SerializedItem.STATUS_MISSING
                        ),
                    ),
                    AVAILABLE_COUNT_ANNOTATION: Count(
                        "serialized_items",
                        filter=Q(
                            serialized_items__status=SerializedItem.STATUS_AVAILABLE
                        ),
                    ),
                    IN_MAINTENANCE_COUNT_ANNOTATION: Count(
                        "serialized_items",
                        filter=Q(
                            serialized_items__status=(
                                SerializedItem.STATUS_IN_MAINTENANCE
                            )
                        ),
                    ),
                    WRITTEN_OFF_COUNT_ANNOTATION: Count(
                        "serialized_items",
                        filter=Q(
                            serialized_items__status=(SerializedItem.STATUS_WRITTEN_OFF)
                        ),
                    ),
                }
            )
        return queryset

    def filter_queryset(self, queryset):
        # SearchFilter is only meant to scope the list endpoint. GenericAPIView
        # .get_object() (used by destroy) also runs filter_queryset()
        # before its pk lookup, so leaving it active there would 404 a
        # perfectly valid product type whenever the request carries a stray
        # ?search= param that doesn't match its name.
        if self.action != "list":
            return queryset
        return super().filter_queryset(queryset)

    @staticmethod
    def _delete_blocked_response(registered_count):
        noun = "item" if registered_count == 1 else "items"
        verb = "is" if registered_count == 1 else "are"
        # `detail` is a fixed-English business-rule message (the API contract
        # per AC-3); `registered_item_count` lets the frontend build its own
        # translated (AR/EN) message instead of showing `detail` as-is. Built
        # as a plain Response (not a raised ValidationError) so
        # registered_item_count stays a JSON number, not a string - DRF's
        # ValidationError stringifies every leaf value it wraps.
        return Response(
            {
                "detail": (
                    f"Cannot delete — {registered_count} {noun} {verb} "
                    "registered under this product type. Reassign or remove "
                    "them first."
                ),
                "registered_item_count": registered_count,
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    def destroy(self, request, *args, **kwargs):
        # AC-3/AC-5: only delete when no SerializedItems are registered.
        instance = self.get_object()
        registered_count = instance.serialized_items.count()
        if registered_count:
            return self._delete_blocked_response(registered_count)
        try:
            self.perform_destroy(instance)
        except ProtectedError:
            # A SerializedItem can be registered against this product type
            # between the count() check above and this delete (concurrent
            # request) - the FK's on_delete=PROTECT then raises here instead
            # of leaving the race to surface as an unhandled 500.
            return self._delete_blocked_response(instance.serialized_items.count())
        return Response(status=status.HTTP_204_NO_CONTENT)

    def get_serializer_class(self):
        if self.action == "stock_summary":
            return ProductTypeStockSummarySerializer
        return super().get_serializer_class()

    @action(detail=False, methods=["get"], url_path="stock-summary")
    def stock_summary(self, request):
        # AC-6 (no product types at all) is just an empty list - reuses
        # ListModelMixin's list() (no pagination is configured project-wide,
        # so this returns every row, matching WorkOrderViewSet.active()'s
        # identical reuse of self.list()); get_queryset() above does the
        # actual shaping for this action.
        return self.list(request)
