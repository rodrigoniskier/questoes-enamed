"""Exclusão confirmada do banco ativo, preservando o histórico acadêmico."""

import os
import uuid
from datetime import timedelta
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

from django.contrib import admin
from django.contrib.admin.models import DELETION, LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core import mail, signing
from django.db import OperationalError
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from pre_auditoria.models import (
    AlteracaoPreAuditoria,
    DecisaoPreAuditoria,
    EvidenciaRevisor,
    ReciboDecisao,
    SolicitacaoCorrecao,
)
from pre_auditoria.services import executar_pre_auditoria, snapshot_questao

from .avaliacao import CORRECTION_SIGNING_SALT, notificar_decisao
from .exclusao import arquivar_questoes
from .models import Alternativa, ComponenteCurricular, Periodo, Questao, QuestaoExcluida, Semestre


def criar_questao_com_historico(componente, numero=1):
    questao = Questao.objects.create(
        componente=componente,
        professor_nome="Docente sintético",
        professor_email="docente@example.com",
        enunciado=f"Questão sintética para exclusão {numero}",
        texto_base="Contexto sintético.",
        justificativa="Justificativa sintética.",
        status="APROVADA",
    )
    for indice in range(4):
        Alternativa.objects.create(questao=questao, texto=f"Opção {indice + 1}", eh_correta=indice == 0)
    parecer = executar_pre_auditoria(questao)
    decisao = DecisaoPreAuditoria.objects.create(
        questao=questao,
        parecer=parecer,
        acao="APROVADA",
        fingerprint=parecer.fingerprint,
        justificativa="Parecer humano preservado.",
        notificacao_estado="PENDENTE",
    )
    ReciboDecisao.objects.create(pk=uuid.uuid4(), decisao=decisao)
    EvidenciaRevisor.objects.create(parecer=parecer, criterio_id="TESTE", evidencia="Evidência preservada.")
    correcao = SolicitacaoCorrecao.objects.create(
        questao=questao,
        parecer=parecer,
        mensagem="Orientação preservada.",
        expira_em=timezone.now() + timedelta(days=3),
    )
    snapshot = snapshot_questao(questao)
    AlteracaoPreAuditoria.objects.create(
        questao=questao,
        antes=snapshot,
        depois=snapshot,
        solicitacao_correcao=correcao,
    )
    return questao


class ExclusaoQuestaoAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser("admin-exclusao", "admin@example.com", "teste")
        semestre = Semestre.objects.create(nome="2026.2")
        periodo = Periodo.objects.create(nome="2º Período", semestre=semestre)
        cls.componente = ComponenteCurricular.objects.create(
            nome="Clínica sintética", periodo=periodo, numero_questoes_prova=1
        )
        cls.questao = criar_questao_com_historico(cls.componente)

    def setUp(self):
        self.client.force_login(self.admin)
        self.url = reverse("admin:questoes_questao_delete", args=[self.questao.pk])

    def excluir(self, questao=None):
        url = reverse("admin:questoes_questao_delete", args=[(questao or self.questao).pk])
        return self.client.post(url, {"post": "yes"})

    def test_confirmation_and_cancel_leave_every_record_unchanged(self):
        antes = Questao.objects.values().get(pk=self.questao.pk)
        resposta = self.client.get(self.url)
        self.assertContains(resposta, "Confirmar exclusão</button>", html=False)
        self.assertContains(resposta, "serão preservados")
        self.assertContains(resposta, "Cancelar")
        self.assertFalse(resposta.context["perms_lacking"])
        self.assertFalse(resposta.context["protected"])
        self.assertEqual(Questao.objects.values().get(pk=self.questao.pk), antes)
        self.assertFalse(LogEntry.objects.filter(action_flag=DELETION).exists())

    def test_confirmation_archives_question_and_preserves_all_content_and_history(self):
        antes = Questao.objects.values().get(pk=self.questao.pk)
        registros = {
            modelo: list(modelo.objects.values())
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
        self.assertRedirects(self.excluir(), reverse("admin:questoes_questao_changelist"))
        self.assertFalse(Questao.objects.filter(pk=self.questao.pk).exists())
        depois = Questao.todos_objetos.values().get(pk=self.questao.pk)
        self.assertIsNotNone(depois.pop("excluida_em"))
        self.assertEqual(depois.pop("excluida_por_id"), self.admin.pk)
        antes.pop("excluida_em")
        antes.pop("excluida_por_id")
        self.assertEqual(depois, antes)
        for modelo, linhas in registros.items():
            self.assertEqual(list(modelo.objects.values()), linhas)
        self.assertEqual(LogEntry.objects.filter(action_flag=DELETION).count(), 1)

    def test_bulk_confirmation_preserves_history_and_does_not_touch_unselected_question(self):
        segunda = criar_questao_com_historico(self.componente, 2)
        terceira = criar_questao_com_historico(self.componente, 3)
        url = reverse("admin:questoes_questao_changelist")
        dados = {"action": "delete_selected", "_selected_action": [self.questao.pk, segunda.pk]}
        previa = self.client.post(url, dados)
        self.assertContains(previa, "Confirmar exclusão</button>")
        self.assertEqual(Questao.objects.count(), 3)
        self.assertRedirects(self.client.post(url, {**dados, "post": "yes"}), url)
        self.assertEqual(list(Questao.objects.values_list("pk", flat=True)), [terceira.pk])
        self.assertEqual(Questao.todos_objetos.count(), 3)
        self.assertEqual(DecisaoPreAuditoria.objects.count(), 3)
        self.assertEqual(LogEntry.objects.filter(action_flag=DELETION).count(), 2)

    def test_question_delete_permission_is_required_but_audit_delete_permission_is_not(self):
        gestor = get_user_model().objects.create_user("gestor-exclusao", is_staff=True)
        gestor.user_permissions.add(Permission.objects.get(codename="view_questao"))
        self.client.force_login(gestor)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.excluir().status_code, 403)
        self.assertTrue(Questao.objects.filter(pk=self.questao.pk).exists())
        gestor.user_permissions.add(Permission.objects.get(codename="delete_questao"))
        self.assertFalse(gestor.has_perm("pre_auditoria.delete_decisaopreauditoria"))
        self.assertContains(self.client.get(self.url), "Confirmar exclusão</button>")
        self.assertEqual(self.excluir().status_code, 302)
        self.assertTrue(DecisaoPreAuditoria.objects.exists())

    def test_csrf_is_required_for_confirmation(self):
        cliente = Client(enforce_csrf_checks=True)
        cliente.force_login(self.admin)
        self.assertEqual(cliente.get(self.url).status_code, 200)
        self.assertEqual(cliente.post(self.url, {"post": "yes"}).status_code, 403)
        self.assertTrue(Questao.objects.filter(pk=self.questao.pk).exists())

    def test_archived_question_is_read_only_and_available_with_its_history(self):
        self.excluir()
        url = reverse("admin:questoes_questaoexcluida_change", args=[self.questao.pk])
        consulta = self.client.get(url)
        self.assertContains(consulta, self.questao.enunciado)
        self.assertContains(consulta, "Histórico de Alterações")
        self.assertContains(consulta, "Opção 1")
        self.assertContains(consulta, "admin-exclusao")
        self.assertNotContains(consulta, 'name="_save"')
        self.assertFalse(consulta.context["has_change_permission"])
        self.assertEqual(self.client.post(url, {"enunciado": "Conteúdo adulterado"}).status_code, 403)
        self.assertEqual(
            self.client.post(
                reverse("admin:questoes_questaoexcluida_delete", args=[self.questao.pk]), {"post": "yes"}
            ).status_code,
            403,
        )
        self.assertEqual(Questao.todos_objetos.get(pk=self.questao.pk).enunciado, self.questao.enunciado)
        self.assertRedirects(
            self.client.get(reverse("admin:questoes_questao_change", args=[self.questao.pk])),
            reverse("admin:index"),
        )

    def test_multiple_answer_archive_keeps_statement_and_combination_tabs_read_only(self):
        Questao.objects.filter(pk=self.questao.pk).update(tipo_questao="MULTIPLA_ESCOLHA")
        self.excluir()
        consulta = self.client.get(reverse("admin:questoes_questaoexcluida_change", args=[self.questao.pk]))
        self.assertContains(consulta, "Combinações A–D")
        self.assertContains(consulta, "Opção 1")
        self.assertNotContains(consulta, 'name="combinacoes_personalizadas"')
        self.assertNotContains(consulta, 'name="_save"')

    def test_question_without_audit_history_uses_same_confirmation_and_archive(self):
        questao = Questao.objects.create(
            componente=self.componente,
            professor_nome="Docente",
            professor_email="docente@example.com",
            enunciado="Questão sem parecer",
            justificativa="Justificativa.",
        )
        self.assertContains(
            self.client.get(reverse("admin:questoes_questao_delete", args=[questao.pk])),
            "Confirmar exclusão</button>",
        )
        self.assertEqual(self.excluir(questao).status_code, 302)
        self.assertFalse(Questao.objects.filter(pk=questao.pk).exists())
        self.assertTrue(Questao.todos_objetos.filter(pk=questao.pk, excluida_em__isnull=False).exists())

    def test_archived_question_is_removed_from_bank_exams_and_existing_signed_selection(self):
        params = {"componentes": self.componente.pk, "preflight": "1"}
        previa = self.client.get(reverse("prova_gerada"), params).json()
        self.excluir()
        self.assertRedirects(self.client.get(previa["url_abertura"]), reverse("pagina_gerar_prova"))
        self.assertEqual(self.client.get(reverse("prova_gerada"), params).status_code, 400)
        banco = self.client.get(reverse("api_get_historico"), {"componente_id": self.componente.pk}).json()
        self.assertEqual(banco["questoes"], [])
        self.assertEqual(banco["contagem"]["enviado"], 0)
        self.assertFalse(self.componente.questoes.exists())
        self.assertNotContains(
            self.client.get(reverse("submeter_visualizar_questoes"), {"componente": self.componente.pk}),
            self.questao.enunciado,
        )

    def test_old_correction_and_evidence_links_are_inactive_and_notification_is_cancelled(self):
        correcao = self.questao.solicitacoes_correcao.get()
        token = signing.dumps(
            {"nonce": str(correcao.nonce), "fingerprint": correcao.parecer.fingerprint},
            salt=CORRECTION_SIGNING_SALT,
        )
        self.excluir()
        url = reverse("pre_auditoria:correcao_professor", args=[token])
        self.assertEqual(self.client.get(url).status_code, 410)
        self.assertEqual(self.client.post(url, {"enunciado": "Não pode reativar"}).status_code, 410)
        self.assertEqual(
            self.client.post(
                reverse("pre_auditoria:registrar_evidencia", args=[correcao.parecer_id]),
                {"criterio_id": "TESTE", "evidencia": "Nova"},
            ).status_code,
            410,
        )
        decisao = self.questao.decisoes_pre_auditoria.get()
        notificar_decisao(decisao.pk)
        decisao.refresh_from_db()
        self.assertEqual(decisao.notificacao_estado, "SUPERADO")
        self.assertEqual(len(mail.outbox), 0)

    def test_admin_totals_ignore_archived_question(self):
        criar_questao_com_historico(self.componente, 2)
        self.excluir()
        request = RequestFactory().get("/admin/")
        request.user = self.admin
        for modelo in (Semestre, Periodo, ComponenteCurricular):
            item = admin.site.get_model_admin(modelo).get_queryset(request).get()
            self.assertEqual(item._total, 1)
            self.assertEqual(item._aprovadas, 1)
            self.assertEqual(item._pendentes, 0)

    def test_failed_archive_rolls_back_the_admin_deletion_log(self):
        def falha_apos_gravar(queryset, responsavel):
            arquivar_questoes(queryset, responsavel)
            raise OperationalError("synthetic")

        with patch("questoes.admin.arquivar_questoes", side_effect=falha_apos_gravar):
            resposta = self.excluir()
        self.assertRedirects(resposta, self.url, fetch_redirect_response=False)
        self.assertContains(self.client.get(self.url), "A questão foi preservada")
        self.assertTrue(Questao.objects.filter(pk=self.questao.pk).exists())
        self.assertFalse(LogEntry.objects.filter(action_flag=DELETION).exists())
        self.assertFalse(QuestaoExcluida.todos_objetos.filter(excluida_em__isnull=False).exists())

    def test_failed_bulk_archive_rolls_back_every_question_and_log(self):
        segunda = criar_questao_com_historico(self.componente, 2)

        def falha_apos_gravar(queryset, responsavel):
            arquivar_questoes(queryset, responsavel)
            raise OperationalError("synthetic")

        url = reverse("admin:questoes_questao_changelist")
        with patch("questoes.admin.arquivar_questoes", side_effect=falha_apos_gravar):
            resposta = self.client.post(
                url,
                {
                    "action": "delete_selected",
                    "_selected_action": [self.questao.pk, segunda.pk],
                    "post": "yes",
                },
            )
        self.assertRedirects(resposta, url, fetch_redirect_response=False)
        self.assertEqual(Questao.objects.count(), 2)
        self.assertFalse(LogEntry.objects.filter(action_flag=DELETION).exists())

    def test_repeated_confirmation_does_not_change_archival_date_or_duplicate_log(self):
        self.excluir()
        original = Questao.todos_objetos.values().get(pk=self.questao.pk)
        self.assertEqual(self.excluir().status_code, 302)
        self.assertEqual(Questao.todos_objetos.values().get(pk=self.questao.pk), original)
        self.assertEqual(LogEntry.objects.filter(action_flag=DELETION).count(), 1)


