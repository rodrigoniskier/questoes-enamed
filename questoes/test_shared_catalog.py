import uuid
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from questoes.catalog import grade_compartilhada
from questoes.models import Alternativa, ComponenteCurricular, Periodo, Questao, Semestre, SubmissionReceipt
from questoes.submission import new_submission_token


class SharedCatalogTests(TestCase):
    def setUp(self):
        self.semester = Semestre.objects.create(nome="2026.2")
        grade, _ = grade_compartilhada()
        for item in grade["periodos"]:
            period = Periodo.objects.create(nome=item["nome"], semestre=self.semester)
            for component in item["componentes"]:
                ComponenteCurricular.objects.create(periodo=period, **component)
        self.ab = ComponenteCurricular.objects.get(
            periodo__nome="6º Período (turmas A e B)", nome="Atenção Primária em Saúde na Comunidade VI"
        )
        self.cd = ComponenteCurricular.objects.get(
            periodo__nome="6º Período (turmas C e D)", nome=self.ab.nome
        )
        self.ab_question = self.make_question(self.ab, "Questão A/B")
        self.cd_question = self.make_question(self.cd, "Questão C/D")
        self.receipt = SubmissionReceipt.objects.create(token=uuid.uuid4(), questao=self.cd_question)
        self.staff = get_user_model().objects.create_user("montador", is_staff=True)

    def make_question(self, component, text):
        question = Questao.objects.create(
            componente=component,
            professor_nome="Teste",
            professor_email="professor@example.com",
            texto_base="Contexto",
            enunciado=text,
            justificativa="Justificativa",
            status="APROVADA",
            uso_prova="INTEGRADA",
            imagem="imagens_questoes/imagem_historica.jpg",
        )
        for index in range(4):
            Alternativa.objects.create(questao=question, texto=f"Alternativa {index}", eh_correta=index == 0)
        return question

    def consolidate(self, **options):
        call_command("configurar_semestre_2026_2", stdout=StringIO(), **options)

    def test_existing_banks_are_combined_without_deleting_records(self):
        questions = list(Questao.objects.values())
        alternatives = list(Alternativa.objects.values())
        component_ids = set(ComponenteCurricular.objects.values_list("pk", flat=True))
        self.consolidate()
        self.consolidate()
        self.ab.refresh_from_db()
        self.cd.refresh_from_db()
        self.assertEqual(self.ab.nome, "Atenção Primária em Saúde na Comunidade VI (turmas A, B, C e D)")
        self.assertEqual(self.ab.numero_questoes_prova, 6)
        self.assertEqual(self.cd.consolidado_em_id, self.ab.pk)
        self.assertEqual(Questao.objects.filter(componente=self.ab).count(), 2)
        self.assertEqual(component_ids, set(ComponenteCurricular.objects.values_list("pk", flat=True)))
        for old in questions:
            current = Questao.objects.values().get(pk=old["id"])
            self.assertEqual(current.pop("componente_id"), self.ab.pk)
            old.pop("componente_id")
            self.assertEqual(current, old)
        self.assertEqual(list(Alternativa.objects.values()), alternatives)
        self.receipt.refresh_from_db()
        self.assertEqual(self.receipt.questao_id, self.cd_question.pk)
        self.assertEqual(Periodo.objects.filter(semestre=self.semester, ativo=True).count(), 5)
        self.assertEqual(Periodo.objects.filter(semestre=self.semester, ativo=False).count(), 9)
        self.assertEqual(ComponenteCurricular.objects.filter(consolidado_em__isnull=True).count(), 50)

    def test_dry_run_keeps_original_groups_and_question_links(self):
        self.consolidate(dry_run=True)
        self.assertEqual(Periodo.objects.filter(ativo=True).count(), 9)
        self.assertEqual(ComponenteCurricular.objects.filter(consolidado_em__isnull=True).count(), 62)
        self.cd_question.refresh_from_db()
        self.assertEqual(self.cd_question.componente_id, self.cd.pk)

    def test_failure_during_question_move_rolls_back_catalog_and_links(self):
        from django.db.models.query import QuerySet

        update = QuerySet.update

        def fail_question_move(queryset, **kwargs):
            if queryset.model is Questao:
                raise RuntimeError("Falha simulada")
            return update(queryset, **kwargs)

        with patch("django.db.models.query.QuerySet.update", fail_question_move):
            with self.assertRaises(RuntimeError):
                self.consolidate()
        self.assertEqual(Periodo.objects.filter(ativo=True).count(), 9)
        self.cd.refresh_from_db()
        self.cd_question.refresh_from_db()
        self.assertIsNone(self.cd.consolidado_em_id)
        self.assertEqual(self.cd_question.componente_id, self.cd.pk)

    def test_old_links_share_history_and_a_form_opened_before_merge_still_submits(self):
        token = new_submission_token(self.cd, "RESPOSTA_UNICA")
        self.consolidate()
        history = self.client.get(reverse("api_get_historico"), {"componente_id": self.cd.pk}).json()
        self.assertEqual(history["contagem"]["enviado"], 2)
        self.assertEqual(history["contagem"]["necessario"], 6)
        url = reverse("submeter_resposta_unica") + f"?componente={self.cd.pk}"
        self.assertEqual(self.client.get(url).context["componente"].pk, self.ab.pk)
        payload = {
            "professor_nome": "Teste",
            "professor_email": "professor@example.com",
            "componente": self.cd.pk,
            "tipo_questao": "RESPOSTA_UNICA",
            "uso_prova": "REPOSICAO",
            "texto_base": "Contexto",
            "enunciado": "Nova questão",
            "justificativa": "Justificativa",
            "submission_token": token,
            "submit_action": "finish",
            "alternativas-TOTAL_FORMS": "4",
            "alternativas-INITIAL_FORMS": "0",
            "alternativas-0-eh_correta": "on",
        }
        for index in range(4):
            payload[f"alternativas-{index}-texto"] = f"Alternativa nova {index}"
        self.assertRedirects(self.client.post(url, payload), reverse("submeter_selecao"))
        question = Questao.objects.get(enunciado="Nova questão")
        self.assertEqual(question.componente_id, self.ab.pk)

    def test_generator_deduplicates_aliases_and_can_select_a_lower_quantity(self):
        self.consolidate()
        self.client.force_login(self.staff)
        response = self.client.get(reverse("prova_gerada"), {"componentes": [self.ab.pk, self.cd.pk]})
        self.assertEqual(len(response.context["componentes"]), 1)
        self.assertEqual(len(response.context["questoes_selecionadas"]), 2)
        response = self.client.get(
            reverse("prova_gerada"),
            {"componentes": [self.ab.pk, self.cd.pk], f"quantidade_{self.ab.pk}": "1"},
        )
        self.assertEqual(len(response.context["questoes_selecionadas"]), 1)
        for value in ("0", "7", "-1", "bad", "9" * 5000):
            response = self.client.get(
                reverse("prova_gerada"), {"componentes": [self.ab.pk], f"quantidade_{self.ab.pk}": value}
            )
            self.assertRedirects(response, reverse("pagina_gerar_prova"))
        self.ab.refresh_from_db()
        self.assertEqual(self.ab.numero_questoes_prova, 6)

    def test_active_component_api_excludes_aliases_and_exposes_shared_target(self):
        self.consolidate()
        period = Periodo.objects.get(nome="6º Período", semestre=self.semester)
        data = self.client.get(reverse("api_get_componentes"), {"periodo_id": period.pk}).json()
        self.assertEqual(len(data), 13)
        shared = [c for c in data if c["id"] == self.ab.pk]
        self.assertEqual(shared[0]["numero_questoes_prova"], 6)
        self.assertIn("turmas A, B, C e D", shared[0]["nome"])
        self.assertFalse(any(c["id"] == self.cd.pk for c in data))
