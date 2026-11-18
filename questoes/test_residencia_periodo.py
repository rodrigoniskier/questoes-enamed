"""Residência Médica deve aparecer como Período real nos dois aplicativos."""

from importlib import import_module
from io import StringIO
from types import SimpleNamespace

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from django.urls import reverse

from .models import Alternativa, ComponenteCurricular, Periodo, Questao, Semestre


class ResidenciaPeriodoTests(TestCase):
    def setUp(self):
        self.semestre = Semestre.objects.create(nome="2026.2", ativo=True)
        call_command("configurar_residencia", stdout=StringIO())
        self.periodo = Periodo.objects.get(semestre=self.semestre, nome="RESIDÊNCIA MÉDICA")
        self.componentes = list(self.periodo.componentes.order_by("pk"))
        self.gestor = get_user_model().objects.create_user("gestor-residencia-periodo", is_staff=True)

    def test_appears_in_both_initial_period_selectors(self):
        self.assertTrue(self.periodo.ativo)
        self.assertEqual(len(self.componentes), 5)
        self.assertEqual(
            [(componente.nome, componente.numero_questoes_prova) for componente in self.componentes],
            [
                ("Medicina Social e Preventiva - RM", 10),
                ("Obstetrícia e Ginecologia - RM", 10),
                ("Pediatria - RM", 10),
                ("Clínica Médica - RM", 10),
                ("Cirurgia Geral - RM", 10),
            ],
        )
        self.assertEqual(sum(c.numero_questoes_prova for c in self.componentes), 50)
        pagina_submissao = self.client.get(reverse("submeter_selecao"))
        self.assertContains(pagina_submissao, f'<option value="{self.periodo.pk}">RESIDÊNCIA MÉDICA</option>')
        self.client.force_login(self.gestor)
        pagina_gerador = self.client.get(reverse("pagina_gerar_prova"))
        self.assertContains(pagina_gerador, f'<option value="{self.periodo.pk}">RESIDÊNCIA MÉDICA</option>')
        self.assertContains(pagina_gerador, 'value="residencia"')
        componentes = self.client.get(
            reverse("api_get_componentes"), {"periodo_id": self.periodo.pk}
        ).json()
        self.assertEqual(len(componentes), 5)
        self.assertEqual(sum(c["numero_questoes_prova"] for c in componentes), 50)

    def test_submission_uses_residency_only_and_four_alternatives(self):
        componente = self.componentes[0]
        url = reverse("submeter_resposta_unica") + f"?componente={componente.pk}"
        resposta = self.client.get(url)
        self.assertEqual(resposta.context["questao_form"].initial["uso_prova"], "RESIDENCIA")
        self.assertEqual(resposta.context["num_alternativas"], 4)
        token = resposta.context["submission_token"]
        dados = {
            "professor_nome": "Docente",
            "professor_email": "professor@example.com",
            "tipo_questao": "RESPOSTA_UNICA",
            "componente": str(componente.pk),
            "uso_prova": "RESIDENCIA",
            "texto_base": "Paciente com febre.",
            "enunciado": "Qual a melhor conduta?",
            "justificativa": "Conduta A.",
            "submission_token": token,
            "submit_action": "finish",
            "alternativas-TOTAL_FORMS": "4",
            "alternativas-INITIAL_FORMS": "0",
            "alternativas-0-eh_correta": "on",
        }
        for indice in range(4):
            dados[f"alternativas-{indice}-texto"] = f"Conduta {indice}"
        self.assertRedirects(self.client.post(url, dados), reverse("submeter_selecao"))
        questao = Questao.objects.get(enunciado="Qual a melhor conduta?")
        self.assertEqual(questao.uso_prova, "RESIDENCIA")
        self.assertEqual(questao.alternativas.count(), 4)
        dados["uso_prova"] = "INTEGRADA"
        dados["submission_token"] = self.client.get(url).context["submission_token"]
        dados["enunciado"] = "Tentativa com destino incorreto"
        recusado = self.client.post(url, dados)
        self.assertEqual(recusado.status_code, 200)
        self.assertContains(recusado, "Este período aceita somente questões de Residência Médica.")
        self.assertFalse(Questao.objects.filter(enunciado="Tentativa com destino incorreto").exists())

    def test_generator_selects_residency_questions_in_its_own_period(self):
        self.client.force_login(self.gestor)
        for indice, componente in enumerate(self.componentes[:2]):
            questao = Questao.objects.create(
                professor_nome="Docente",
                professor_email="professor@example.com",
                componente=componente,
                uso_prova="RESIDENCIA",
                tipo_questao="RESPOSTA_UNICA",
                status="APROVADA",
                texto_base=f"Caso clínico {indice + 1}.",
                enunciado=f"Diagnóstico {indice + 1}?",
                justificativa="Justificativa.",
            )
            for posicao in range(4):
                Alternativa.objects.create(
                    questao=questao, texto=f"Alternativa {posicao + 1}", eh_correta=posicao == 0
                )
        parametros = {
            "componentes": [c.pk for c in self.componentes[:2]],
            "tipo_prova": "residencia",
            f"quantidade_{self.componentes[0].pk}": "1",
            f"quantidade_{self.componentes[1].pk}": "1",
            "preflight": "1",
        }
        previa = self.client.get(reverse("prova_gerada"), parametros)
        self.assertEqual(previa.status_code, 200)
        self.assertEqual(previa.json()["total"], 2)
        prova = self.client.get(previa.json()["url_abertura"])
        self.assertEqual(prova.status_code, 200)
        self.assertEqual(prova.context["titulo_topo"], "PROVA DE RESIDÊNCIA MÉDICA - UNIPÊ")
        self.assertEqual(len(prova.context["questoes_selecionadas"]), 2)

    def test_command_can_run_twice_without_duplicates_or_changing_existing_quotas(self):
        primeiro = self.componentes[0]
        primeiro.numero_questoes_prova = 15
        primeiro.save(update_fields=["numero_questoes_prova"])
        call_command("configurar_residencia", stdout=StringIO())
        self.assertEqual(self.periodo.componentes.count(), 5)
        primeiro.refresh_from_db()
        self.assertEqual(primeiro.numero_questoes_prova, 15)

    def test_dry_run_preserves_database(self):
        self.periodo.delete()
        saida = StringIO()
        call_command("configurar_residencia", dry_run=True, stdout=saida)
        self.assertIn("SIMULAÇÃO", saida.getvalue())
        self.assertFalse(Periodo.objects.filter(semestre=self.semestre, nome="RESIDÊNCIA MÉDICA").exists())

    def test_integrated_semester_configuration_keeps_residency_period(self):
        call_command("configurar_semestre_2026_2", stdout=StringIO())
        self.periodo.refresh_from_db()
        self.assertTrue(self.periodo.ativo)
        self.assertEqual(self.periodo.componentes.count(), 5)
        self.assertEqual(self.semestre.periodos.filter(ativo=True).count(), 6)


