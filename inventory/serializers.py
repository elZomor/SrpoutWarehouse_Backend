from django.contrib.auth.models import User
from rest_framework import serializers
from rest_framework.validators import UniqueValidator

from inventory.models import Category, ProductType


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "username", "email", "first_name", "last_name"]
        read_only_fields = fields


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)


class ProductTypeSerializer(serializers.ModelSerializer):
    # AC-6/WRH-62: an archived category isn't a valid parent for a new
    # Product Type - restrict the write-side queryset so this is enforced
    # at the API layer, not just hidden from the category dropdown in the
    # UI. to_representation() doesn't consult this queryset, so an existing
    # Product Type whose category gets archived later still reads back fine.
    category = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.filter(archived=False),
        error_messages={
            "does_not_exist": "Select a category that exists and is not archived."
        },
    )
    category_name = serializers.CharField(source="category.name", read_only=True)

    class Meta:
        model = ProductType
        fields = [
            "id",
            "name",
            "model_code",
            "description",
            "category",
            "category_name",
        ]


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
