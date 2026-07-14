from django.db.models import ProtectedError
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from inventory.models import ProductType
from inventory.serializers import ProductTypeSerializer


class ProductTypeViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    # WRH-20 (PRD story US-002a) scoped create/list/search. WRH-21
    # (US-002b) adds delete-protection/archive; retrieve/update still
    # aren't needed by any story yet, so those routes stay unregistered.
    permission_classes = [IsAuthenticated]
    # select_related avoids an N+1 query per row: CategorySerializer's
    # category_name field reads category.name on every serialized instance.
    queryset = ProductType.objects.select_related("category").all()
    serializer_class = ProductTypeSerializer
    filter_backends = [SearchFilter]
    search_fields = ProductType.SEARCH_FIELDS

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action == "list":
            # AC-4/AC-6/TC-06: archived product types are hidden from the
            # default list, which is also what the SerializedItem
            # registration form's product-type selector queries.
            queryset = queryset.filter(archived=False)
        return queryset

    def filter_queryset(self, queryset):
        # SearchFilter is only meant to scope the list endpoint. GenericAPIView
        # .get_object() (used by destroy/archive) also runs filter_queryset()
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
                    "registered under this product type. Archive it instead."
                ),
                "registered_item_count": registered_count,
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    def destroy(self, request, *args, **kwargs):
        # AC-3/AC-5: only delete when no SerializedItems are registered;
        # otherwise the manager must archive instead.
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

    @action(detail=True, methods=["post"])
    def archive(self, request, pk=None):
        # AC-4: archiving retires a product type without touching its
        # existing SerializedItems or their history.
        product_type = self.get_object()
        product_type.archived = True
        product_type.save(update_fields=["archived"])
        return Response(self.get_serializer(product_type).data)