@skipUnless(os.environ.get("RUN_BROWSER_TESTS") == "1", "Habilite RUN_BROWSER_TESTS=1 para Chromium")
class ExclusaoQuestaoBrowserTests(StaticLiveServerTestCase):
    def test_confirm_and_cancel_on_desktop_and_mobile_with_preserved_history(self):
        from playwright.sync_api import sync_playwright

        usuario = get_user_model().objects.create_superuser(
            "admin-exclusao-browser", "admin@example.com", "teste"
        )
        semestre = Semestre.objects.create(nome="2026.2")
        periodo = Periodo.objects.create(nome="2º Período", semestre=semestre)
        componente = ComponenteCurricular.objects.create(nome="Clínica", periodo=periodo)
        questao = criar_questao_com_historico(componente)
        self.client.force_login(usuario)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True, executable_path=os.environ.get("BROWSER_EXECUTABLE") or None
            )
            page = browser.new_page()
            page.context.add_cookies(
                [
                    {
                        "name": "sessionid",
                        "value": self.client.cookies["sessionid"].value,
                        "url": self.live_server_url,
                    }
                ]
            )
            url = self.live_server_url + reverse("admin:questoes_questao_delete", args=[questao.pk])
            for largura in (1440, 390):
                page.set_viewport_size({"width": largura, "height": 1000})
                page.goto(url)
                botao = page.get_by_role("button", name="Confirmar exclusão", exact=True)
                self.assertTrue(botao.is_visible())
                self.assertTrue(botao.is_enabled())
                botao.click(trial=True, timeout=5000)
                if largura >= 992:
                    menu = page.locator(".main-sidebar").bounding_box()
                    self.assertGreaterEqual(botao.bounding_box()["x"], menu["x"] + menu["width"])
                self.assertIn("serão preservados", page.locator("body").inner_text())
                self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), largura + 1)
                if os.environ.get("BROWSER_SCREENSHOT"):
                    destino = Path(os.environ["BROWSER_SCREENSHOT"]).parent
                    destino.mkdir(parents=True, exist_ok=True)
                    page.screenshot(
                        path=str(destino / f"exclusao-confirmacao-{largura}.png"), animations="disabled"
                    )
                page.get_by_role("link", name="Cancelar", exact=True).click()
                page.wait_for_url(self.live_server_url + reverse("admin:questoes_questao_changelist"))
                self.assertIn(questao.enunciado, page.locator("body").inner_text())
            page.goto(url)
            page.get_by_role("button", name="Confirmar exclusão", exact=True).click()
            page.wait_for_url(self.live_server_url + reverse("admin:questoes_questao_changelist"))
            page.goto(
                self.live_server_url + reverse("admin:questoes_questaoexcluida_change", args=[questao.pk])
            )
            self.assertIn(questao.enunciado, page.locator("body").inner_text())
            self.assertEqual(page.locator("[name=_save]").count(), 0)
            browser.close()
        self.assertFalse(Questao.objects.filter(pk=questao.pk).exists())
        self.assertTrue(DecisaoPreAuditoria.objects.filter(questao_id=questao.pk).exists())
