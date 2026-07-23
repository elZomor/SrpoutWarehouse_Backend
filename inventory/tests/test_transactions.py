from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from inventory.models import Transaction
from inventory.tests.factories import (
    SerializedItemFactory,
    TransactionFactory,
    WorkOrderFactory,
)


def _backdate(transaction_obj, date_str):
    # auto_now_add ignores any created_at passed to create()/save() - the
    # only way to set a specific value is a separate update() after the row
    # exists, matching this repo's existing convention for backdating an
    # auto_now_add field in tests (see WorkOrder-adjacent factories/tests
    # for the equivalent pattern on other auto_now_add fields).
    Transaction.objects.filter(pk=transaction_obj.pk).update(created_at=date_str)


class TransactionTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="jane", email="jane@example.com", password="pw"
        )
        self.client.force_authenticate(user=self.user)

    def test_full_log_lists_every_transaction_type(self):
        # TC-01/AC-1: every transaction is listed with type, reference
        # number, serial, product type, date, user, note.
        types = [choice for choice, _ in Transaction.TYPE_CHOICES]
        for transaction_type in types:
            TransactionFactory(
                transaction_type=transaction_type,
                reference_number=f"REF-{transaction_type}",
                note=f"note for {transaction_type}",
                user=self.user,
            )

        response = self.client.get(reverse("transaction-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), len(types))
        returned_types = {row["transaction_type"] for row in response.data}
        self.assertEqual(returned_types, set(types))
        first = response.data[0]
        for field in (
            "transaction_type",
            "reference_number",
            "serial_number",
            "product_type_name",
            "created_at",
            "user_username",
            "note",
        ):
            self.assertIn(field, first)

    def test_filter_by_serial_number_returns_only_that_items_transactions(self):
        # TC-02/AC-2: only the filtered serial's transactions come back, in
        # chronological order.
        target_item = SerializedItemFactory(serial_number="SN-042")
        other_item = SerializedItemFactory(serial_number="SN-099")
        first = TransactionFactory(
            transaction_type=Transaction.TYPE_RECEIVE,
            serialized_item=target_item,
            user=self.user,
        )
        second = TransactionFactory(
            transaction_type=Transaction.TYPE_ISSUE,
            serialized_item=target_item,
            user=self.user,
        )
        TransactionFactory(
            transaction_type=Transaction.TYPE_RECEIVE,
            serialized_item=other_item,
            user=self.user,
        )
        _backdate(first, "2026-01-01T00:00:00Z")
        _backdate(second, "2026-01-02T00:00:00Z")

        response = self.client.get(
            reverse("transaction-list"), {"serial_number": "SN-042"}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)
        self.assertEqual([row["id"] for row in response.data], [first.id, second.id])

    def test_filter_by_work_order_reference_returns_only_that_wos_transactions(self):
        # TC-03/AC-3.
        wo_a = WorkOrderFactory()
        wo_b = WorkOrderFactory()
        matching = TransactionFactory(
            transaction_type=Transaction.TYPE_ISSUE,
            work_order=wo_a,
            reference_number=f"WO-{wo_a.id}",
            user=self.user,
        )
        TransactionFactory(
            transaction_type=Transaction.TYPE_ISSUE,
            work_order=wo_b,
            reference_number=f"WO-{wo_b.id}",
            user=self.user,
        )

        response = self.client.get(
            reverse("transaction-list"), {"reference_number": f"WO-{wo_a.id}"}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([row["id"] for row in response.data], [matching.id])

    def test_filter_by_date_range_returns_only_transactions_in_range(self):
        # TC-04/AC-4.
        in_range = TransactionFactory(user=self.user)
        before_range = TransactionFactory(user=self.user)
        after_range = TransactionFactory(user=self.user)
        _backdate(in_range, "2026-02-15T00:00:00Z")
        _backdate(before_range, "2026-01-01T00:00:00Z")
        _backdate(after_range, "2026-03-01T00:00:00Z")

        response = self.client.get(
            reverse("transaction-list"),
            {"date_from": "2026-02-01", "date_to": "2026-02-28"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([row["id"] for row in response.data], [in_range.id])

    def test_filter_by_transaction_type_returns_only_that_type(self):
        # TC-05/AC-5.
        damaged = TransactionFactory(
            transaction_type=Transaction.TYPE_DAMAGED, user=self.user
        )
        TransactionFactory(transaction_type=Transaction.TYPE_RECEIVE, user=self.user)

        response = self.client.get(
            reverse("transaction-list"), {"transaction_type": "damaged"}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([row["id"] for row in response.data], [damaged.id])

    def test_combining_serial_and_date_range_filters(self):
        # TC-06/AC-6: only transactions matching ALL active filters.
        target_item = SerializedItemFactory(serial_number="SN-042")
        in_range_match = TransactionFactory(serialized_item=target_item, user=self.user)
        out_of_range_same_serial = TransactionFactory(
            serialized_item=target_item, user=self.user
        )
        in_range_other_serial = TransactionFactory(user=self.user)
        _backdate(in_range_match, "2026-02-15T00:00:00Z")
        _backdate(out_of_range_same_serial, "2026-03-15T00:00:00Z")
        _backdate(in_range_other_serial, "2026-02-15T00:00:00Z")

        response = self.client.get(
            reverse("transaction-list"),
            {
                "serial_number": "SN-042",
                "date_from": "2026-02-01",
                "date_to": "2026-02-28",
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([row["id"] for row in response.data], [in_range_match.id])

    def test_filters_with_no_matches_return_empty_list_not_error(self):
        # TC-07: boundary - empty state, no error.
        TransactionFactory(user=self.user)

        response = self.client.get(
            reverse("transaction-list"), {"serial_number": "SN-does-not-exist"}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [])
