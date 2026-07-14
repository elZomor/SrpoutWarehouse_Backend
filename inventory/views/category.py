from django.db.models import ProtectedError
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from inventory.models import Category
from inventory.serializers import CategorySerializer


class CategoryViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    # WRH-61 (PRD story US-026a) scoped create/list/search. WRH-62
    # (US-026b) adds delete-protection/archive; retrieve/update still
    # aren't needed by any story yet, so those routes stay unregistered.
    permission_classes = [IsAuthenticated]
    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    filter_backends = [SearchFilter]
    search_fields = Category.SEARCH_FIELDS

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action == "list":
            # AC-4/AC-6: archived categories are hidden from the default
            # list, which is also what the Product Type form's category
            # selector queries - so this one filter covers both ACs.
            queryset = queryset.filter(archived=False)
        return queryset

    def filter_queryset(self, queryset):
        # SearchFilter is only meant to scope the list endpoint. GenericAPIView
        # .get_object() (used by destroy/archive) also runs filter_queryset()
        # before its pk lookup, so leaving it active there would 404 a
        # perfectly valid category whenever the request carries a stray
        # ?search= param that doesn't match its name.
        if self.action != "list":
            return queryset
        return super().filter_queryset(queryset)

    @staticmethod
    def _delete_blocked_response(assigned_count):
        noun = "product type" if assigned_count == 1 else "product types"
        verb = "is" if assigned_count == 1 else "are"
        # `detail` is a fixed-English business-rule message (the API contract
        # per AC-3); `assigned_product_type_count` lets the frontend build its
        # own translated (AR/EN) message instead of showing `detail` as-is.
        # Built as a plain Response (not a raised ValidationError) so
        # assigned_product_type_count stays a JSON number, not a string - DRF's
        # ValidationError stringifies every leaf value it wraps.
        return Response(
            {
                "detail": (
                    f"Cannot delete — {assigned_count} {noun} {verb} "
                    "assigned to this category. Archive it instead."
                ),
                "assigned_product_type_count": assigned_count,
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    def destroy(self, request, *args, **kwargs):
        # AC-3/AC-5: only delete when no Product Types are assigned;
        # otherwise the manager must archive instead.
        instance = self.get_object()
        assigned_count = instance.product_types.count()
        if assigned_count:
            return self._delete_blocked_response(assigned_count)
        try:
            self.perform_destroy(instance)
        except ProtectedError:
            # A Product Type can be assigned to this category between the
            # count() check above and this delete (concurrent request) - the
            # FK's on_delete=PROTECT then raises here instead of leaving the
            # race to surface as an unhandled 500.
            return self._delete_blocked_response(instance.product_types.count())
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"])
    def archive(self, request, pk=None):
        # AC-4: archiving retires a category without touching its
        # existing Product Types or their history.
        category = self.get_object()
        category.archived = True
        category.save(update_fields=["archived"])
        return Response(self.get_serializer(category).data)
