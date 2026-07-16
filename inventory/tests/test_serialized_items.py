from unittest import mock

from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from inventory.models import ProductType, SerializedItem
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

    def test_retrieve_and_update_routes_are_not_registered(self):
        # WRH-22 only scopes register/list/filter/search, and WRH-66 only
        # adds destroy - retrieve/update remain separate, unscoped stories.
        # The detail route now exists (for DELETE), so GET/PUT return 405
        # (method not allowed) rather than 404 (no such route).
        item = SerializedItemFactory(product_type=self.product_type)
        detail_url = f"/api/serialized-items/{item.pk}/"

        self.assertEqual(
            self.client.get(detail_url).status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )
        self.assertEqual(
            self.client.put(detail_url).status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    def test_delete_serialized_item(self):
        # TC-01/AC-1: deleting a registered item permanently removes it -
        # it no longer appears in the list.
        item = SerializedItemFactory(
            serial_number="SN-042", product_type=self.product_type
        )
        detail_url = f"/api/serialized-items/{item.pk}/"

        response = self.client.delete(detail_url)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(SerializedItem.objects.filter(pk=item.pk).exists())
        list_response = self.client.get(reverse("serializeditem-list"))
        self.assertEqual([i["serial_number"] for i in list_response.data], [])

    def test_delete_one_item_does_not_affect_others(self):
        # TC-02/AC-2: deleting one item leaves other items, and their
        # parent product type, untouched.
        item_to_delete = SerializedItemFactory(
            serial_number="SN-042", product_type=self.product_type
        )
        other_item = SerializedItemFactory(
            serial_number="SN-043", product_type=self.product_type
        )

        response = self.client.delete(f"/api/serialized-items/{item_to_delete.pk}/")

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        other_item.refresh_from_db()
        self.assertEqual(other_item.serial_number, "SN-043")
        self.assertTrue(ProductType.objects.filter(pk=self.product_type.pk).exists())

    def test_delete_blocks_a_reserved_item(self):
        # WRH-54: a reserved item is mid-scan against a WorkOrder - deleting
        # it would silently corrupt that WO's live scanned_quantity count.
        item = SerializedItemFactory(
            serial_number="SN-042",
            product_type=self.product_type,
            status=SerializedItem.STATUS_RESERVED,
        )

        response = self.client.delete(f"/api/serialized-items/{item.pk}/")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(SerializedItem.objects.filter(pk=item.pk).exists())

    def test_delete_blocks_an_out_item(self):
        # WRH-54: an "out" item is part of a fulfilled WO's audit trail
        # (last_work_order_reference) - deleting it would erase that record.
        item = SerializedItemFactory(
            serial_number="SN-042",
            product_type=self.product_type,
            status=SerializedItem.STATUS_OUT,
        )

        response = self.client.delete(f"/api/serialized-items/{item.pk}/")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(SerializedItem.objects.filter(pk=item.pk).exists())

    def test_delete_rechecks_status_after_the_lock_is_acquired(self):
        # WRH-54: simulates a concurrent WorkOrderViewSet.scan() claiming
        # this item in the window between get_object()'s unlocked fetch and
        # destroy()'s select_for_update() - matches the same
        # race-simulation technique as
        # PurchaseOrderReceiveTests.test_receive_rechecks_archived_status_after_lock_is_acquired.
        item = SerializedItemFactory(
            serial_number="SN-042", product_type=self.product_type
        )
        original_select_for_update = SerializedItem.objects.select_for_update

        def reserve_then_lock(*args, **kwargs):
            item.status = SerializedItem.STATUS_RESERVED
            item.save(update_fields=["status"])
            return original_select_for_update(*args, **kwargs)

        with mock.patch.object(
            SerializedItem.objects, "select_for_update", reserve_then_lock
        ):
            response = self.client.delete(f"/api/serialized-items/{item.pk}/")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(SerializedItem.objects.filter(pk=item.pk).exists())

    def test_delete_unknown_item_404s(self):
        response = self.client.delete("/api/serialized-items/999999/")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_deleting_an_already_deleted_item_404s(self):
        # WRH-67 AC-2/TC-02: a second delete of the same id (stale list,
        # double-click, second tab) must 404 rather than erroring or
        # silently succeeding twice - DestroyModelMixin's get_object() no
        # longer finds the row after the first delete, so this is free
        # behavior; this test just pins it down as a regression check.
        item = SerializedItemFactory(
            serial_number="SN-042", product_type=self.product_type
        )
        detail_url = f"/api/serialized-items/{item.pk}/"

        first_response = self.client.delete(detail_url)
        second_response = self.client.delete(detail_url)

        self.assertEqual(first_response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(second_response.status_code, status.HTTP_404_NOT_FOUND)

    def test_deleted_items_serial_number_can_be_reregistered(self):
        # WRH-67 AC-3/TC-03: the delete-side counterpart of US-003b's
        # global-uniqueness invariant - a hard delete actually frees the
        # serial_number unique constraint, so registering a new item with
        # the same serial number the deleted one used must succeed rather
        # than being rejected as a duplicate.
        item = SerializedItemFactory(
            serial_number="SN-042", product_type=self.product_type
        )
        self.client.delete(f"/api/serialized-items/{item.pk}/")

        response = self.client.post(
            reverse("serializeditem-list"),
            {"serial_number": "SN-042", "product_type": self.product_type.id},
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["serial_number"], "SN-042")
        self.assertNotEqual(response.data["id"], item.pk)

    def test_second_clients_list_drops_item_after_first_client_deletes_it(self):
        # WRH-67 TC-04: two clients have the same item loaded; client A
        # deletes it, then client B's list refresh must no longer show it -
        # no stale reference, no error - rather than assuming this is
        # implied by the single-client list check in
        # test_delete_serialized_item above.
        item = SerializedItemFactory(
            serial_number="SN-042", product_type=self.product_type
        )
        client_b = APIClient()
        client_b.force_authenticate(user=self.user)

        delete_response = self.client.delete(f"/api/serialized-items/{item.pk}/")
        list_response = client_b.get(reverse("serializeditem-list"))

        self.assertEqual(delete_response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [i["serial_number"] for i in list_response.data],
            [],
        )

    def test_qr_pdf_for_single_product_type(self):
        # TC-05/AC-4: scoping the PDF to one product type only bundles that
        # type's items - a different product type's item must not leak in.
        # Patches HTML.write_pdf to capture the rendered HTML string
        # directly, rather than trusting the PDF's opaque bytes, so the
        # scoping itself (not just the response's status/content-type) is
        # actually verified.
        SerializedItemFactory(serial_number="SN-001", product_type=self.product_type)
        SerializedItemFactory(serial_number="SN-002", product_type=self.product_type)
        other_type = ProductTypeFactory()
        SerializedItemFactory(serial_number="SN-999", product_type=other_type)

        with mock.patch("inventory.views.serialized_item.HTML") as mock_html_class:
            mock_html_class.return_value.write_pdf.return_value = b"%PDF-fake"
            response = self.client.get(
                "/api/serialized-items/qr-pdf/", {"product_type": self.product_type.id}
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "application/pdf")
        rendered_html = mock_html_class.call_args.kwargs["string"]
        self.assertIn("SN-001", rendered_html)
        self.assertIn("SN-002", rendered_html)
        self.assertNotIn("SN-999", rendered_html)

    def test_qr_pdf_for_all_product_types(self):
        # TC-06/AC-5: no product_type filter ("All") bundles every
        # registered item across every product type into one PDF.
        SerializedItemFactory(serial_number="SN-001", product_type=self.product_type)
        other_type = ProductTypeFactory()
        SerializedItemFactory(serial_number="SN-999", product_type=other_type)

        with mock.patch("inventory.views.serialized_item.HTML") as mock_html_class:
            mock_html_class.return_value.write_pdf.return_value = b"%PDF-fake"
            response = self.client.get("/api/serialized-items/qr-pdf/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "application/pdf")
        rendered_html = mock_html_class.call_args.kwargs["string"]
        self.assertIn("SN-001", rendered_html)
        self.assertIn("SN-999", rendered_html)

    def test_qr_pdf_for_product_type_with_no_items_is_rejected(self):
        # AC-4: a product type with zero registered items has nothing to
        # bundle - reject rather than silently returning an empty PDF.
        empty_type = ProductTypeFactory()

        response = self.client.get(
            "/api/serialized-items/qr-pdf/", {"product_type": empty_type.id}
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["detail"],
            "No serialized items found for the selected product type.",
        )

    def test_qr_pdf_for_all_with_no_items_registered_is_rejected(self):
        # AC-5: the "All" branch has its own message - it must not claim a
        # "selected product type" when the user selected none.
        response = self.client.get("/api/serialized-items/qr-pdf/")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["detail"], "No serialized items are registered yet."
        )

    def test_qr_pdf_ignores_a_stray_search_param(self):
        # A stray ?search= must not silently narrow the label batch the way
        # it's intentionally allowed to on the list endpoint - qr_pdf only
        # honors product_type (via DjangoFilterBackend), not SearchFilter.
        SerializedItemFactory(serial_number="SN-001", product_type=self.product_type)

        with mock.patch("inventory.views.serialized_item.HTML") as mock_html_class:
            mock_html_class.return_value.write_pdf.return_value = b"%PDF-fake"
            response = self.client.get(
                "/api/serialized-items/qr-pdf/", {"search": "no-match"}
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rendered_html = mock_html_class.call_args.kwargs["string"]
        self.assertIn("SN-001", rendered_html)
