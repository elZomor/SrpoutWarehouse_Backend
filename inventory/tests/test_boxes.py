from unittest.mock import patch

from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from inventory.models import Box, ProductType, SerializedItem
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
        # TC-02/AC-2: an item already boxed can't be silently re-assigned
        # into a second box - Box contents are meant to be set once and
        # never cleared (SerializedItem.box's own comment).
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
        self.assertEqual(
            second_box_response.data["item_ids"],
            [f"{item.serial_number} is already in box BX-FIRST"],
        )
        item.refresh_from_db()
        self.assertEqual(item.box.code, "BX-FIRST")

    def test_create_box_rejects_an_item_of_a_different_product_type(self):
        # TC-01/AC-1
        other_product_type = ProductTypeFactory()
        item = SerializedItemFactory(product_type=other_product_type)

        response = self.client.post(
            reverse("box-list"),
            {
                "code": "BX-004",
                "product_type": self.product_type.id,
                "item_ids": [item.id],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["item_ids"],
            [f"{item.serial_number} does not match this box's product type"],
        )
        self.assertFalse(Box.objects.filter(code="BX-004").exists())

    def test_create_box_rejects_a_non_available_item(self):
        # TC-05/AC-5
        item = SerializedItemFactory(product_type=self.product_type, status="out")

        response = self.client.post(
            reverse("box-list"),
            {
                "code": "BX-005",
                "product_type": self.product_type.id,
                "item_ids": [item.id],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["item_ids"],
            [f"{item.serial_number} is not available to box"],
        )

    def test_create_box_requires_a_box_code(self):
        # TC-03/AC-3
        item = SerializedItemFactory(product_type=self.product_type)

        response = self.client.post(
            reverse("box-list"),
            {
                "code": "",
                "product_type": self.product_type.id,
                "item_ids": [item.id],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["code"], ["Box code is required."])

    def test_create_box_requires_at_least_one_item(self):
        # TC-04/AC-4
        response = self.client.post(
            reverse("box-list"),
            {"code": "BX-006", "product_type": self.product_type.id, "item_ids": []},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["item_ids"], ["Select at least one item."])

    def test_create_box_rechecks_archived_status_after_lock_is_acquired(self):
        # BoxSerializer.product_type's queryset filter only catches an
        # already-archived product type - it can't catch one archived in
        # the window between that validation and create()'s
        # select_for_update()-guarded re-fetch. Simulate a concurrent
        # archive landing in exactly that window by hooking create()'s one
        # select_for_update() call site directly on ProductType's manager,
        # matching PurchaseOrderReceiveSerializer's identical regression
        # test (WRH-56) for the same guard shape.
        item = SerializedItemFactory(product_type=self.product_type)
        original_select_for_update = ProductType.objects.select_for_update
        product_type = self.product_type

        def archive_then_lock(*args, **kwargs):
            product_type.archived = True
            product_type.save(update_fields=["archived"])
            return original_select_for_update(*args, **kwargs)

        with patch.object(ProductType.objects, "select_for_update", archive_then_lock):
            response = self.client.post(
                reverse("box-list"),
                {
                    "code": "BX-RACE",
                    "product_type": self.product_type.id,
                    "item_ids": [item.id],
                },
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("product_type", response.data)
        self.assertFalse(Box.objects.filter(code="BX-RACE").exists())
        item.refresh_from_db()
        self.assertIsNone(item.box)

    def test_create_box_requires_authentication(self):
        self.client.logout()

        response = self.client.post(
            reverse("box-list"),
            {"code": "BX-003", "product_type": self.product_type.id, "item_ids": []},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_update_destroy_routes_are_not_registered(self):
        # WRH-71 adds retrieve (box detail) but update/destroy still aren't
        # mixed in - box contents are fixed after creation - so those two
        # 405 on the now-registered "{pk}/" pattern rather than 404ing at
        # URL-resolution level (retrieve's mixin means the base pattern
        # exists now, unlike before WRH-71).
        box = BoxFactory(product_type=self.product_type)

        detail_url = f"/api/boxes/{box.id}/"

        self.assertEqual(
            self.client.put(detail_url, {}, format="json").status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )
        self.assertEqual(
            self.client.delete(detail_url).status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    def test_retrieve_returns_box_detail_with_its_items(self):
        # AC-1/AC-2/TC-01: clicking a box shows the items packed inside it.
        items = [
            SerializedItemFactory(product_type=self.product_type) for _ in range(3)
        ]
        box = BoxFactory(product_type=self.product_type)
        SerializedItem.objects.filter(pk__in=[i.pk for i in items]).update(box=box)

        response = self.client.get(f"/api/boxes/{box.id}/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["code"], box.code)
        self.assertEqual(
            {item["id"] for item in response.data["items"]},
            {item.id for item in items},
        )

    def test_retrieve_empty_box_returns_empty_items_list(self):
        # AC-3/TC-02: an empty box isn't an error, just an empty list.
        box = BoxFactory(product_type=self.product_type)

        response = self.client.get(f"/api/boxes/{box.id}/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["items"], [])

    def test_retrieve_only_shows_items_currently_assigned_to_the_box(self):
        # AC-5: an item that was in this box but has since moved to another
        # box (e.g. reassigned via a different flow) shouldn't still show.
        box = BoxFactory(product_type=self.product_type)
        other_box = BoxFactory(product_type=self.product_type)
        item = SerializedItemFactory(product_type=self.product_type, box=other_box)

        response = self.client.get(f"/api/boxes/{box.id}/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotIn(item.id, [i["id"] for i in response.data["items"]])

    def test_retrieve_404s_for_unknown_box(self):
        # TC-05: a non-existent/deleted box 404s, not a crash.
        response = self.client.get("/api/boxes/999999/")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_retrieve_requires_authentication(self):
        self.client.force_authenticate(user=None)
        box = BoxFactory(product_type=self.product_type)

        response = self.client.get(f"/api/boxes/{box.id}/")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


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
