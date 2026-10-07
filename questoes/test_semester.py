import json
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from bulk_submit.forms import BulkSubmitForm
from questoes.models import Alternativa, ComponenteCurricular, Periodo, Questao, Semestre
from questoes.submission import alternative_count


class SemesterCatalogTests(TestCase):
    def setUp(self):
        self.old = Semestre.objects.create(nome="2026.1")
        self.period = Periodo.objects.create(nome="2º Período (turmas A e B)", semestre=self.old)
        self.component = ComponenteCurricular.objects.create(nome="Histórico", periodo=self.period)
        self.question = Questao.objects.create(
            componente=self.component,
            professor_nome="Professor de teste",
            professor_email="professor@example.com",
            enunciado="Questão histórica",
            justificativa="Justificativa histórica",
        )
        self.answer = Alternativa.objects.create(questao=self.question, texto="Histórico", eh_correta=True)

    def configure(self, **options):
        call_command("configurar_semestre_2026_2", stdout=StringIO(), **options)

    def test_catalog_is_idempotent_and_preserves_history(self):
        before_question = Questao.objects.values().get(pk=self.question.pk)
        before_answer = Alternativa.objects.values().get(pk=self.answer.pk)
        self.configure()
        ids = list(
            ComponenteCurricular.objects.filter(periodo__semestre__nome="2026.2").values_list("pk", flat=True)
        )
        self.configure()
        self.old.refresh_from_db()
        self.assertFalse(self.old.ativo)
        current = Semestre.objects.get(nome="2026.2")
        self.assertTrue(current.ativo)
        self.assertEqual(current.periodos.filter(ativo=True).count(), 5)
        self.assertEqual(len(ids), 50)
        self.assertEqual(
            ids,
            list(ComponenteCurricular.objects.filter(periodo__semestre=current).values_list("pk", flat=True)),
        )
        self.assertEqual(current.periodos.get(nome="4º Período").componentes.count(), 8)
        self.assertEqual(Questao.objects.values().get(pk=self.question.pk), before_question)
        self.assertEqual(Alternativa.objects.values().get(pk=self.answer.pk), before_answer)
        self.assertTrue(Periodo.objects.filter(pk=self.period.pk, semestre=self.old).exists())
        self.assertTrue(ComponenteCurricular.objects.filter(pk=self.component.pk).exists())
        period = current.periodos.get(nome="6º Período")
        shared = period.componentes.get(
            nome="Atenção Primária em Saúde na Comunidade VI (turmas A, B, C e D)"
        )
        self.assertEqual(shared.numero_questoes_prova, 6)
        self.assertEqual(period.componentes.count(), 13)

    def test_dry_run_does_not_archive_or_create_records(self):
        self.configure(dry_run=True)
        self.old.refresh_from_db()
        self.assertTrue(self.old.ativo)
        self.assertFalse(Semestre.objects.filter(nome="2026.2").exists())
        self.assertEqual(Periodo.objects.count(), 1)

    def test_unexpected_current_catalog_aborts_atomically(self):
        current = Semestre.objects.create(nome="2026.2", ativo=False)
        Periodo.objects.create(nome="Fora do edital", semestre=current)
        with self.assertRaises(CommandError):
            self.configure()
        self.old.refresh_from_db()
        current.refresh_from_db()
        self.assertTrue(self.old.ativo)
        self.assertFalse(current.ativo)
        self.assertEqual(current.periodos.count(), 1)

    def test_names_are_unique_within_each_semester(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Periodo.objects.create(nome=self.period.nome, semestre=self.old)

    def test_failure_during_catalog_write_rolls_back_all_new_records(self):
        update = ComponenteCurricular.objects.update_or_create
        calls = 0

        def fail_during_write(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise RuntimeError("Falha simulada")
            return update(*args, **kwargs)

        with patch(
            "questoes.management.commands.configurar_semestre_2026_2.ComponenteCurricular.objects.update_or_create",
            side_effect=fail_during_write,
        ):
            with self.assertRaises(RuntimeError):
                self.configure()
        self.old.refresh_from_db()
        self.assertTrue(self.old.ativo)
        self.assertFalse(Semestre.objects.filter(nome="2026.2").exists())
        self.assertEqual(ComponenteCurricular.objects.count(), 1)

    def test_current_submission_accepts_four_and_rejects_five_alternatives(self):
        self.configure()
        current = ComponenteCurricular.objects.filter(periodo__semestre__nome="2026.2").first()
        url = reverse("submeter_resposta_unica") + f"?componente={current.pk}"
        token = self.client.get(url).context["submission_token"]
        payload = {
            "professor_nome": "Professor de teste",
            "professor_email": "professor@example.com",
            "componente": current.pk,
            "tipo_questao": "RESPOSTA_UNICA",
            "uso_prova": "REPOSICAO",
            "texto_base": "Contexto sintético",
            "enunciado": "Questão sintética",
            "justificativa": "Justificativa sintética",
            "submission_token": token,
            "submit_action": "finish",
            "alternativas-TOTAL_FORMS": "5",
            "alternativas-INITIAL_FORMS": "0",
            "alternativas-0-eh_correta": "on",
        }
        for index in range(5):
            payload[f"alternativas-{index}-texto"] = f"Alternativa {index}"
        self.assertEqual(self.client.post(url, payload).status_code, 200)
        self.assertFalse(Questao.objects.filter(componente=current).exists())
        payload["alternativas-TOTAL_FORMS"] = "4"
        del payload["alternativas-4-texto"]
        self.assertRedirects(self.client.post(url, payload), reverse("submeter_selecao"))
        question = Questao.objects.get(componente=current)
        self.assertEqual(question.alternativas.count(), 4)
        self.assertEqual(question.uso_prova, "REPOSICAO")

    @patch("questoes.views.gerar_questao_com_ia")
    def test_archived_components_cannot_receive_submissions_or_ai_calls(self, generate):
        self.configure()
        url = reverse("submeter_resposta_unica") + f"?componente={self.component.pk}"
        for response in [self.client.get(url), self.client.post(url, {"enunciado": "Novo"})]:
            self.assertRedirects(response, reverse("submeter_selecao"))
        response = self.client.get(reverse("api_get_componentes"), {"periodo_id": self.period.pk})
        self.assertEqual(response.json(), [])
        payload = {
            "componente_id": self.component.pk,
            "tipo_questao": "RESPOSTA_UNICA",
            "num_alternativas": 5,
            "prompt_professor": "Tema",
        }
        self.assertEqual(
            self.client.post(
                reverse("api_gerar_questao"), json.dumps(payload), content_type="application/json"
            ).status_code,
            400,
        )
        generate.assert_not_called()
        form = BulkSubmitForm({"periodo": self.period.pk})
        self.assertFalse(form.fields["periodo"].queryset.filter(pk=self.period.pk).exists())
        self.assertFalse(form.fields["componente"].queryset.exists())
        self.assertEqual(Questao.objects.count(), 1)

    def test_current_semester_has_four_alternatives_and_correct_selection(self):
        self.configure()
        current = ComponenteCurricular.objects.filter(periodo__semestre__nome="2026.2").first()
        self.assertEqual(alternative_count(current), 4)
        self.assertEqual(alternative_count(self.component), 5)
        response = self.client.get(reverse("submeter_resposta_unica"), {"componente": current.pk})
        self.assertEqual(response.context["alternativa_formset"].total_form_count(), 4)
        selection = self.client.get(reverse("submeter_selecao"))
        self.assertEqual(len(selection.context["periodos"]), 5)
        self.assertNotIn(self.period, selection.context["periodos"])
        self.assertContains(selection, "QUESTÕES MEDICINA")
        self.assertContains(selection, "Desenvolvido por Prof. Rodrigo Niskier")