class ResidenciaMigrationTests(TestCase):
    def test_migration_activates_existing_residency_and_preserves_custom_components(self):
        semestre = Semestre.objects.create(nome="2026.2", ativo=True)
        periodo = Periodo.objects.create(nome="RESIDÊNCIA MÉDICA", ativo=False, semestre=semestre)
        componente = ComponenteCurricular.objects.create(
            nome="Área personalizada", periodo=periodo, numero_questoes_prova=35
        )
        migracao = import_module("questoes.migrations.0023_residencia_periodo_ativo")
        editor = SimpleNamespace(connection=connection)
        migracao.cadastrar_residencia(apps, editor)
        migracao.cadastrar_residencia(apps, editor)
        periodo.refresh_from_db()
        componente.refresh_from_db()
        self.assertTrue(periodo.ativo)
        self.assertEqual(periodo.componentes.count(), 1)
        self.assertEqual(componente.numero_questoes_prova, 35)

    def test_migration_creates_once_in_latest_active_semester(self):
        anterior = Semestre.objects.create(nome="2026.1", ativo=True)
        atual = Semestre.objects.create(nome="2026.2", ativo=True)
        migracao = import_module("questoes.migrations.0023_residencia_periodo_ativo")
        editor = SimpleNamespace(connection=connection)
        migracao.cadastrar_residencia(apps, editor)
        migracao.cadastrar_residencia(apps, editor)
        self.assertFalse(Periodo.objects.filter(semestre=anterior).exists())
        self.assertEqual(Periodo.objects.filter(semestre=atual, ativo=True).count(), 1)
        self.assertEqual(ComponenteCurricular.objects.filter(periodo__semestre=atual).count(), 5)
