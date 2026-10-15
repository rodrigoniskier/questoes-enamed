import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from questoes.ai_control import AIControlError
from questoes.models import Alternativa, ComponenteCurricular, Periodo, Questao, Semestre
from questoes.submission import ASSERTION_ANSWERS

from .models import (
    AlteracaoPreAuditoria,
    DecisaoPreAuditoria,
    EvidenciaRevisor,
    LotePreAuditoria,
    ParecerPreAuditoria,
    SolicitacaoCorrecao,
)
from .services import executar_pre_auditoria, fingerprint_questao


class PreAuditoriaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        semester = Semestre.objects.create(nome="2026.2")
        period = Periodo.objects.create(nome="2º Período", semestre=semester)
        cls.component = ComponenteCurricular.objects.create(nome="Clínica", periodo=period)
        cls.question = Questao.objects.create(
            componente=cls.component,
            professor_nome="Docente de teste",
            professor_email="docente@example.com",
            tipo_questao="RESPOSTA_UNICA",
            texto_base="Contexto clínico sintético.",
            enunciado="Qual a conduta?",
            justificativa="A alternativa A é correta; as demais são inadequadas.",
        )
        for index in range(4):
            Alternativa.objects.create(
                questao=cls.question, texto=f"Opção {index + 1}", eh_correta=index == 0
            )
        cls.reviewer = get_user_model().objects.create_user("reviewer")
        permission = Permission.objects.get(codename="view_parecerpreauditoria")
        review_permission = Permission.objects.get(codename="review_parecerpreauditoria")
        cls.reviewer.user_permissions.add(permission, review_permission)

    def test_requires_login_and_reviewer_permission(self):
        url = reverse("pre_auditoria:lista")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response.url)
        self.assertIn("next=", response.url)
        user = get_user_model().objects.create_user("ordinary")
        self.client.force_login(user)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_active_semester_queue_excludes_historical_records(self):
        old = Semestre.objects.create(nome="2026.1", ativo=False)
        old_period = Periodo.objects.create(nome="2º Período", semestre=old)
        old_component = ComponenteCurricular.objects.create(nome="Histórico", periodo=old_period)
        Questao.objects.create(
            componente=old_component,
            professor_nome="Docente antigo",
            professor_email="antigo@example.com",
            enunciado="Questão arquivada",
            justificativa="Justificativa",
        )
        self.client.force_login(self.reviewer)
        response = self.client.get(reverse("pre_auditoria:lista"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Qual a conduta?")
        self.assertNotContains(response, "Questão arquivada")

    def test_clean_structural_result_is_ready_without_gemini(self):
        self.client.force_login(self.reviewer)
        response = self.client.post(reverse("pre_auditoria:iniciar", args=[self.question.pk]))
        self.assertRedirects(response, reverse("pre_auditoria:detalhe", args=[self.question.pk]))
        report = ParecerPreAuditoria.objects.get(questao=self.question)
        self.assertEqual(report.resultado, ParecerPreAuditoria.Resultado.APROVADA)
        self.assertEqual(report.indicadores["politica"], "submeter-basica-v1")
        self.assertFalse(report.indicadores["semantica_verificada"])
        self.assertNotIn("inconclusivos", report.indicadores)
        self.assertEqual(self.question.status, "PENDENTE")

    def test_read_only_reviewer_cannot_start_audit(self):
        self.reviewer.user_permissions.remove(Permission.objects.get(codename="review_parecerpreauditoria"))
        self.client.force_login(self.reviewer)
        response = self.client.post(reverse("pre_auditoria:iniciar", args=[self.question.pk]))
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ParecerPreAuditoria.objects.exists())

    def test_fingerprint_changes_when_question_content_changes(self):
        before = fingerprint_questao(self.question)
        self.question.enunciado = "Comando alterado?"
        self.question.save()
        self.assertNotEqual(before, fingerprint_questao(self.question))

    def test_repeating_same_audit_request_id_does_not_duplicate_report(self):
        request_id = uuid.uuid4()
        first = executar_pre_auditoria(self.question, semantica=False, request_id=str(request_id))
        second = executar_pre_auditoria(self.question, semantica=False, request_id=str(request_id))
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(ParecerPreAuditoria.objects.count(), 1)

    def test_editor_saves_content_records_history_and_rechecks_structure(self):
        historical = ParecerPreAuditoria.objects.create(
            questao=self.question,
            resultado=ParecerPreAuditoria.Resultado.INCONCLUSIVA,
            fingerprint=fingerprint_questao(self.question),
            achados=[{"criterio": "manual.13", "gravidade": "mandatory"}],
            indicadores={"inconclusivos": [{"criterion_id": "13", "reason": "Política anterior"}]},
        )
        self.client.force_login(self.reviewer)
        data = {
            "fingerprint": fingerprint_questao(self.question),
            "tipo_questao": "RESPOSTA_UNICA",
            "uso_prova": "INTEGRADA",
            "texto_base": "Contexto revisado.",
            "enunciado": "Qual é a conduta revisada?",
            "proposicao_dois": "",
            "justificativa": "Justificativa revisada.",
            "comentario_validacao": "Ajuste de clareza.",
            "alternativas-TOTAL_FORMS": "5",
            "alternativas-INITIAL_FORMS": "4",
            "alternativas-MIN_NUM_FORMS": "0",
            "alternativas-MAX_NUM_FORMS": "5",
        }
        for index, alternative in enumerate(self.question.alternativas.order_by("pk")):
            data[f"alternativas-{index}-id"] = str(alternative.pk)
            data[f"alternativas-{index}-texto"] = f"Opção revisada {index + 1}"
        data["alternativas-0-eh_correta"] = "on"
        response = self.client.post(reverse("pre_auditoria:editar", args=[self.question.pk]), data)
        self.assertRedirects(response, reverse("pre_auditoria:detalhe", args=[self.question.pk]))
        self.question.refresh_from_db()
        self.assertEqual(self.question.enunciado, "Qual é a conduta revisada?")
        self.assertEqual(self.question.status, "PENDENTE")
        self.assertEqual(AlteracaoPreAuditoria.objects.count(), 1)
        self.assertEqual(ParecerPreAuditoria.objects.count(), 2)
        historical.refresh_from_db()
        self.assertEqual(historical.resultado, ParecerPreAuditoria.Resultado.INCONCLUSIVA)
        self.assertEqual(historical.achados[0]["criterio"], "manual.13")
        current = ParecerPreAuditoria.objects.exclude(pk=historical.pk).get()
        self.assertEqual(current.fingerprint, fingerprint_questao(self.question))
        self.assertEqual(current.indicadores["politica"], "submeter-basica-v1")

    def test_editor_rejects_stale_fingerprint_without_overwriting(self):
        self.client.force_login(self.reviewer)
        response = self.client.post(
            reverse("pre_auditoria:editar", args=[self.question.pk]),
            {"fingerprint": "stale-version"},
        )
        self.assertEqual(response.status_code, 409)
        self.question.refresh_from_db()
        self.assertEqual(self.question.enunciado, "Qual a conduta?")
        self.assertFalse(AlteracaoPreAuditoria.objects.exists())

    def test_editor_rejects_invalid_gabarito_without_persisting(self):
        self.client.force_login(self.reviewer)
        data = {
            "fingerprint": fingerprint_questao(self.question),
            "tipo_questao": "RESPOSTA_UNICA",
            "uso_prova": "INTEGRADA",
            "texto_base": "Contexto revisado.",
            "enunciado": "Qual é a conduta?",
            "proposicao_dois": "",
            "justificativa": "Justificativa revisada.",
            "comentario_validacao": "",
            "alternativas-TOTAL_FORMS": "5",
            "alternativas-INITIAL_FORMS": "4",
            "alternativas-MIN_NUM_FORMS": "0",
            "alternativas-MAX_NUM_FORMS": "5",
        }
        for index, alternative in enumerate(self.question.alternativas.order_by("pk")):
            data[f"alternativas-{index}-id"] = str(alternative.pk)
            data[f"alternativas-{index}-texto"] = alternative.texto
        response = self.client.post(reverse("pre_auditoria:editar", args=[self.question.pk]), data)
        self.assertEqual(response.status_code, 200)
        self.question.refresh_from_db()
        self.assertEqual(self.question.enunciado, "Qual a conduta?")
        self.assertFalse(AlteracaoPreAuditoria.objects.exists())

    @override_settings(GEMINI_API_KEY="synthetic-test-key")
    @patch(
        "pre_auditoria.services.generate_text",
        return_value='{"recommendations":[{"criterion_id":"alternativas","evidence":"Opção 1","reason":"A opção pode ser mais concisa.","recommendation":"Revise a extensão."}]}',
    )
    def test_semantic_recommendation_is_not_a_blocking_pendency(self, generate):
        report = executar_pre_auditoria(self.question, semantica=True)
        self.assertEqual(report.resultado, ParecerPreAuditoria.Resultado.APROVADA)
        self.assertTrue(report.indicadores["semantica_verificada"])
        self.assertEqual(report.achados[0]["gravidade"], "recomendacao")
        generate.assert_called_once()
        self.client.force_login(self.reviewer)
        self.client.post(reverse("pre_auditoria:aprovar", args=[self.question.pk]))
        self.question.refresh_from_db()
        self.assertEqual(self.question.status, "APROVADA")

    def test_approval_needs_no_bibliography_bloom_or_objectives(self):
        report = executar_pre_auditoria(self.question, revisor=self.reviewer, semantica=False)
        self.assertEqual(report.resultado, ParecerPreAuditoria.Resultado.APROVADA)
        self.assertFalse(report.indicadores["semantica_verificada"])
        self.assertNotIn("inconclusivos", report.indicadores)
        self.assertNotIn("bloom", report.indicadores)
        self.assertNotIn("objetivos", report.indicadores)
        self.client.force_login(self.reviewer)
        url = reverse("pre_auditoria:aprovar", args=[self.question.pk])
        with self.captureOnCommitCallbacks(execute=True):
            approved = self.client.post(url)
        self.assertRedirects(approved, reverse("pre_auditoria:detalhe", args=[self.question.pk]))
        self.question.refresh_from_db()
        report.refresh_from_db()
        self.assertEqual(self.question.status, "APROVADA")
        self.assertEqual(report.resultado, ParecerPreAuditoria.Resultado.APROVADA)
        self.assertEqual(DecisaoPreAuditoria.objects.get().revisor, self.reviewer)
        self.question.refresh_from_db()
        self.assertEqual(self.question.notified_status, "APROVADA")
        self.assertEqual(len(mail.outbox), 1)

    def test_approved_question_is_visible_to_admin_submeter_and_generator(self):
        executar_pre_auditoria(self.question, revisor=self.reviewer, semantica=False)
        pending = Questao.objects.create(
            componente=self.component,
            professor_nome="Docente pendente",
            professor_email="pendente@example.com",
            texto_base="Outro contexto.",
            enunciado="Questão ainda pendente?",
            justificativa="Justificativa.",
        )
        for index in range(4):
            Alternativa.objects.create(questao=pending, texto=f"Pendente {index}", eh_correta=index == 0)
        self.client.force_login(self.reviewer)
        self.client.post(reverse("pre_auditoria:aprovar", args=[self.question.pk]))
        self.question.refresh_from_db()
        self.assertEqual(self.question.status, "APROVADA")

        administrator = get_user_model().objects.create_superuser(
            "admin-review", "admin@example.com", "test-only-password"
        )
        self.client.force_login(administrator)
        admin_page = self.client.get(reverse("admin:questoes_questao_changelist"))
        self.assertContains(admin_page, "APROVADA")

        submeter_page = self.client.get(
            reverse("submeter_visualizar_questoes"), {"componente": self.component.pk}
        )
        self.assertContains(submeter_page, "APROVADA")
        self.assertContains(submeter_page, "PENDENTE")

        generated = self.client.get(
            reverse("prova_gerada"),
            {"componentes": [self.component.pk], f"quantidade_{self.component.pk}": "1"},
        )
        self.assertEqual(generated.status_code, 200)
        self.assertContains(generated, self.question.enunciado)
        self.assertNotContains(generated, pending.enunciado)

    def test_stale_report_cannot_approve_changed_content(self):
        executar_pre_auditoria(self.question, revisor=self.reviewer, semantica=False)
        self.question.enunciado = "Versão nova da questão?"
        self.question.save()
        self.client.force_login(self.reviewer)
        self.client.post(reverse("pre_auditoria:aprovar", args=[self.question.pk]))
        self.question.refresh_from_db()
        self.assertEqual(self.question.status, "PENDENTE")
        self.assertFalse(DecisaoPreAuditoria.objects.exists())

    def test_batch_approval_accepts_structurally_valid_reports(self):
        executar_pre_auditoria(self.question, revisor=self.reviewer, semantica=False)
        second = Questao.objects.create(
            componente=self.component,
            professor_nome="Segundo docente",
            professor_email="segundo@example.com",
            texto_base="Contexto clínico.",
            enunciado="Questão de teste?",
            justificativa="Justificativa.",
        )
        for index in range(4):
            Alternativa.objects.create(questao=second, texto=f"Resposta {index}", eh_correta=index == 0)
        executar_pre_auditoria(second, revisor=self.reviewer, semantica=False)
        self.client.force_login(self.reviewer)
        response = self.client.post(
            reverse("pre_auditoria:aprovar_lote"),
            {"questoes": [str(self.question.pk), str(second.pk)]},
        )
        self.assertRedirects(response, reverse("pre_auditoria:lista"))
        self.question.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(self.question.status, "APROVADA")
        self.assertEqual(second.status, "APROVADA")
        self.assertEqual(DecisaoPreAuditoria.objects.count(), 2)

    def test_signed_correction_link_is_single_use_and_cannot_approve(self):
        report = executar_pre_auditoria(self.question, revisor=self.reviewer, semantica=False)
        self.client.force_login(self.reviewer)
        response = self.client.post(
            reverse("pre_auditoria:solicitar_correcao", args=[self.question.pk]),
            {"mensagem": "Esclareça a justificativa e revise a alternativa C."},
        )
        self.assertRedirects(response, reverse("pre_auditoria:detalhe", args=[self.question.pk]))
        correction = SolicitacaoCorrecao.objects.get(questao=self.question)
        self.assertEqual(correction.parecer_id, report.pk)
        self.assertEqual(len(mail.outbox), 1)
        link = mail.outbox[0].body.split("/app/pre-auditoria/correcao/")[1].split()[0]
        self.assertNotIn(self.question.professor_email, link)
        correction_url = f"/app/pre-auditoria/correcao/{link}"
        self.client.logout()
        form_page = self.client.get(correction_url)
        self.assertEqual(form_page.status_code, 200)

        data = {
            "tipo_questao": self.question.tipo_questao,
            "uso_prova": self.question.uso_prova,
            "texto_base": "Contexto clínico revisado.",
            "enunciado": "Qual a conduta atualizada?",
            "proposicao_dois": "",
            "justificativa": "Justificativa atualizada.",
            "alternativas-TOTAL_FORMS": "5",
            "alternativas-INITIAL_FORMS": "4",
            "alternativas-MIN_NUM_FORMS": "0",
            "alternativas-MAX_NUM_FORMS": "5",
        }
        for index, alternative in enumerate(self.question.alternativas.order_by("pk")):
            data[f"alternativas-{index}-id"] = str(alternative.pk)
            data[f"alternativas-{index}-texto"] = f"Resposta revisada {index + 1}"
        data["alternativas-0-eh_correta"] = "on"
        submitted = self.client.post(correction_url, data)
        self.assertContains(submitted, "Correção reenviada")
        self.question.refresh_from_db()
        correction.refresh_from_db()
        self.assertEqual(self.question.status, "PENDENTE")
        self.assertIsNotNone(correction.utilizada_em)
        self.assertEqual(
            self.question.alteracoes_pre_auditoria.get().origem, AlteracaoPreAuditoria.Origem.PROFESSOR
        )
        self.assertEqual(self.client.get(correction_url).status_code, 410)
        self.assertEqual(DecisaoPreAuditoria.objects.get().acao, DecisaoPreAuditoria.Acao.CORRECAO)

    def test_semester_2026_2_single_answer_uses_four_options(self):
        report = executar_pre_auditoria(self.question, revisor=self.reviewer)
        self.assertEqual(report.resultado, ParecerPreAuditoria.Resultado.APROVADA)
        self.assertEqual(report.achados, [])

    def test_historical_semester_keeps_five_single_answer_options(self):
        historical_semester = Semestre.objects.create(nome="2026.1")
        historical_period = Periodo.objects.create(nome="2º Período", semestre=historical_semester)
        historical_component = ComponenteCurricular.objects.create(
            nome="Clínica histórica", periodo=historical_period
        )
        self.question.componente = historical_component
        self.question.save()
        for index in range(4, 5):
            Alternativa.objects.create(questao=self.question, texto=f"Opção {index + 1}", eh_correta=False)

        report = executar_pre_auditoria(self.question, semantica=False)

        self.assertEqual(report.resultado, ParecerPreAuditoria.Resultado.APROVADA)
        self.assertEqual(self.question.alternativas.count(), 5)

    def test_multiple_choice_requires_true_and_false_statements(self):
        self.question.tipo_questao = "MULTIPLA_ESCOLHA"
        self.question.save()
        self.question.alternativas.all().delete()
        for index in range(3):
            Alternativa.objects.create(questao=self.question, texto=f"Afirmação {index}", eh_correta=True)
        report = executar_pre_auditoria(self.question)
        self.assertEqual(report.resultado, ParecerPreAuditoria.Resultado.PENDENCIAS)
        self.assertIn("resposta-multipla.distribuicao", {finding["criterio"] for finding in report.achados})

    def test_single_answer_blocks_wrong_count_and_duplicate_options(self):
        alternatives = list(self.question.alternativas.order_by("pk"))
        alternatives[-1].texto = alternatives[0].texto
        alternatives[-1].save()
        Alternativa.objects.create(questao=self.question, texto="Opção extra", eh_correta=False)

        report = executar_pre_auditoria(self.question, semantica=False)
        finding_ids = {finding["criterio"] for finding in report.achados}

        self.assertEqual(report.resultado, ParecerPreAuditoria.Resultado.PENDENCIAS)
        self.assertIn("alternativas", finding_ids)
        self.assertIn("resposta-unica.quantidade", finding_ids)

    def test_assertion_reason_blocks_missing_proposition_and_gabarito(self):
        self.question.tipo_questao = "ASSERCAO_RAZAO"
        self.question.proposicao_dois = ""
        self.question.save()
        self.question.alternativas.all().delete()
        for letter, text in ASSERTION_ANSWERS.items():
            Alternativa.objects.create(questao=self.question, texto=f"{letter}) {text}", eh_correta=False)

        report = executar_pre_auditoria(self.question, semantica=False)
        finding_ids = {finding["criterio"] for finding in report.achados}

        self.assertEqual(report.resultado, ParecerPreAuditoria.Resultado.PENDENCIAS)
        self.assertIn("assercao-razao.proposicao", finding_ids)
        self.assertIn("assercao-razao.gabarito", finding_ids)

    def test_audit_batch_persists_and_advances_one_question_per_request(self):
        second = Questao.objects.create(
            componente=self.component,
            professor_nome="Segundo docente",
            professor_email="segundo@example.com",
            texto_base="Contexto dois.",
            enunciado="Questão dois?",
            justificativa="Justificativa dois.",
        )
        for index in range(4):
            Alternativa.objects.create(questao=second, texto=f"Resposta {index}", eh_correta=index == 0)
        self.client.force_login(self.reviewer)
        created = self.client.post(
            reverse("pre_auditoria:criar_lote"),
            {"questoes": [str(self.question.pk), str(second.pk)]},
        )
        batch = LotePreAuditoria.objects.get()
        self.assertRedirects(created, reverse("pre_auditoria:detalhe_lote", args=[batch.pk]))
        self.assertEqual(batch.posicao, 0)
        self.assertContains(self.client.get(created.url), "0 de 2")

        step_url = reverse("pre_auditoria:processar_proximo", args=[batch.pk])
        first = self.client.post(step_url)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["posicao"], 1)
        self.assertEqual(first.json()["resultado"], "Apta para aprovação")
        batch.refresh_from_db()
        self.assertEqual(batch.status, LotePreAuditoria.Status.AGUARDANDO)
        self.assertEqual(ParecerPreAuditoria.objects.count(), 1)

        second_step = self.client.post(step_url)
        self.assertEqual(second_step.status_code, 200)
        self.assertTrue(second_step.json()["concluido"])
        batch.refresh_from_db()
        self.assertEqual(batch.status, LotePreAuditoria.Status.CONCLUIDO)
        self.assertEqual(batch.posicao, 2)
        self.assertEqual(ParecerPreAuditoria.objects.count(), 2)

    def test_audit_batch_rejects_archived_or_approved_questions(self):
        self.question.status = "APROVADA"
        self.question.save()
        self.client.force_login(self.reviewer)
        response = self.client.post(
            reverse("pre_auditoria:criar_lote"), {"questoes": [str(self.question.pk)]}
        )
        self.assertRedirects(response, reverse("pre_auditoria:lista"))
        self.assertFalse(LotePreAuditoria.objects.exists())

    def test_semantic_analysis_is_not_requested_by_default(self):
        self.client.force_login(self.reviewer)
        with patch("pre_auditoria.services.generate_text") as generate:
            response = self.client.post(reverse("pre_auditoria:iniciar", args=[self.question.pk]))
        self.assertEqual(response.status_code, 302)
        report = ParecerPreAuditoria.objects.get(questao=self.question)
        self.assertEqual(report.resultado, ParecerPreAuditoria.Resultado.APROVADA)
        self.assertFalse(report.indicadores["semantica_verificada"])
        generate.assert_not_called()

    @override_settings(GEMINI_API_KEY="synthetic-test-key")
    @patch("pre_auditoria.services.generate_text")
    def test_direct_personal_identifiers_block_paid_semantic_call(self, generate):
        self.question.texto_base = "Paciente: Ana de Souza, CPF 123.456.789-00, apresenta febre."
        self.question.save()
        report = executar_pre_auditoria(self.question, semantica=True)
        generate.assert_not_called()
        self.assertFalse(report.indicadores["semantica_verificada"])
        self.assertIn(
            "identificadores pessoais",
            report.indicadores["analise_semantica_aviso"],
        )

    @override_settings(GEMINI_API_KEY="synthetic-test-key")
    @patch("pre_auditoria.services.generate_text", side_effect=AIControlError("Cota de teste indisponível."))
    def test_gemini_failure_warns_without_blocking_approval(self, generate):
        report = executar_pre_auditoria(self.question, semantica=True)
        generate.assert_called_once()
        self.assertFalse(report.indicadores["semantica_verificada"])
        self.assertTrue(report.indicadores["analise_semantica_aviso"])
        self.assertEqual(report.resultado, ParecerPreAuditoria.Resultado.APROVADA)
        self.client.force_login(self.reviewer)
        self.client.post(reverse("pre_auditoria:aprovar", args=[self.question.pk]))
        self.question.refresh_from_db()
        self.assertEqual(self.question.status, "APROVADA")

    def test_historical_report_is_preserved_and_requires_a_new_audit(self):
        historical = ParecerPreAuditoria.objects.create(
            questao=self.question,
            resultado=ParecerPreAuditoria.Resultado.INCONCLUSIVA,
            fingerprint=fingerprint_questao(self.question),
            achados=[{"criterio": "manual.10", "gravidade": "mandatory"}],
            indicadores={"inconclusivos": [{"criterion_id": "10", "reason": "Conferência anterior"}]},
        )
        evidence = EvidenciaRevisor.objects.create(
            parecer=historical,
            criterio_id="10",
            evidencia="Registro histórico de conferência bibliográfica.",
            referencia="Acervo consultado na revisão anterior.",
            revisor=self.reviewer,
        )
        self.client.force_login(self.reviewer)
        self.client.post(reverse("pre_auditoria:aprovar", args=[self.question.pk]))
        self.question.refresh_from_db()
        historical.refresh_from_db()
        self.assertEqual(self.question.status, "PENDENTE")
        self.assertEqual(historical.resultado, ParecerPreAuditoria.Resultado.INCONCLUSIVA)
        self.assertTrue(EvidenciaRevisor.objects.filter(pk=evidence.pk).exists())

        current = executar_pre_auditoria(self.question, revisor=self.reviewer)
        self.assertEqual(current.resultado, ParecerPreAuditoria.Resultado.APROVADA)
        response = self.client.get(reverse("pre_auditoria:detalhe", args=[self.question.pk]))
        self.assertContains(response, "Política anterior")
        self.client.post(reverse("pre_auditoria:aprovar", args=[self.question.pk]))
        self.question.refresh_from_db()
        self.assertEqual(self.question.status, "APROVADA")

    @override_settings(GEMINI_API_KEY="synthetic-test-key")
    @patch(
        "pre_auditoria.services.generate_text",
        return_value='{"recommendations":[{"criterion_id":"alternativas","evidence":"texto inventado","reason":"achado","recommendation":"rever"}]}',
    )
    def test_unverifiable_ai_evidence_invalidates_the_semantic_response(self, generate):
        report = executar_pre_auditoria(self.question, semantica=True)
        generate.assert_called_once()
        self.assertFalse(report.indicadores["semantica_verificada"])
        self.assertFalse(report.achados)
        self.assertTrue(report.indicadores["analise_semantica_aviso"])

    @override_settings(GEMINI_API_KEY="synthetic-test-key")
    @patch("pre_auditoria.services.generate_text")
    def test_ai_is_skipped_when_structural_failures_exist(self, generate):
        self.question.tipo_questao = "ASSERCAO_RAZAO"
        self.question.proposicao_dois = ""
        self.question.save()
        report = executar_pre_auditoria(self.question, semantica=True)
        generate.assert_not_called()
        self.assertIn("assercao-razao.proposicao", {finding["criterio"] for finding in report.achados})
        self.assertEqual(report.resultado, ParecerPreAuditoria.Resultado.PENDENCIAS)

    def test_assertion_reason_requires_the_canonical_five_response_key(self):
        self.question.tipo_questao = "ASSERCAO_RAZAO"
        self.question.proposicao_dois = "A proposição II explica a proposição I."
        self.question.save()
        self.question.alternativas.all().delete()
        for letter, text in ASSERTION_ANSWERS.items():
            Alternativa.objects.create(
                questao=self.question,
                texto=f"{letter}) {text}",
                eh_correta=letter == "B",
            )
        report = executar_pre_auditoria(self.question, semantica=False)
        self.assertNotIn("assercao-razao.chave-padrao", {finding["criterio"] for finding in report.achados})
