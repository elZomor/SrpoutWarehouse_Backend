from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from inventory.models import DamageReport, SerializedItem, Transaction
from inventory.tests.factories import ProductTypeFactory, SerializedItemFactory


class DamageReportCreateTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="jane", email="jane@example.com", password="pw"
        )
        self.client.force_authenticate(user=self.user)
        self.product_type = ProductTypeFactory()

    def create(self, serial_number, note=None):
        payload = {"serial_number": serial_number}
        if note is not None:
            payload["note"] = note
        return self.client.post(reverse("damagereport-list"), payload)

    def test_creates_a_damage_report_with_a_note(self):
        # AC-1/TC-01
        item = SerializedItemFactory(
            product_type=self.product_type, status=SerializedItem.STATUS_AVAILABLE
        )

        response = self.create(item.serial_number, note="cracked housing")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["reference"], "DR-0001")
        self.assertEqual(response.data["serial_number"], item.serial_number)
        self.assertEqual(response.data["product_type_name"], self.product_type.name)
        self.assertEqual(response.data["note"], "cracked housing")
        self.assertEqual(response.data["user_username"], "jane")

        item.refresh_from_db()
        self.assertEqual(item.status, SerializedItem.STATUS_DAMAGED)

        report = DamageReport.objects.get()
        self.assertEqual(report.serialized_item_id, item.id)
        self.assertEqual(report.note, "cracked housing")
        self.assertEqual(report.user_id, self.user.id)

        self.assertTrue(
            Transaction.objects.filter(
                serialized_item=item,
                transaction_type=Transaction.TYPE_DAMAGED,
                reference_number="DR-0001",
                note="cracked housing",
            ).exists()
        )

    def test_creates_a_damage_report_without_a_note(self):
        # AC-3/TC-02
        item = SerializedItemFactory(
            product_type=self.product_type, status=SerializedItem.STATUS_AVAILABLE
        )

        response = self.create(item.serial_number)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["note"], "")
        report = DamageReport.objects.get()
        self.assertEqual(report.note, "")

    def test_reference_numbering_is_sequential(self):
        # TC-04
        items = [
            SerializedItemFactory(
                product_type=self.product_type, status=SerializedItem.STATUS_AVAILABLE
            )
            for _ in range(3)
        ]

        references = [
            self.create(item.serial_number).data["reference"] for item in items
        ]

        self.assertEqual(references, ["DR-0001", "DR-0002", "DR-0003"])

    def test_rejects_an_unknown_serial_number(self):
        response = self.create("SN-does-not-exist")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(DamageReport.objects.count(), 0)

    def test_rejects_an_item_that_is_not_available(self):
        item = SerializedItemFactory(
            product_type=self.product_type, status=SerializedItem.STATUS_DAMAGED
        )

        response = self.create(item.serial_number)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(DamageReport.objects.count(), 0)

    def test_requires_authentication(self):
        item = SerializedItemFactory(
            product_type=self.product_type, status=SerializedItem.STATUS_AVAILABLE
        )
        self.client.force_authenticate(user=None)

        response = self.create(item.serial_number)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class DamageReportListTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="jane", email="jane@example.com", password="pw"
        )
        self.client.force_authenticate(user=self.user)
        self.product_type = ProductTypeFactory()

    def test_list_shows_damage_report_fields(self):
        # AC-2/TC-03
        item = SerializedItemFactory(
            product_type=self.product_type, status=SerializedItem.STATUS_AVAILABLE
        )
        create_response = self.client.post(
            reverse("damagereport-list"),
            {"serial_number": item.serial_number, "note": "cracked housing"},
        )
        assert create_response.status_code == status.HTTP_201_CREATED

        response = self.client.get(reverse("damagereport-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        entry = response.data[0]
        self.assertEqual(entry["reference"], "DR-0001")
        self.assertEqual(entry["serial_number"], item.serial_number)
        self.assertEqual(entry["product_type_name"], self.product_type.name)
        self.assertEqual(entry["note"], "cracked housing")
        self.assertEqual(entry["user_username"], "jane")
        self.assertIsNotNone(entry["created_at"])

    def test_list_requires_authentication(self):
        self.client.force_authenticate(user=None)

        response = self.client.get(reverse("damagereport-list"))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
