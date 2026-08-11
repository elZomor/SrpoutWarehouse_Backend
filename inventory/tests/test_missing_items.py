from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from inventory.models import SerializedItem, Transaction, WorkOrder
from inventory.tests.factories import (
    ProductTypeFactory,
    SerializedItemFactory,
    WorkOrderFactory,
    WorkOrderLineItemFactory,
)


class MissingItemTestsBase(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="jane", email="jane@example.com", password="pw"
        )
        self.client.force_authenticate(user=self.user)
        self.product_type = ProductTypeFactory()
        self.work_order = WorkOrderFactory(status=WorkOrder.STATUS_FULFILLED)
        self.line_item = WorkOrderLineItemFactory(
            work_order=self.work_order,
            product_type=self.product_type,
            quantity=1,
        )

    def _close_work_order_with_missing_item(self, work_order=None, line_item=None):
        # Matches WorkOrderCloseTests' own setup shape (WRH-40) - a missing
        # item only ever comes into existence through close(), so building
        # one for these tests reuses that real endpoint rather than
        # hand-crafting STATUS_MISSING (which would let the fixture drift
        # from what close() actually produces, e.g. the TYPE_MISSING
        # Transaction row list() depends on for date_missing).
        work_order = work_order or self.work_order
        line_item = line_item or self.line_item
        item = SerializedItemFactory(
            product_type=self.product_type,
            work_order_line_item=line_item,
            status=SerializedItem.STATUS_OUT,
        )
        response = self.client.post(reverse("workorder-close", args=[work_order.id]))
        assert response.status_code == status.HTTP_200_OK
        item.refresh_from_db()
        assert item.status == SerializedItem.STATUS_MISSING
        return item


class MissingItemListTests(MissingItemTestsBase):
    def test_list_shows_missing_item_fields(self):
        # AC-1/TC-01/TC-04
        item = self._close_work_order_with_missing_item()

        response = self.client.get(reverse("missing-item-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        entry = response.data[0]
        self.assertEqual(entry["serial_number"], item.serial_number)
        self.assertEqual(entry["product_type_name"], self.product_type.name)
        self.assertEqual(entry["work_order_id"], self.work_order.id)
        self.assertEqual(entry["work_order_reference"], f"WO-{self.work_order.id}")
        self.assertEqual(entry["status"], SerializedItem.STATUS_MISSING)
        self.assertIsNotNone(entry["date_missing"])

    def test_list_combines_multiple_closed_work_orders(self):
        # TC-05
        second_work_order = WorkOrderFactory(status=WorkOrder.STATUS_FULFILLED)
        second_line_item = WorkOrderLineItemFactory(
            work_order=second_work_order,
            product_type=self.product_type,
            quantity=1,
        )
        self._close_work_order_with_missing_item()
        self._close_work_order_with_missing_item(
            work_order=second_work_order, line_item=second_line_item
        )

        response = self.client.get(reverse("missing-item-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)

    def test_list_excludes_available_items(self):
        SerializedItemFactory(
            product_type=self.product_type, status=SerializedItem.STATUS_AVAILABLE
        )

        response = self.client.get(reverse("missing-item-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 0)

    def test_list_requires_authentication(self):
        self.client.force_authenticate(user=None)

        response = self.client.get(reverse("missing-item-list"))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class MissingItemMarkFoundTests(MissingItemTestsBase):
    def test_mark_found_creates_return_transaction_and_frees_stock(self):
        # AC-3/TC-02
        item = self._close_work_order_with_missing_item()

        response = self.client.post(reverse("missing-item-mark-found", args=[item.id]))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], SerializedItem.STATUS_AVAILABLE)
        item.refresh_from_db()
        self.assertEqual(item.status, SerializedItem.STATUS_AVAILABLE)
        self.assertTrue(
            Transaction.objects.filter(
                serialized_item=item,
                transaction_type=Transaction.TYPE_RETURN,
                work_order=self.work_order,
            ).exists()
        )

    def test_mark_found_removes_item_from_active_list(self):
        item = self._close_work_order_with_missing_item()
        self.client.post(reverse("missing-item-mark-found", args=[item.id]))

        response = self.client.get(reverse("missing-item-list"))

        self.assertEqual(len(response.data), 0)

    def test_mark_found_rejects_an_item_that_is_not_missing(self):
        item = SerializedItemFactory(
            product_type=self.product_type, status=SerializedItem.STATUS_AVAILABLE
        )

        response = self.client.post(reverse("missing-item-mark-found", args=[item.id]))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_mark_found_returns_404_for_unknown_item(self):
        response = self.client.post(reverse("missing-item-mark-found", args=[999999]))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_mark_found_requires_authentication(self):
        item = self._close_work_order_with_missing_item()
        self.client.force_authenticate(user=None)

        response = self.client.post(reverse("missing-item-mark-found", args=[item.id]))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class MissingItemWriteOffTests(MissingItemTestsBase):
    def test_write_off_marks_item_written_off_and_logs_transaction(self):
        # AC-4/TC-03
        item = self._close_work_order_with_missing_item()

        response = self.client.post(reverse("missing-item-write-off", args=[item.id]))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], SerializedItem.STATUS_WRITTEN_OFF)
        item.refresh_from_db()
        self.assertEqual(item.status, SerializedItem.STATUS_WRITTEN_OFF)
        self.assertTrue(
            Transaction.objects.filter(
                serialized_item=item,
                transaction_type=Transaction.TYPE_WRITTEN_OFF,
                work_order=self.work_order,
            ).exists()
        )

    def test_write_off_removes_item_from_active_list(self):
        item = self._close_work_order_with_missing_item()
        self.client.post(reverse("missing-item-write-off", args=[item.id]))

        response = self.client.get(reverse("missing-item-list"))

        self.assertEqual(len(response.data), 0)

    def test_write_off_rejects_an_item_that_is_not_missing(self):
        item = SerializedItemFactory(
            product_type=self.product_type, status=SerializedItem.STATUS_AVAILABLE
        )

        response = self.client.post(reverse("missing-item-write-off", args=[item.id]))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_write_off_requires_authentication(self):
        item = self._close_work_order_with_missing_item()
        self.client.force_authenticate(user=None)

        response = self.client.post(reverse("missing-item-write-off", args=[item.id]))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
