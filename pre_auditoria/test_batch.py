"""Seleções do painel, exportação e decisões atômicas, com dados sintéticos."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail
from django.db import OperationalError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from questoes.avaliacao import registrar_decisao
from questoes.models import Alternativa, ComponenteCurricular, Periodo, Questao, Semestre
from questoes.submission import ASSERTION_ANSWERS

from .models import DecisaoPreAuditoria
from .services import executar_pre_auditoria


class BatchSelectionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        semester = Semestre.objects.create(nome="2026.2")
        period = Periodo.objects.create(nome="2º Período", semestre=semester)
        cls.component = ComponenteCurricular.objects.create(nome="Clínica", periodo=period)
        cls.reviewer = get_user_model().objects.create_user("gestor-lote")
        cls.reviewer.user_permissions.add(
            *Permission.objects.filter(
                codename__in=["view_parecerpreauditoria", "review_parecerpreauditoria"]
            )
        )

    def setUp(self):
        self.client.force_login(self.reviewer)
        self.question = self.new_question("Questão inicial?")
        self.second = self.new_question("Questão final?")
        self.url = reverse("pre_auditoria:aprovar_lote")
        self.export_url = reverse("pre_auditoria:exportar_pdf")
        self.return_to = (
            reverse("pre_auditoria:lista") + f"?componente={self.component.pk}&page=1&status=PENDENTE"
        )

    def new_question(self, text, kind="RESPOSTA_UNICA"):
        question = Questao.objects.create(
            componente=self.component,
            professor_nome="Docente sintético",
            professor_email="sintetico@example.com",
            texto_base="Cenário de teste.",
            enunciado=text,
            tipo_questao=kind,
            proposicao_dois="Proposição II de teste.",
            justificativa="Justificativa completa da alternativa correta e dos distratores.",
        )
        if kind == "ASSERCAO_RAZAO":
            answers = [(f"{letter}) {answer}", letter == "B") for letter, answer in ASSERTION_ANSWERS.items()]
        else:
            answers = [(f"Opção {i}", i == 0) for i in range(4)]
        for text, correct in answers:
            Alternativa.objects.create(questao=question, texto=text, eh_correta=correct)
        return question

    def selection(self, questions=None, **filters):
        page = self.client.get(reverse("pre_auditoria:lista"), filters)
        return {
            "questoes": [str(q.pk) for q in (questions or [self.question, self.second])],
            "selecao": page.context["selecao"],
            "next": self.return_to,
        }

    def preview(self, questions=None):
        for question in questions or [self.question, self.second]:
            executar_pre_auditoria(question, revisor=self.reviewer)
        return self.client.post(self.url, self.selection(questions))

    def confirm(self, preview, **overrides):
        data = {
            "confirmacao": preview.context["confirmacao"],
            "confirmar_aprovacao": "on",
            "next": self.return_to,
        }
        data.update(overrides)
        return self.client.post(self.url, data)

    def assert_untouched(self):
        self.assertFalse(DecisaoPreAuditoria.objects.exists())
        self.assertEqual(Questao.objects.filter(status="APROVADA").count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_export_requires_read_permission_post_and_csrf(self):
        self.client.logout()
        self.assertEqual(self.client.post(self.export_url).status_code, 302)
        ordinary = get_user_model().objects.create_user("sem-permissao")
        self.client.force_login(ordinary)
        self.assertEqual(self.client.post(self.export_url).status_code, 403)
        self.client.force_login(self.reviewer)
        self.assertEqual(self.client.get(self.export_url).status_code, 405)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.reviewer)
        self.assertEqual(csrf_client.post(self.export_url).status_code, 403)

    def test_read_only_reviewer_can_select_and_export_but_cannot_approve(self):
        viewer = get_user_model().objects.create_user("somente-leitura")
        viewer.user_permissions.add(Permission.objects.get(codename="view_parecerpreauditoria"))
        self.client.force_login(viewer)
        selection = self.selection([self.question])
        response = self.client.post(self.export_url, selection)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.question.enunciado)
        self.assertEqual(self.client.post(self.url, selection).status_code, 403)
        page = self.client.get(reverse("pre_auditoria:lista"))
        self.assertContains(page, "Selecionar todas desta página")
        self.assertContains(page, "EXPORTAR SELECIONADAS EM PDF")
        self.assertNotContains(page, "APROVAR SELECIONADAS EM LOTE")
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assert_untouched()

    def test_export_only_selected_in_display_order_with_answers_and_escaped_text(self):
        self.question.enunciado = "Questão <script>alert(1)</script> inicial?"
        self.question.save()
        unselected = self.new_question("QUESTÃO NÃO SELECIONADA")
        response = self.client.post(self.export_url, self.selection())
        self.assertEqual([q.pk for q in response.context["questoes"]], [self.second.pk, self.question.pk])
        self.assertContains(response, "Gabarito: A)")
        self.assertContains(response, self.question.justificativa)
        self.assertContains(response, "&lt;script&gt;alert(1)&lt;/script&gt;")
        self.assertNotContains(response, "<script>alert(1)</script>")
        self.assertNotContains(response, unselected.enunciado)
        self.assert_untouched()

    def test_export_multiple_and_assertion_options_match_dashboard(self):
        multiple = self.new_question("Afirmações?", "MULTIPLA_ESCOLHA")
        assertion = self.new_question("Asserção I.", "ASSERCAO_RAZAO")
        dashboard = self.client.get(reverse("pre_auditoria:lista"))
        displayed = {q.pk: q for q in dashboard.context["questoes"]}
        response = self.client.post(
            self.export_url,
            {"questoes": [multiple.pk, assertion.pk], "selecao": dashboard.context["selecao"]},
        )
        self.assertEqual(response.status_code, 200)
        for question in response.context["questoes"]:
            self.assertEqual(question.gabarito_preview, displayed[question.pk].gabarito_preview)
        self.assertContains(response, "PORQUE")
        self.assertContains(response, "Considerando o texto apresentado")

    def test_invalid_empty_oversized_or_outside_page_selection_is_rejected(self):
        selection = self.selection()
        outside = self.new_question("Fora da tela")
        for ids in ([], ["x"], ["1" * 200], ["١"], [str(outside.pk)], [str(self.question.pk)] * 26):
            for url in (self.url, self.export_url):
                with self.subTest(ids=ids, url=url):
                    response = self.client.post(url, {**selection, "questoes": ids})
                    self.assertRedirects(response, self.return_to)
        self.assert_untouched()

    def test_selected_question_on_another_page_or_component_is_rejected(self):
        for i in range(25):
            self.new_question(f"Questão extra {i}")
        selection = self.selection([self.question])
        self.assertEqual(self.client.post(self.export_url, selection).status_code, 302)
        other_component = ComponenteCurricular.objects.create(nome="Outro", periodo=self.component.periodo)
        selection = self.selection([self.second], componente=other_component.pk)
        self.assertEqual(self.client.post(self.export_url, selection).status_code, 302)
        self.assert_untouched()

    def test_selection_token_tampering_expiry_or_foreign_session_is_rejected(self):
        selection = self.selection()
        for url in (self.url, self.export_url):
            self.assertEqual(
                self.client.post(url, {**selection, "selecao": selection["selecao"] + "x"}).status_code, 302
            )
            with patch("django.core.signing.time.time", return_value=timezone.now().timestamp() + 1801):
                self.assertEqual(self.client.post(url, selection).status_code, 302)
            other = Client()
            other.force_login(self.reviewer)
            self.assertEqual(other.post(url, selection).status_code, 302)
        self.assert_untouched()

    def test_changed_content_status_or_archived_component_cannot_be_exported(self):
        for field, value in (("enunciado", "Conteúdo alterado"), ("status", "REPROVADA")):
            selection = self.selection(status="TODAS")
            old_value = getattr(self.question, field)
            Questao.objects.filter(pk=self.question.pk).update(**{field: value})
            self.assertEqual(self.client.post(self.export_url, selection).status_code, 302)
            Questao.objects.filter(pk=self.question.pk).update(**{field: old_value})
        selection = self.selection()
        target = ComponenteCurricular.objects.create(nome="Destino", periodo=self.component.periodo)
        ComponenteCurricular.objects.filter(pk=self.component.pk).update(consolidado_em=target)
        self.assertEqual(self.client.post(self.export_url, selection).status_code, 302)
        self.assert_untouched()

    def test_preview_requires_explicit_confirmation_and_preserves_filters(self):
        preview = self.preview()
        self.assertEqual(preview.status_code, 200)
        self.assertContains(preview, "2 questões selecionadas")
        self.assert_untouched()
        response = self.confirm(preview, confirmar_aprovacao="")
        self.assertEqual(response.status_code, 400)
        self.assert_untouched()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.confirm(preview)
        self.assertRedirects(response, self.return_to)
        self.assertEqual(Questao.objects.filter(status="APROVADA").count(), 2)
        self.assertEqual(DecisaoPreAuditoria.objects.count(), 2)
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_missing_stale_or_decided_question_blocks_whole_batch(self):
        for state in ("missing", "stale", "decided"):
            with self.subTest(state=state):
                if state != "missing":
                    executar_pre_auditoria(self.question, revisor=self.reviewer)
                if state == "stale":
                    self.question.enunciado += " Alterada"
                    self.question.save()
                if state == "decided":
                    self.question.status = "APROVADA"
                    self.question.save()
                executar_pre_auditoria(self.second, revisor=self.reviewer)
                response = self.client.post(self.url, self.selection(status="TODAS"))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["impedimentos"])
                self.assertNotContains(response, "CONFIRMAR APROVAÇÃO DE")
                self.assertFalse(DecisaoPreAuditoria.objects.exists())

    def test_exception_requires_reason_and_records_human_decision(self):
        self.question.texto_base = ""
        self.question.save()
        preview = self.preview()
        self.assertTrue(preview.context["exige_justificativa"])
        response = self.confirm(preview, justificativa="Curta")
        self.assertEqual(response.status_code, 409)
        self.assertContains(response, "Curta", status_code=409)
        self.assert_untouched()
        reason = "Inspecionei o conteúdo e considero esta estrutura adequada ao objetivo."
        response = self.confirm(preview, justificativa=reason)
        self.assertRedirects(response, self.return_to)
        decision = DecisaoPreAuditoria.objects.get(questao=self.question)
        self.assertTrue(decision.aprovacao_excepcional)
        self.assertEqual(decision.justificativa, reason)
        self.assertEqual(decision.revisor, self.reviewer)

    def test_changed_question_after_preview_keeps_reason_and_prevents_all_writes(self):
        preview = self.preview()
        self.question.enunciado = "Alteração concorrente"
        self.question.save()
        reason = "Justificativa do gestor que deve permanecer na tela."
        response = self.confirm(preview, justificativa=reason)
        self.assertIn(response.status_code, (400, 409))
        self.assertContains(response, reason, status_code=response.status_code)
        self.assert_untouched()

    def test_new_report_after_preview_invalidates_confirmation(self):
        preview = self.preview()
        executar_pre_auditoria(self.question, revisor=self.reviewer)
        response = self.confirm(preview, justificativa="Justificativa preservada nesta confirmação.")
        self.assertEqual(response.status_code, 409)
        self.assertContains(response, "parecer da questão", status_code=409)
        self.assert_untouched()

    def test_atomic_rollback_on_second_write_failure_preserves_reason_and_sends_nothing(self):
        preview = self.preview()

        def fail_second(question_id, *args, **kwargs):
            if question_id == self.question.pk:
                raise OperationalError("falha sintética no segundo item")
            return registrar_decisao(question_id, *args, **kwargs)

        reason = "Justificativa que deve ser preservada após falha de gravação."
        with patch("pre_auditoria.views.registrar_decisao", side_effect=fail_second):
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                response = self.confirm(preview, justificativa=reason)
        self.assertEqual(response.status_code, 503)
        self.assertContains(response, reason, status_code=503)
        self.assertEqual(callbacks, [])
        self.assert_untouched()
        self.assertRedirects(self.confirm(preview, justificativa=reason), self.return_to)

    def test_repeated_confirmation_never_duplicates_decisions_or_notifications(self):
        preview = self.preview()
        with self.captureOnCommitCallbacks(execute=True):
            self.confirm(preview)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.confirm(preview)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(DecisaoPreAuditoria.objects.count(), 2)
        self.assertEqual(len(mail.outbox), 2)

    def test_confirmation_token_cannot_be_used_as_selection_or_modified(self):
        preview = self.preview()
        self.assertEqual(
            self.confirm(preview, confirmacao=preview.context["confirmacao"] + "x").status_code, 409
        )
        self.assertEqual(
            self.client.post(
                self.export_url, {"questoes": [self.question.pk], "selecao": preview.context["confirmacao"]}
            ).status_code,
            302,
        )
        self.assert_untouched()

    def test_old_confirmation_cannot_replay_after_a_new_human_decision(self):
        preview = self.preview()
        self.confirm(preview)
        for question in (self.question, self.second):
            registrar_decisao(question.pk, self.reviewer, "PENDENTE")
        response = self.confirm(preview)
        self.assertContains(response, "mudou", status_code=409)
        self.assertEqual(Questao.objects.filter(status="PENDENTE").count(), 2)
        self.assertEqual(DecisaoPreAuditoria.objects.count(), 4)

    def test_return_url_cannot_redirect_to_another_host(self):
        preview = self.preview()
        response = self.confirm(preview, next="https://example.org/externo")
        self.assertRedirects(response, reverse("pre_auditoria:lista"))

    def test_duplicate_selected_ids_only_create_one_decision_per_question(self):
        executar_pre_auditoria(self.question, revisor=self.reviewer)
        selection = self.selection([self.question])
        selection["questoes"] *= 2
        preview = self.client.post(self.url, selection)
        self.assertEqual(len(preview.context["questoes"]), 1)
        self.assertRedirects(self.confirm(preview), self.return_to)
        self.assertEqual(DecisaoPreAuditoria.objects.count(), 1)

    def test_expired_confirmation_preserves_text_and_selection_without_renewing_token(self):
        preview = self.preview()
        reason = "Justificativa preservada mesmo após expiração da confirmação."
        with patch("django.core.signing.time.time", return_value=timezone.now().timestamp() + 1801):
            response = self.confirm(preview, justificativa=reason)
        self.assertContains(response, reason, status_code=409)
        self.assertEqual(len(response.context["questoes"]), 2)
        self.assertEqual(response.context["confirmacao"], preview.context["confirmacao"])
        self.assertNotContains(response, "CONFIRMAR APROVAÇÃO DE", status_code=409)
        self.assert_untouched()

    def test_archived_period_after_preview_preserves_text_without_allowing_approval(self):
        preview = self.preview()
        Periodo.objects.filter(pk=self.component.periodo_id).update(ativo=False)
        reason = "Justificativa preservada após arquivamento do período acadêmico."
        response = self.confirm(preview, justificativa=reason)
        self.assertContains(response, reason, status_code=409)
        self.assertEqual(len(response.context["questoes"]), 2)
        self.assertNotContains(response, "CONFIRMAR APROVAÇÃO DE", status_code=409)
        self.assert_untouched()
