from rest_framework import serializers
from rest_framework.validators import UniqueValidator

from inventory.models import Category


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "description", "archived"]
        read_only_fields = ["archived"]
        extra_kwargs = {
            "name": {
                "error_messages": {
                    "blank": "Name is required.",
                    "required": "Name is required.",
                },
                "validators": [
                    UniqueValidator(
                        queryset=Category.objects.all(),
                        message="A category with this name already exists.",
                    )
                ],
            },
        }
