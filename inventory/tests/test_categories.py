from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from inventory.tests.factories import CategoryFactory


class CategoryTests(APITestCase):
    def setUp(self):
        self.password = "correct-horse-battery-staple"
        self.user = User.objects.create_user(
            username="jane", email="jane@example.com", password=self.password
        )
        self.client.force_authenticate(user=self.user)

    def test_create_category_with_all_fields(self):
        # TC-01: create with name and description
        response = self.client.post(
            reverse("category-list"),
            {"name": "Lighting", "description": "Moving lights and fixtures"},
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["name"], "Lighting")
        self.assertEqual(response.data["description"], "Moving lights and fixtures")

    def test_create_category_with_name_only(self):
        # TC-02/AC-3: description is optional
        response = self.client.post(reverse("category-list"), {"name": "Lighting"})

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["description"], "")

    def test_create_category_succeeds_via_real_csrf_flow(self):
        # force_authenticate (used by every other test here) bypasses CSRF
        # entirely, so it can't prove a real browser session - which must
        # send X-CSRFToken - can actually create a category.
        client = APIClient(enforce_csrf_checks=True)
        login_response = client.post(
            reverse("login"),
            {"email": self.user.email, "password": self.password},
        )
        self.assertEqual(login_response.status_code, status.HTTP_200_OK)
        csrf_token = client.cookies["csrftoken"].value

        response = client.post(
            reverse("category-list"),
            {"name": "Lighting"},
            HTTP_X_CSRFTOKEN=csrf_token,
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_category_is_rejected_without_csrf_token(self):
        # A real session without the X-CSRFToken header must still be
        # rejected - proves CSRF enforcement is actually active on this
        # endpoint, not just that it succeeds when the token is sent.
        client = APIClient(enforce_csrf_checks=True)
        login_response = client.post(
            reverse("login"),
            {"email": self.user.email, "password": self.password},
        )
        self.assertEqual(login_response.status_code, status.HTTP_200_OK)

        response = client.post(reverse("category-list"), {"name": "Lighting"})

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_detail_route_is_not_registered(self):
        # CategoryViewSet only mixes in list+create, so no retrieve/update/
        # destroy route exists at all for /categories/<pk>/ - WRH-61 only
        # scopes create/list/search; that surface is a separate story (PRD
        # US-026b / WRH-62) and must not be reachable yet.
        category = CategoryFactory()
        detail_url = f"/api/categories/{category.pk}/"

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

    def test_create_category_without_name_is_rejected(self):
        response = self.client.post(reverse("category-list"), {})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("name", response.data)

    def test_created_category_appears_in_list(self):
        # AC-1: new category appears in the category list
        response = self.client.post(reverse("category-list"), {"name": "Lighting"})
        created_id = response.data["id"]

        list_response = self.client.get(reverse("category-list"))

        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertIn(created_id, [item["id"] for item in list_response.data])

    def test_search_matches_by_name(self):
        # TC-03: search filters to categories whose name matches
        CategoryFactory(name="Lighting")
        CategoryFactory(name="Staging")

        response = self.client.get(reverse("category-list"), {"search": "Light"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([item["name"] for item in response.data], ["Lighting"])

    def test_search_with_no_matches_returns_empty_list(self):
        # TC-04: no matches -> empty list, not an error
        CategoryFactory(name="Lighting")

        response = self.client.get(reverse("category-list"), {"search": "nonexistent"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [])
