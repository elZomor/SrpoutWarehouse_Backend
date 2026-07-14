from django.db import IntegrityError
from rest_framework import mixins, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.filters import SearchFilter
from rest_framework.permissions import IsAuthenticated

from django_filters.rest_framework import DjangoFilterBackend

from inventory.models import SerializedItem
from inventory.serializers import SerializedItemSerializer


class SerializedItemViewSet(
    mixins.ListModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet
):
    # WRH-22 (PRD story US-003a) only scopes register/list/filter/search;
    # retrieve/update/destroy are separate stories, so only list+create are
    # mixed in - no retrieve/update/destroy routes get registered at all.
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
            raise ValidationError(
                {
                    "serial_number": (
                        "serialized item with this serial number already exists."
                    )
                }
            ) from exc
