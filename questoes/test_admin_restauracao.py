from unittest.mock import patch

from django.contrib.admin.models import CHANGE, DELETION, LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core import mail
from django.db import OperationalError
from django.test import Client, TestCase
from django.urls import reverse

from pre_auditoria.models import (
    AlteracaoPreAuditoria,
    DecisaoPreAuditoria,
    EvidenciaRevisor,
    ReciboDecisao,
    SolicitacaoCorrecao,
)

from .exclusao import arquivar_questoes
from .models import Alternativa, ComponenteCurricular, Periodo, Questao, Semestre
from .test_admin_exclusao import criar_questao_com_historico


class RestauracaoQuestaoAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser("admin-restauracao")
        semestre = Semestre.objects.create(nome="2026.2")
        periodo = Periodo.objects.create(nome="2º Período", semestre=semestre)
        cls.componente = ComponenteCurricular.objects.create(
            nome="Clínica sintética", periodo=periodo, numero_questoes_prova=1
        )
        cls.questao = criar_questao_com_historico(cls.componente)
        cls.questao_ativa = Questao.objects.values().get(pk=cls.questao.pk)

    def setUp(self):
        self.client.force_login(self.admin)
        self.lista = reverse("admin:questoes_questaoexcluida_changelist")
        self.detalhe = reverse("admin:questoes_questaoexcluida_change", args=[self.questao.pk])
        self.previa = reverse("admin:questoes_questaoexcluida_restaurar", args=[self.questao.pk])
        self.confirmar = reverse("admin:questoes_questaoexcluida_confirmar_restauracao")
        exclusao = reverse("admin:questoes_questao_delete", args=[self.questao.pk])
        self.assertEqual(self.client.post(exclusao, {"post": "yes"}).status_code, 302)

    def confirmar_token(self, token):
        return self.client.post(self.confirmar, {"confirmacao": token}, follow=True)

    def test_individual_preserves_content_history_status_and_returns_to_exam_bank(self):
        registros = {
            modelo: list(modelo.objects.order_by("pk").values())
            for modelo in (
                Alternativa,
                AlteracaoPreAuditoria,
                DecisaoPreAuditoria,
                EvidenciaRevisor,
                ReciboDecisao,
                SolicitacaoCorrecao,
                self.questao.pareceres_pre_auditoria.model,
            )
        }
        self.assertContains(self.client.get(self.detalhe), "Restaurar questão")
        previa = self.client.get(self.previa)
        self.assertContains(previa, "Confirmar restauração</button>")
        self.assertContains(previa, self.questao.enunciado)
        self.assertFalse(Questao.objects.filter(pk=self.questao.pk).exists())
        self.assertFalse(LogEntry.objects.filter(action_flag=CHANGE).exists())

        resposta = self.confirmar_token(previa.context["confirmacao"])
        self.assertRedirects(resposta, self.lista)
        self.assertContains(resposta, "Questões restauradas ao banco ativo: 1.")
        self.assertNotContains(resposta, self.questao.enunciado)
        self.assertEqual(Questao.objects.values().get(pk=self.questao.pk), self.questao_ativa)
        for modelo, linhas in registros.items():
            self.assertEqual(list(modelo.objects.order_by("pk").values()), linhas)
        self.assertEqual(len(mail.outbox), 0)

        logs = LogEntry.objects.filter(
            content_type=ContentType.objects.get_for_model(Questao), object_id=self.questao.pk
        )
        self.assertEqual(logs.filter(action_flag=DELETION).count(), 1)
        log = logs.get(action_flag=CHANGE)
        self.assertEqual(log.user_id, self.admin.pk)
        self.assertIn("restaurada", log.get_change_message())
        historico = self.client.get(reverse("admin:questoes_questao_history", args=[self.questao.pk]))
        self.assertContains(historico, "restaurada ao banco ativo.")
        previa_prova = self.client.get(
            reverse("prova_gerada"), {"componentes": self.componente.pk, "preflight": "1"}
        )
        self.assertEqual(previa_prova.status_code, 200)
        self.assertIn("url_abertura", previa_prova.json())

    def test_bulk_restores_only_confirmed_rows_and_rejects_replays(self):
        segunda = criar_questao_com_historico(self.componente, 2)
        terceira = criar_questao_com_historico(self.componente, 3)
        ativa = criar_questao_com_historico(self.componente, 4)
        arquivar_questoes(Questao.objects.filter(pk__in=[segunda.pk, terceira.pk]), self.admin)
        previa = self.client.post(
            self.lista,
            {
                "action": "restaurar_selecionadas",
                "_selected_action": [self.questao.pk, segunda.pk, ativa.pk],
                "index": 0,
            },
        )
        self.assertContains(previa, segunda.enunciado)
        self.assertNotContains(previa, terceira.enunciado)
        self.assertNotContains(previa, ativa.enunciado)
        self.assertEqual(list(Questao.objects.values_list("pk", flat=True)), [ativa.pk])
        token = previa.context["confirmacao"]
        self.assertContains(self.confirmar_token(token), "Questões restauradas ao banco ativo: 2.")
        self.assertContains(self.confirmar_token(token), "A situação de uma ou mais questões mudou.")
        self.assertCountEqual(
            Questao.objects.values_list("pk", flat=True), [self.questao.pk, segunda.pk, ativa.pk]
        )
        self.assertEqual(LogEntry.objects.filter(action_flag=CHANGE).count(), 2)

    def test_previous_confirmation_cannot_restore_a_new_archival(self):
        token = self.client.get(self.previa).context["confirmacao"]
        self.confirmar_token(token)
        arquivar_questoes(Questao.objects.filter(pk=self.questao.pk), self.admin)
        self.assertContains(self.confirmar_token(token), "A situação de uma ou mais questões mudou.")
        self.assertFalse(Questao.objects.filter(pk=self.questao.pk).exists())
        self.assertEqual(LogEntry.objects.filter(action_flag=CHANGE).count(), 1)

    def test_view_only_staff_cannot_restore_and_editors_get_the_action(self):
        token = self.client.get(self.previa).context["confirmacao"]
        gestor = get_user_model().objects.create_user("gestor-restauracao", is_staff=True)
        gestor.user_permissions.add(Permission.objects.get(codename="view_questao"))
        self.client.force_login(gestor)
        self.assertNotContains(self.client.get(self.detalhe), "Restaurar questão")
        self.assertNotContains(self.client.get(self.lista), "Restaurar questões selecionadas")
        self.assertEqual(self.client.get(self.previa).status_code, 403)
        self.assertEqual(self.client.post(self.confirmar, {"confirmacao": token}).status_code, 403)

        gestor.user_permissions.add(Permission.objects.get(codename="change_questao"))
        self.assertContains(self.client.get(self.lista), "Restaurar questões selecionadas")
        self.assertContains(self.confirmar_token(token), "Confirmação inválida.")
        self.assertFalse(Questao.objects.filter(pk=self.questao.pk).exists())
        token_gestor = self.client.get(self.previa).context["confirmacao"]
        self.assertContains(self.confirmar_token(token_gestor), "Questões restauradas ao banco ativo: 1.")
        self.assertEqual(LogEntry.objects.get(action_flag=CHANGE).user_id, gestor.pk)

    def test_confirmation_requires_valid_signature_post_and_csrf(self):
        token = self.client.get(self.previa).context["confirmacao"]
        self.assertEqual(self.client.get(self.confirmar, {"confirmacao": token}).status_code, 405)
        self.assertEqual(self.client.post(self.previa, {"confirmacao": token}).status_code, 405)
        self.assertContains(self.confirmar_token(token + "adulterado"), "Confirmação inválida.")
        cliente = Client(enforce_csrf_checks=True)
        cliente.force_login(self.admin)
        self.assertEqual(cliente.post(self.confirmar, {"confirmacao": token}).status_code, 403)
        self.assertFalse(Questao.objects.filter(pk=self.questao.pk).exists())

    def test_log_failure_rolls_back_the_entire_batch(self):
        segunda = criar_questao_com_historico(self.componente, 2)
        arquivar_questoes(Questao.objects.filter(pk=segunda.pk), self.admin)
        previa = self.client.post(
            self.lista,
            {"action": "restaurar_selecionadas", "_selected_action": [self.questao.pk, segunda.pk]},
        )
        with patch(
            "questoes.exclusao.LogEntry.objects.log_actions", side_effect=OperationalError("synthetic")
        ):
            self.assertContains(
                self.confirmar_token(previa.context["confirmacao"]), "Não foi possível restaurar as questões."
            )
        self.assertFalse(Questao.objects.exists())
        self.assertFalse(LogEntry.objects.filter(action_flag=CHANGE).exists())
