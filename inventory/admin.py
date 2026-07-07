from django.contrib import admin

from inventory.models import ProductType


@admin.register(ProductType)
class ProductTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "model_code", "description")
    search_fields = ("name", "model_code")
