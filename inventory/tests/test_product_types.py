from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from inventory.tests.factories import ProductTypeFactory


class ProductTypeTests(APITestCase):
    def setUp(self):
        self.password = "correct-horse-battery-staple"
        self.user = User.objects.create_user(
            username="jane", email="jane@example.com", password=self.password
        )
        self.client.force_authenticate(user=self.user)

    def test_create_product_type_with_all_fields(self):
        # TC-01: create with name, model code, and description
        response = self.client.post(
            reverse("producttype-list"),
            {
                "name": "Bar LED Model A",
                "model_code": "BAR-LED-A",
                "description": "Moving bar light",
            },
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["name"], "Bar LED Model A")
        self.assertEqual(response.data["model_code"], "BAR-LED-A")
        self.assertEqual(response.data["description"], "Moving bar light")

    def test_create_product_type_with_name_only(self):
        # TC-02/AC-3: model code and description are optional
        response = self.client.post(
            reverse("producttype-list"), {"name": "Bar LED Model A"}
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["model_code"], "")
        self.assertEqual(response.data["description"], "")

    def test_create_product_type_succeeds_via_real_csrf_flow(self):
        # force_authenticate (used by every other test here) bypasses CSRF
        # entirely, so it can't prove a real browser session - which must
        # send X-CSRFToken - can actually create a product type.
        client = APIClient(enforce_csrf_checks=True)
        login_response = client.post(
            reverse("login"),
            {"email": self.user.email, "password": self.password},
        )
        self.assertEqual(login_response.status_code, status.HTTP_200_OK)
        csrf_token = client.cookies["csrftoken"].value

        response = client.post(
            reverse("producttype-list"),
            {"name": "Bar LED Model A"},
            HTTP_X_CSRFTOKEN=csrf_token,
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_product_type_is_rejected_without_csrf_token(self):
        # A real session without the X-CSRFToken header must still be
        # rejected - proves CSRF enforcement is actually active on this
        # endpoint, not just that it succeeds when the token is sent.
        client = APIClient(enforce_csrf_checks=True)
        login_response = client.post(
            reverse("login"),
            {"email": self.user.email, "password": self.password},
        )
        self.assertEqual(login_response.status_code, status.HTTP_200_OK)

        response = client.post(reverse("producttype-list"), {"name": "Bar LED Model A"})

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_detail_route_is_not_registered(self):
        # ProductTypeViewSet only mixes in list+create, so no retrieve/
        # update/destroy route exists at all for /product-types/<pk>/ -
        # WRH-20 only scopes create/list/search; that surface is a
        # separate story (PRD US-002b) and must not be reachable yet.
        product_type = ProductTypeFactory()
        detail_url = f"/api/product-types/{product_type.pk}/"

        self.assertEqual(
            self.client.get(detail_url).status_code, status.HTTP_404_NOT_FOUND
        )
        self.assertEqual(
            self.client.put(detail_url, {"name": "New Name"}).status_code,
            status.HTTP_404_NOT_FOUND,
        )
        self.assertEqual(
            self.client.patch(detail_url, {"name": "New Name"}).status_code,
            status.HTTP_404_NOT_FOUND,
        )
        self.assertEqual(
            self.client.delete(detail_url).status_code, status.HTTP_404_NOT_FOUND
        )

    def test_create_product_type_without_name_is_rejected(self):
        response = self.client.post(reverse("producttype-list"), {})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("name", response.data)

    def test_created_product_type_appears_in_list(self):
        # AC-1: new product type appears in the product type list
        response = self.client.post(
            reverse("producttype-list"), {"name": "Bar LED Model A"}
        )
        created_id = response.data["id"]

        list_response = self.client.get(reverse("producttype-list"))

        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertIn(created_id, [item["id"] for item in list_response.data])

    def test_search_matches_by_name(self):
        # TC-03: search filters to product types whose name matches
        ProductTypeFactory(name="Bar LED Model A")
        ProductTypeFactory(name="Fog Machine")

        response = self.client.get(reverse("producttype-list"), {"search": "Bar LED"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([item["name"] for item in response.data], ["Bar LED Model A"])

    def test_search_matches_by_model_code(self):
        # AC-2: search also matches on model code
        ProductTypeFactory(name="Bar LED Model A", model_code="BAR-LED-A")
        ProductTypeFactory(name="Fog Machine", model_code="FOG-01")

        response = self.client.get(reverse("producttype-list"), {"search": "FOG-01"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([item["name"] for item in response.data], ["Fog Machine"])

    def test_search_with_no_matches_returns_empty_list(self):
        # TC-04: no matches -> empty list, not an error
        ProductTypeFactory(name="Bar LED Model A")

        response = self.client.get(
            reverse("producttype-list"), {"search": "nonexistent"}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [])
