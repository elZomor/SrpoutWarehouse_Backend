from django.contrib import admin

from inventory.models import Category, ProductType, SerializedItem


@admin.register(ProductType)
class ProductTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "model_code", "category", "archived")
    list_filter = ("archived",)
    search_fields = ProductType.SEARCH_FIELDS


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "archived")
    list_filter = ("archived",)
    search_fields = Category.SEARCH_FIELDS


@admin.register(SerializedItem)
class SerializedItemAdmin(admin.ModelAdmin):
    list_display = ("serial_number", "product_type", "status", "serial")
    list_filter = ("status", "product_type")
    search_fields = SerializedItem.SEARCH_FIELDS
