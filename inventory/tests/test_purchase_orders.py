from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from inventory.models import PurchaseOrder
from inventory.tests.factories import ProductTypeFactory


class PurchaseOrderTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="jane", email="jane@example.com", password="pw"
        )
        self.client.force_authenticate(user=self.user)
        self.product_type = ProductTypeFactory()

    def test_create_purchase_order_with_one_line_item(self):
        # TC-01/AC-1: a PO with supplier, date, and one line item is saved
        # with status "pending".
        response = self.client.post(
            reverse("purchaseorder-list"),
            {
                "supplier_name": "Acme Lighting Co",
                "order_date": "2026-07-01",
                "line_items": [
                    {"product_type": self.product_type.id, "expected_quantity": 5}
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["supplier_name"], "Acme Lighting Co")
        self.assertEqual(response.data["order_date"], "2026-07-01")
        self.assertEqual(response.data["status"], PurchaseOrder.STATUS_PENDING)
        self.assertEqual(len(response.data["line_items"]), 1)
        self.assertEqual(
            response.data["line_items"][0]["product_type"], self.product_type.id
        )
        self.assertEqual(response.data["line_items"][0]["expected_quantity"], 5)

    def test_create_purchase_order_with_multiple_line_items(self):
        # TC-02: a PO with 3 line items for different product types is
        # created with all 3, each with its own expected quantity.
        other_type = ProductTypeFactory()
        third_type = ProductTypeFactory()

        response = self.client.post(
            reverse("purchaseorder-list"),
            {
                "supplier_name": "Acme Lighting Co",
                "order_date": "2026-07-01",
                "line_items": [
                    {"product_type": self.product_type.id, "expected_quantity": 5},
                    {"product_type": other_type.id, "expected_quantity": 2},
                    {"product_type": third_type.id, "expected_quantity": 10},
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(response.data["line_items"]), 3)
        quantities_by_product_type = {
            item["product_type"]: item["expected_quantity"]
            for item in response.data["line_items"]
        }
        self.assertEqual(quantities_by_product_type[self.product_type.id], 5)
        self.assertEqual(quantities_by_product_type[other_type.id], 2)
        self.assertEqual(quantities_by_product_type[third_type.id], 10)

    def test_created_purchase_order_is_persisted_with_its_line_items(self):
        self.client.post(
            reverse("purchaseorder-list"),
            {
                "supplier_name": "Acme Lighting Co",
                "order_date": "2026-07-01",
                "line_items": [
                    {"product_type": self.product_type.id, "expected_quantity": 5}
                ],
            },
            format="json",
        )

        purchase_order = PurchaseOrder.objects.get()
        self.assertEqual(purchase_order.supplier_name, "Acme Lighting Co")
        self.assertEqual(purchase_order.line_items.count(), 1)
        self.assertEqual(purchase_order.line_items.get().expected_quantity, 5)

    def test_response_includes_product_type_name_per_line_item(self):
        response = self.client.post(
            reverse("purchaseorder-list"),
            {
                "supplier_name": "Acme Lighting Co",
                "order_date": "2026-07-01",
                "line_items": [
                    {"product_type": self.product_type.id, "expected_quantity": 5}
                ],
            },
            format="json",
        )

        self.assertEqual(
            response.data["line_items"][0]["product_type_name"],
            self.product_type.name,
        )

    def test_create_purchase_order_without_supplier_name_is_rejected(self):
        response = self.client.post(
            reverse("purchaseorder-list"),
            {
                "order_date": "2026-07-01",
                "line_items": [
                    {"product_type": self.product_type.id, "expected_quantity": 5}
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["supplier_name"], ["Supplier name is required."])

    def test_create_purchase_order_without_order_date_is_rejected(self):
        response = self.client.post(
            reverse("purchaseorder-list"),
            {
                "supplier_name": "Acme Lighting Co",
                "line_items": [
                    {"product_type": self.product_type.id, "expected_quantity": 5}
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("order_date", response.data)

    def test_create_purchase_order_without_line_items_is_rejected(self):
        # AC-1: "one or more line items" - an empty list isn't a valid PO.
        response = self.client.post(
            reverse("purchaseorder-list"),
            {
                "supplier_name": "Acme Lighting Co",
                "order_date": "2026-07-01",
                "line_items": [],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("line_items", response.data)
        self.assertEqual(PurchaseOrder.objects.count(), 0)

    def test_create_purchase_order_line_item_without_product_type_is_rejected(self):
        response = self.client.post(
            reverse("purchaseorder-list"),
            {
                "supplier_name": "Acme Lighting Co",
                "order_date": "2026-07-01",
                "line_items": [{"expected_quantity": 5}],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("line_items", response.data)

    def test_create_purchase_order_line_item_without_expected_quantity_is_rejected(
        self,
    ):
        response = self.client.post(
            reverse("purchaseorder-list"),
            {
                "supplier_name": "Acme Lighting Co",
                "order_date": "2026-07-01",
                "line_items": [{"product_type": self.product_type.id}],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("line_items", response.data)

    def test_status_cannot_be_overridden_on_create(self):
        # status is a read_only field - AC-1 guarantees every new PO is
        # "pending" regardless of what the client sends.
        response = self.client.post(
            reverse("purchaseorder-list"),
            {
                "supplier_name": "Acme Lighting Co",
                "order_date": "2026-07-01",
                "status": "received",
                "line_items": [
                    {"product_type": self.product_type.id, "expected_quantity": 5}
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], PurchaseOrder.STATUS_PENDING)

    def test_created_purchase_order_appears_in_list(self):
        response = self.client.post(
            reverse("purchaseorder-list"),
            {
                "supplier_name": "Acme Lighting Co",
                "order_date": "2026-07-01",
                "line_items": [
                    {"product_type": self.product_type.id, "expected_quantity": 5}
                ],
            },
            format="json",
        )
        created_id = response.data["id"]

        list_response = self.client.get(reverse("purchaseorder-list"))

        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertIn(created_id, [item["id"] for item in list_response.data])
        listed = next(item for item in list_response.data if item["id"] == created_id)
        self.assertEqual(len(listed["line_items"]), 1)

    def test_retrieve_update_destroy_routes_are_not_registered(self):
        # WRH-29 only scopes create+list - retrieve/update/destroy are
        # separate, unscoped stories, so no detail route exists at all yet.
        response = self.client.post(
            reverse("purchaseorder-list"),
            {
                "supplier_name": "Acme Lighting Co",
                "order_date": "2026-07-01",
                "line_items": [
                    {"product_type": self.product_type.id, "expected_quantity": 5}
                ],
            },
            format="json",
        )
        detail_url = f"/api/purchase-orders/{response.data['id']}/"

        self.assertEqual(
            self.client.get(detail_url).status_code, status.HTTP_404_NOT_FOUND
        )
