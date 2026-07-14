from rest_framework import mixins, viewsets
from rest_framework.filters import SearchFilter
from rest_framework.permissions import IsAuthenticated

from inventory.models import ProductType
from inventory.serializers import ProductTypeSerializer


class ProductTypeViewSet(
    mixins.ListModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet
):
    # WRH-20 (PRD story US-002a) only scopes create/list/search; retrieve,
    # update, and archive/delete semantics are a separate PRD story
    # (US-002b), so only list+create are mixed in - no retrieve/update/
    # destroy routes get registered at all.
    permission_classes = [IsAuthenticated]
    # select_related avoids an N+1 query per row: CategorySerializer's
    # category_name field reads category.name on every serialized instance.
    queryset = ProductType.objects.select_related("category").all()
    serializer_class = ProductTypeSerializer
    filter_backends = [SearchFilter]
    search_fields = ProductType.SEARCH_FIELDS
