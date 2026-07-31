from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from inventory.models import Box
from inventory.tests.factories import (
    BoxFactory,
    ProductTypeFactory,
    SerializedItemFactory,
)


class BoxCreationTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="jane", email="jane@example.com", password="pw"
        )
        self.client.force_authenticate(user=self.user)
        self.product_type = ProductTypeFactory()

    def test_register_a_box_with_valid_items(self):
        # TC-01/AC-1: a Box is created with an auto-generated UUID and QR
        # code, containing exactly the selected items.
        items = [
            SerializedItemFactory(product_type=self.product_type) for _ in range(10)
        ]

        response = self.client.post(
            reverse("box-list"),
            {
                "code": "BX-001",
                "product_type": self.product_type.id,
                "item_ids": [item.id for item in items],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["code"], "BX-001")
        self.assertIsNotNone(response.data["uuid"])
        self.assertEqual(len(response.data["items"]), 10)
        box = Box.objects.get(code="BX-001")
        self.assertEqual(box.items.count(), 10)

    def test_created_box_appears_in_list(self):
        BoxFactory(code="BX-002", product_type=self.product_type)

        response = self.client.get(reverse("box-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        codes = [box["code"] for box in response.data]
        self.assertIn("BX-002", codes)

    def test_create_box_rejects_an_item_already_in_another_box(self):
        # An item already boxed can't be silently re-assigned into a second
        # box - Box contents are meant to be set once and never cleared
        # (SerializedItem.box's own comment); item_ids' box__isnull=True
        # queryset restriction is what actually enforces that.
        item = SerializedItemFactory(product_type=self.product_type)
        first_box_response = self.client.post(
            reverse("box-list"),
            {
                "code": "BX-FIRST",
                "product_type": self.product_type.id,
                "item_ids": [item.id],
            },
            format="json",
        )
        self.assertEqual(first_box_response.status_code, status.HTTP_201_CREATED)

        second_box_response = self.client.post(
            reverse("box-list"),
            {
                "code": "BX-SECOND",
                "product_type": self.product_type.id,
                "item_ids": [item.id],
            },
            format="json",
        )

        self.assertEqual(second_box_response.status_code, status.HTTP_400_BAD_REQUEST)
        item.refresh_from_db()
        self.assertEqual(item.box.code, "BX-FIRST")

    def test_create_box_requires_authentication(self):
        self.client.logout()

        response = self.client.post(
            reverse("box-list"),
            {"code": "BX-003", "product_type": self.product_type.id, "item_ids": []},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_retrieve_update_destroy_routes_are_not_registered(self):
        # No retrieve/update/destroy mixin is mixed in (box contents are
        # fixed after creation) and qr-code is the only detail route, so
        # DRF never registers a base "{pk}/" pattern at all - unlike a
        # viewset with at least one detail mixin (e.g. WorkOrder's retrieve),
        # this 404s at the URL-resolution level rather than 405ing on a
        # registered-but-unmapped method.
        box = BoxFactory(product_type=self.product_type)

        detail_url = f"/api/boxes/{box.id}/"

        self.assertEqual(
            self.client.get(detail_url).status_code, status.HTTP_404_NOT_FOUND
        )
        self.assertEqual(
            self.client.put(detail_url, {}, format="json").status_code,
            status.HTTP_404_NOT_FOUND,
        )
        self.assertEqual(
            self.client.delete(detail_url).status_code, status.HTTP_404_NOT_FOUND
        )


class BoxQrCodeTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="jane", email="jane@example.com", password="pw"
        )
        self.client.force_authenticate(user=self.user)

    def test_qr_code_endpoint_returns_png_encoding_box_uuid(self):
        # TC-02: the QR code is generated on demand from the box's own
        # UUID (not stored) - matches SerializedItemViewSet.qr_code()'s
        # identical behavior.
        box = BoxFactory()

        response = self.client.get(f"/api/boxes/{box.id}/qr-code/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertEqual(response.content, box.generate_qr_code_png())

    def test_qr_code_endpoint_404s_for_unknown_box(self):
        response = self.client.get("/api/boxes/999999/qr-code/")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
