from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from inventory.models import SerializedItem
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
        # status "available" and an auto-generated QR code
        response = self.client.post(
            reverse("serializeditem-list"),
            {"serial_number": "SN-042", "product_type": self.product_type.id},
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["serial_number"], "SN-042")
        self.assertEqual(response.data["status"], SerializedItem.STATUS_AVAILABLE)
        self.assertTrue(response.data["qr_code"])

    def test_qr_code_filename_encodes_item_uuid(self):
        # TC-02: the QR code is generated from (and named after) the item's
        # own UUID, not the user-entered serial number
        response = self.client.post(
            reverse("serializeditem-list"),
            {"serial_number": "SN-042", "product_type": self.product_type.id},
        )
        item = SerializedItem.objects.get(id=response.data["id"])

        self.assertEqual(str(item.serial), response.data["serial"])
        self.assertIn(str(item.serial), item.qr_code.name)

    def test_duplicate_serial_number_is_rejected(self):
        # AC-2: the same serial number cannot be registered twice
        SerializedItemFactory(serial_number="SN-042", product_type=self.product_type)

        response = self.client.post(
            reverse("serializeditem-list"),
            {"serial_number": "SN-042", "product_type": self.product_type.id},
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("serial_number", response.data)

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

    def test_detail_route_is_not_registered(self):
        # WRH-22 only scopes register/list/filter/search - no retrieve/
        # update/destroy route exists yet for /serialized-items/<pk>/
        item = SerializedItemFactory(product_type=self.product_type)
        detail_url = f"/api/serialized-items/{item.pk}/"

        self.assertEqual(
            self.client.get(detail_url).status_code, status.HTTP_404_NOT_FOUND
        )
