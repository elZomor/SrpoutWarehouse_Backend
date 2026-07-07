from django.contrib import admin

from inventory.models import ProductType


@admin.register(ProductType)
class ProductTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "model_code")
    search_fields = ProductType.SEARCH_FIELDS
