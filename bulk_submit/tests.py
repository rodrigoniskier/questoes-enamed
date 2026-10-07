import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import OperationalError
from django.test import TestCase
from django.urls import reverse

from questoes.models import Alternativa, ComponenteCurricular, Periodo, Questao


class BulkImportTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user("staff", is_staff=True))
        period = Periodo.objects.create(nome="3º Período")
        self.component = ComponenteCurricular.objects.create(nome="Clínica", periodo=period)

    def payload(self):
        item = {
            "enunciado": "Questão sintética",
            "justificativa": "Justificativa sintética",
            "alternativas": [
                {"texto": "Alternativa A", "correta": True},
                {"texto": "Alternativa B", "correta": False},
            ],
        }
        return {
            "professor_nome": "Professor",
            "professor_email": "example@example.com",
            "componente": str(self.component.pk),
            "periodo": str(self.component.periodo_id),
            "arquivo_backup": SimpleUploadedFile(
                "backup.json", json.dumps([item]).encode(), content_type="application/json"
            ),
        }

    def test_backup_import_has_atomic_rollback_on_write_failure(self):
        with patch(
            "bulk_submit.views.Alternativa.objects.create", side_effect=OperationalError("private detail")
        ):
            response = self.client.post(reverse("bulk_submit_form"), self.payload())
        self.assertEqual(Questao.objects.count(), 0)
        self.assertEqual(Alternativa.objects.count(), 0)
        self.assertContains(response, "Nenhuma questão deste lote foi salva")
        self.assertNotContains(response, "private detail")

    def test_invalid_json_has_no_writes(self):
        data = self.payload()
        data["arquivo_backup"] = SimpleUploadedFile("backup.json", b"{invalid")
        response = self.client.post(reverse("bulk_submit_form"), data)
        self.assertContains(response, "não é um JSON válido")
        self.assertEqual(Questao.objects.count(), 0)
