from unittest import mock

from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from inventory.models import SerializedItem
from inventory.serializers.serialized_item import SerializedItemSerializer
from inventory.tests.factories import ProductTypeFactory, SerializedItemFactory


class SerializedItemTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="jane", email="jane@example.com", password="pw"
        )
        self.client.force_authenticate(user=self.user)
        self.product_type = ProductTypeFactory()

    def test_register_serialized_item(self):
        # TC-01/AC-1: registering a new serial number creates an item with
        # status "available"
        response = self.client.post(
            reverse("serializeditem-list"),
            {"serial_number": "SN-042", "product_type": self.product_type.id},
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["serial_number"], "SN-042")
        self.assertEqual(response.data["status"], SerializedItem.STATUS_AVAILABLE)

    def test_qr_code_endpoint_returns_png_encoding_item_uuid(self):
        # TC-02: the QR code is generated on demand from the item's own
        # UUID (not stored) - the action returns the same PNG bytes the
        # model's own generator produces for that UUID.
        item = SerializedItemFactory(
            serial_number="SN-042", product_type=self.product_type
        )

        response = self.client.get(f"/api/serialized-items/{item.id}/qr-code/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertEqual(response.content, item.generate_qr_code_png())

    def test_qr_code_endpoint_404s_for_unknown_item(self):
        response = self.client.get("/api/serialized-items/999999/qr-code/")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_duplicate_serial_number_is_rejected(self):
        # TC-01/AC-1: the same serial number cannot be registered twice,
        # under the same product type, with the exact AC-1 error text
        SerializedItemFactory(serial_number="SN-042", product_type=self.product_type)

        response = self.client.post(
            reverse("serializeditem-list"),
            {"serial_number": "SN-042", "product_type": self.product_type.id},
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["serial_number"],
            ["Serial number SN-042 is already registered."],
        )

    def test_duplicate_serial_number_is_rejected_across_product_types(self):
        # TC-02/AC-2: uniqueness is global, not scoped to a product type
        SerializedItemFactory(serial_number="SN-042", product_type=self.product_type)
        other_type = ProductTypeFactory()

        response = self.client.post(
            reverse("serializeditem-list"),
            {"serial_number": "SN-042", "product_type": other_type.id},
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["serial_number"],
            ["Serial number SN-042 is already registered."],
        )

    def test_blank_serial_number_is_rejected(self):
        # TC-03/AC-3
        response = self.client.post(
            reverse("serializeditem-list"),
            {"serial_number": "", "product_type": self.product_type.id},
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["serial_number"], ["Serial number is required."])

    def test_missing_product_type_is_rejected(self):
        # TC-04/AC-4
        response = self.client.post(
            reverse("serializeditem-list"), {"serial_number": "SN-042"}
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["product_type"], ["Product type is required."])

    def test_validate_serial_number_excludes_current_instance(self):
        # No update route exists yet (Create+List only), but
        # validate_serial_number() must not reject an existing instance
        # against its own unchanged serial number once one is added -
        # exercise the serializer directly with instance= set.
        item = SerializedItemFactory(
            serial_number="SN-042", product_type=self.product_type
        )
        serializer = SerializedItemSerializer(instance=item)

        self.assertEqual(serializer.validate_serial_number("SN-042"), "SN-042")

    def test_duplicate_serial_number_race_still_returns_400(self):
        # AC-1/AC-5: simulates two requests racing past the serializer's
        # SELECT-based check (disabled here to stand in for that race) so
        # the DB's unique constraint is the only thing left to catch the
        # duplicate - proves perform_create() translates the resulting
        # IntegrityError into a 400 with the same AC-1 wording, not an
        # unhandled 500.
        SerializedItemFactory(serial_number="SN-042", product_type=self.product_type)

        with mock.patch(
            "inventory.serializers.serialized_item.SerializedItemSerializer"
            ".validate_serial_number",
            side_effect=lambda value: value,
        ):
            response = self.client.post(
                reverse("serializeditem-list"),
                {"serial_number": "SN-042", "product_type": self.product_type.id},
            )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["serial_number"],
            ["Serial number SN-042 is already registered."],
        )

    def test_list_filtered_by_product_type(self):
        # TC-03/AC-3: filtering the list by product type only returns items
        # under that product type
        other_type = ProductTypeFactory()
        item = SerializedItemFactory(
            serial_number="SN-001", product_type=self.product_type
        )
        SerializedItemFactory(serial_number="SN-999", product_type=other_type)

        response = self.client.get(
            reverse("serializeditem-list"), {"product_type": self.product_type.id}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([i["id"] for i in response.data], [item.id])

    def test_search_by_full_serial_number(self):
        # TC-04: search matches an exact serial number
        SerializedItemFactory(serial_number="SN-042", product_type=self.product_type)
        SerializedItemFactory(serial_number="SN-999", product_type=self.product_type)

        response = self.client.get(reverse("serializeditem-list"), {"search": "SN-042"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([i["serial_number"] for i in response.data], ["SN-042"])

    def test_search_by_partial_serial_number(self):
        # TC-05: search also matches a partial serial number
        SerializedItemFactory(serial_number="SN-042", product_type=self.product_type)
        SerializedItemFactory(serial_number="AB-999", product_type=self.product_type)

        response = self.client.get(reverse("serializeditem-list"), {"search": "SN-0"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([i["serial_number"] for i in response.data], ["SN-042"])

    def test_list_includes_product_type_name_and_last_wo_reference(self):
        # TC-03/TC-06: list rows carry the fields the frontend needs to
        # render (product type name, status, last WO reference placeholder)
        SerializedItemFactory(serial_number="SN-042", product_type=self.product_type)

        response = self.client.get(reverse("serializeditem-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        item = response.data[0]
        self.assertEqual(item["product_type_name"], self.product_type.name)
        self.assertEqual(item["last_work_order_reference"], "")

    def test_register_serialized_item_with_archived_product_type_is_rejected(self):
        # TC-06/WRH-21: an archived product type is hidden from the
        # selector, but the API itself must also reject it directly - a
        # stale/replayed id shouldn't be able to register a new item
        # against a retired product type.
        archived_type = ProductTypeFactory(archived=True)

        response = self.client.post(
            reverse("serializeditem-list"),
            {"serial_number": "SN-042", "product_type": archived_type.id},
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("product_type", response.data)

    def test_detail_route_is_not_registered(self):
        # WRH-22 only scopes register/list/filter/search - no retrieve/
        # update/destroy route exists yet for /serialized-items/<pk>/
        item = SerializedItemFactory(product_type=self.product_type)
        detail_url = f"/api/serialized-items/{item.pk}/"

        self.assertEqual(
            self.client.get(detail_url).status_code, status.HTTP_404_NOT_FOUND
        )
