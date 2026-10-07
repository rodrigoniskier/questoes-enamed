"""Regression tests for safe CSRF renewal and JSON error contracts."""

import json

from django.test import Client, SimpleTestCase
from django.urls import reverse


class AiCsrfContractTests(SimpleTestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.api_url = reverse("api_gerar_questao")
        self.csrf_url = reverse("api_csrf")

    def post(self, token=None):
        kwargs = {"HTTP_X_CSRFTOKEN": token} if token else {}
        return self.client.post(
            self.api_url, json.dumps({}), content_type="application/json", **kwargs
        )

    def test_missing_token_is_json_403(self):
        response = self.post()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(response.json()["codigo"], "csrf_invalido")

    def test_token_endpoint_sets_cookie_and_allows_subsequent_post(self):
        refresh = self.client.get(self.csrf_url)
        self.assertEqual(refresh.status_code, 200)
        self.assertIn("no-store", refresh["Cache-Control"])
        self.assertIn("csrftoken", self.client.cookies)
        token = refresh.json()["csrfToken"]
        response = self.post(token)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response["Content-Type"], "application/json")

    def test_stale_token_can_be_renewed(self):
        first = self.client.get(self.csrf_url).json()["csrfToken"]
        self.client.cookies["csrftoken"] = "c" * 32
        self.assertEqual(self.post(first).status_code, 403)
        refreshed = self.client.get(self.csrf_url).json()["csrfToken"]
        self.assertEqual(self.post(refreshed).status_code, 400)

    def test_get_and_wrong_content_type_have_json_errors(self):
        response = self.client.get(self.api_url)
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.json()["codigo"], "metodo_invalido")
        self.assertEqual(response["Allow"], "POST")
        token = self.client.get(self.csrf_url).json()["csrfToken"]
        response = self.client.post(
            self.api_url, "{}", content_type="text/plain", HTTP_X_CSRFTOKEN=token
        )
        self.assertEqual(response.status_code, 415)
        self.assertEqual(response.json()["codigo"], "tipo_invalido")

    def test_html_pages_still_use_default_csrf_failure(self):
        response = self.client.post(reverse("admin:login"), {"username": "x"})
        self.assertEqual(response.status_code, 403)
        self.assertIn("text/html", response["Content-Type"])

