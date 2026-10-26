import uuid
from datetime import timedelta
from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from questoes.ai_control import AIControlError
from questoes.models import Alternativa, ComponenteCurricular, Periodo, Questao, Semestre
from questoes.submission import ASSERTION_ANSWERS

from .models import (
    AlteracaoPreAuditoria,
    DecisaoPreAuditoria,
    EvidenciaRevisor,
    LotePreAuditoria,
    OperacaoLimpezaPreAuditoria,
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
        self.assertContains(response, "Questão não auditada")

    def test_dashboard_filters_and_paginates_question_cards(self):
        for index in range(26):
            Questao.objects.create(
                componente=self.component,
                professor_nome="Docente de teste",
                professor_email="docente@example.com",
                texto_base="Contexto de teste.",
                enunciado=f"Questão paginada {index}.",
                justificativa="Justificativa.",
            )
        self.client.force_login(self.reviewer)
        response = self.client.get(
            reverse("pre_auditoria:lista"),
            {
                "semestre": self.component.periodo.semestre_id,
                "periodo": self.component.periodo_id,
                "componente": self.component.pk,
                "page": 2,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["questoes"].number, 2)
        self.assertEqual(len(response.context["questoes"].object_list), 2)
        self.assertEqual(response.context["total_submetidas"], 27)
        self.assertContains(response, f"semestre={self.component.periodo.semestre_id}")
        self.assertContains(response, "Selecionar para auditoria em lote")

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

    def test_preview_single_answer_matches_print_letters_without_exposing_key(self):
        self.client.force_login(self.reviewer)
        response = self.client.get(reverse("pre_auditoria:detalhe", args=[self.question.pk]))
        self.assertEqual(response.status_code, 200)
        preview = (
            response.content.decode()
            .split('<section class="question-print-preview"', 1)[1]
            .split("</section>", 1)[0]
        )
        for letter in ("A)", "B)", "C)", "D)"):
            self.assertIn(letter, preview)
        self.assertNotIn("gabarito cadastrado", preview)
        self.assertNotIn("(gabarito)", preview)

    def test_preview_multiple_choice_includes_stable_generated_final_options(self):
        self.question.tipo_questao = "MULTIPLA_ESCOLHA"
        self.question.save()
        self.question.alternativas.all().delete()
        for index in range(3):
            Alternativa.objects.create(
                questao=self.question,
                texto=f"{'I' if index == 0 else 'II' if index == 1 else 'III'}. Afirmação {index + 1}",
                eh_correta=index == 0,
            )
        self.client.force_login(self.reviewer)
        first = self.client.get(reverse("pre_auditoria:detalhe", args=[self.question.pk]))
        second = self.client.get(reverse("pre_auditoria:detalhe", args=[self.question.pk]))
        self.assertEqual(first.status_code, 200)
        preview = (
            first.content.decode()
            .split('<section class="question-print-preview"', 1)[1]
            .split("</section>", 1)[0]
        )
        self.assertIn("Considerando o texto apresentado", preview)
        self.assertIn("I.", preview)
        self.assertIn("Apenas a afirmativa I está correta.", preview)
        self.assertEqual(
            first.context["questao"].alternativas_finais_preview,
            second.context["questao"].alternativas_finais_preview,
        )
        self.assertEqual(len(first.context["questao"].alternativas_finais_preview), 4)
        self.assertNotIn("gabarito cadastrado", preview)

    def test_preview_assertion_reason_uses_two_propositions_and_five_options(self):
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
        self.client.force_login(self.reviewer)
        response = self.client.get(reverse("pre_auditoria:detalhe", args=[self.question.pk]))
        preview = (
            response.content.decode()
            .split('<section class="question-print-preview"', 1)[1]
            .split("</section>", 1)[0]
        )
        self.assertIn("PORQUE", preview)
        self.assertIn("<strong>II.</strong>", preview)
        self.assertIn("A proposição II explica a proposição I.", preview)
        self.assertEqual(len(response.context["questao"].alternativas_finais_preview), 5)
        self.assertNotIn("gabarito cadastrado", preview)

    def test_preview_shows_existing_image_and_reports_missing_image(self):
        image_buffer = BytesIO()
        Image.new("RGB", (2, 2), color="white").save(image_buffer, format="PNG")
        image = SimpleUploadedFile("tiny.png", image_buffer.getvalue(), content_type="image/png")
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            self.question.imagem = image
            self.question.save()
            self.client.force_login(self.reviewer)
            available = self.client.get(reverse("pre_auditoria:detalhe", args=[self.question.pk]))
            self.assertContains(available, "Imagem vinculada à questão")
            self.assertTrue(available.context["questao"].preview_image_available)

            Questao.objects.filter(pk=self.question.pk).update(imagem="imagens_questoes/arquivo-ausente.png")
            missing = self.client.get(reverse("pre_auditoria:detalhe", args=[self.question.pk]))
            self.assertContains(missing, "Imagem indisponível")
            self.assertFalse(missing.context["questao"].preview_image_available)

    def test_dashboard_and_detail_show_answer_and_full_justification_without_writes(self):
        self.question.alternativas.all().update(eh_correta=False)
        answer = self.question.alternativas.order_by("pk")[2]
        answer.eh_correta = True
        answer.save()
        self.question.justificativa = "C) CERTA. Justificativa completa.\nA, B e D) ERRADAS. Motivos."
        self.question.save()
        before = list(self.question.alternativas.values_list("pk", "texto", "eh_correta"))
        self.client.force_login(self.reviewer)
        for url in (
            reverse("pre_auditoria:lista"),
            reverse("pre_auditoria:detalhe", args=[self.question.pk]),
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertContains(response, "Gabarito: C)")
                self.assertContains(response, "Opção 3")
                self.assertContains(
                    response, "C) CERTA. Justificativa completa.<br>A, B e D) ERRADAS. Motivos."
                )
                self.assertContains(response, "RESULTADO DA PRÉ-AUDITORIA")
                self.assertContains(response, 'class="audit-answer-review no-print"', count=1)
        self.question.refresh_from_db()
        self.assertEqual(self.question.status, "PENDENTE")
        self.assertEqual(before, list(self.question.alternativas.values_list("pk", "texto", "eh_correta")))
        self.assertFalse(ParecerPreAuditoria.objects.exists())
        self.assertFalse(DecisaoPreAuditoria.objects.exists())

    def test_multiple_choice_answer_matches_generated_preview_on_dashboard_and_detail(self):
        self.question.tipo_questao = "MULTIPLA_ESCOLHA"
        self.question.save()
        self.question.alternativas.all().delete()
        for index in range(3):
            Alternativa.objects.create(
                questao=self.question, texto=f"Afirmação {index + 1}", eh_correta=index in (0, 2)
            )
        self.client.force_login(self.reviewer)
        for url in (
            reverse("pre_auditoria:lista"),
            reverse("pre_auditoria:detalhe", args=[self.question.pk]),
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                question = response.context.get("questao") or response.context["questoes"][0]
                index = next(
                    i
                    for i, alternative in enumerate(question.alternativas_finais_preview)
                    if alternative["eh_correta"]
                )
                letter = "ABCD"[index]
                self.assertContains(response, f"Gabarito: {letter})", count=1)
                self.assertContains(response, "Apenas as afirmativas I e III estão corretas.")
                self.assertNotContains(response, "Mais de uma alternativa marcada")

    def test_assertion_reason_answer_uses_preview_letter_without_repeating_prefix(self):
        self.question.tipo_questao = "ASSERCAO_RAZAO"
        self.question.proposicao_dois = "Segunda proposição."
        self.question.save()
        self.question.alternativas.all().delete()
        for letter, text in ASSERTION_ANSWERS.items():
            Alternativa.objects.create(
                questao=self.question, texto=f"{letter}) {text}", eh_correta=letter == "D"
            )
        self.client.force_login(self.reviewer)
        for url in (
            reverse("pre_auditoria:lista"),
            reverse("pre_auditoria:detalhe", args=[self.question.pk]),
        ):
            response = self.client.get(url)
            self.assertContains(
                response, f"<strong>Gabarito: D)</strong> {ASSERTION_ANSWERS['D']}", html=True
            )
            self.assertNotContains(response, "Gabarito: D)</strong> D)")

    def test_missing_or_ambiguous_answers_and_blank_justification_are_explicit(self):
        self.client.force_login(self.reviewer)
        self.question.justificativa = "   \n "
        self.question.save()
        self.question.alternativas.all().update(eh_correta=False)
        for url in (
            reverse("pre_auditoria:lista"),
            reverse("pre_auditoria:detalhe", args=[self.question.pk]),
        ):
            response = self.client.get(url)
            self.assertContains(response, "Gabarito não informado ou inválido.")
            self.assertContains(response, "Justificativa não informada.")
        self.question.alternativas.all().update(eh_correta=True)
        response = self.client.get(reverse("pre_auditoria:lista"))
        self.assertContains(response, "Mais de uma alternativa marcada como correta.")
        self.assertContains(response, "Gabarito:", count=4)

    def test_answer_review_escapes_content_and_requires_reviewer_access(self):
        self.question.justificativa = '<script>alert("justificativa")</script>\nTexto preservado.'
        self.question.save()
        self.client.force_login(self.reviewer)
        for url in (
            reverse("pre_auditoria:lista"),
            reverse("pre_auditoria:detalhe", args=[self.question.pk]),
        ):
            response = self.client.get(url)
            self.assertContains(response, "&lt;script&gt;")
            self.assertNotContains(response, '<script>alert("justificativa")</script>')
        ordinary = get_user_model().objects.create_user("without-review-access")
        self.client.force_login(ordinary)
        for url in (
            reverse("pre_auditoria:lista"),
            reverse("pre_auditoria:detalhe", args=[self.question.pk]),
        ):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 403)
            self.assertNotContains(response, "Texto preservado.", status_code=403)

    def test_checklist_uses_audit_data_for_conforming_and_pending_criteria(self):
        self.client.force_login(self.reviewer)
        report = executar_pre_auditoria(self.question, revisor=self.reviewer)
        response = self.client.get(reverse("pre_auditoria:detalhe", args=[self.question.pk]))
        self.assertContains(response, "✓ Conforme")
        self.assertEqual(
            response.context["questao"].audit_checklist,
            report.indicadores["validacoes_estruturais"],
        )

        self.question.justificativa = ""
        self.question.save()
        pending_report = executar_pre_auditoria(self.question, revisor=self.reviewer)
        pending = self.client.get(reverse("pre_auditoria:detalhe", args=[self.question.pk]))
        self.assertContains(pending, "! Pendente")
        self.assertContains(pending, "justificativa")
        self.assertTrue(
            any(not item["conforme"] for item in pending_report.indicadores["validacoes_estruturais"])
        )

    def test_read_only_reviewer_cannot_start_audit(self):
        self.reviewer.user_permissions.remove(Permission.objects.get(codename="review_parecerpreauditoria"))
        self.client.force_login(self.reviewer)
        response = self.client.post(reverse("pre_auditoria:iniciar", args=[self.question.pk]))
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ParecerPreAuditoria.objects.exists())
        cleanup = self.client.post(
            reverse("pre_auditoria:excluir_auditorias"),
            {"questao_ids": str(self.question.pk)},
        )
        self.assertEqual(cleanup.status_code, 403)
        self.assertFalse(OperacaoLimpezaPreAuditoria.objects.exists())

    def test_cleanup_requires_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.reviewer)
        response = client.post(
            reverse("pre_auditoria:excluir_auditorias"),
            {"parecer_ids": "1"},
        )
        self.assertEqual(response.status_code, 403)

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
        self.client.post(
            reverse("pre_auditoria:aprovar", args=[self.question.pk]),
            {"confirmar_aprovacao": "on"},
        )
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
            approved = self.client.post(url, {"confirmar_aprovacao": "on"})
        self.assertRedirects(approved, reverse("pre_auditoria:detalhe", args=[self.question.pk]))
        self.question.refresh_from_db()
        report.refresh_from_db()
        self.assertEqual(self.question.status, "APROVADA")
        self.assertEqual(report.resultado, ParecerPreAuditoria.Resultado.APROVADA)
        decision = DecisaoPreAuditoria.objects.get()
        self.assertEqual(decision.revisor, self.reviewer)
        self.assertFalse(decision.aprovacao_excepcional)
        self.question.refresh_from_db()
        self.assertEqual(self.question.notified_status, "APROVADA")
        self.assertEqual(len(mail.outbox), 1)

    def test_exceptional_approval_requires_justification_and_preserves_audit(self):
        self.question.justificativa = ""
        self.question.save()
        report = executar_pre_auditoria(self.question, revisor=self.reviewer)
        original_result = report.resultado
        original_findings = report.achados
        original_indicators = report.indicadores
        self.assertEqual(original_result, ParecerPreAuditoria.Resultado.PENDENCIAS)
        self.client.force_login(self.reviewer)
        url = reverse("pre_auditoria:aprovar", args=[self.question.pk])

        missing_reason = self.client.post(url, {"confirmar_aprovacao": "on"}, follow=True)
        self.question.refresh_from_db()
        self.assertEqual(self.question.status, "PENDENTE")
        self.assertFalse(DecisaoPreAuditoria.objects.exists())
        self.assertContains(missing_reason, "justificativa objetiva", status_code=200)

        reason = (
            "Pendências verificadas manualmente; a apresentação e o conteúdo foram considerados adequados."
        )
        with self.captureOnCommitCallbacks(execute=True):
            approved = self.client.post(
                url,
                {"confirmar_aprovacao": "on", "justificativa": reason},
            )
        self.assertRedirects(approved, reverse("pre_auditoria:detalhe", args=[self.question.pk]))
        self.question.refresh_from_db()
        report.refresh_from_db()
        decision = DecisaoPreAuditoria.objects.get()
        self.assertEqual(self.question.status, "APROVADA")
        self.assertTrue(decision.aprovacao_excepcional)
        self.assertEqual(decision.revisor, self.reviewer)
        self.assertEqual(decision.revisor_nome, self.reviewer.get_username())
        self.assertEqual(decision.justificativa, reason)
        self.assertEqual(report.resultado, original_result)
        self.assertEqual(report.achados, original_findings)
        self.assertEqual(report.indicadores, original_indicators)
        self.assertEqual(len(mail.outbox), 1)

    def test_exceptional_single_answer_approval_requires_exactly_one_stored_key(self):
        self.client.force_login(self.reviewer)
        reason = "Pendência revisada pelo gestor e autorizada para uso."
        url = reverse("pre_auditoria:aprovar", args=[self.question.pk])

        for correct_indexes in ((), (0, 1)):
            with self.subTest(correct_indexes=correct_indexes):
                self.question.alternativas.update(eh_correta=False)
                for index in correct_indexes:
                    self.question.alternativas.order_by("pk")[index].eh_correta = True
                    self.question.alternativas.order_by("pk")[index].save(update_fields=["eh_correta"])
                self.question.justificativa = ""
                self.question.save(update_fields=["justificativa"])
                report = executar_pre_auditoria(self.question, revisor=self.reviewer)
                self.assertTrue(
                    any(not item["conforme"] for item in report.indicadores["validacoes_estruturais"])
                )

                response = self.client.post(
                    url,
                    {"confirmar_aprovacao": "on", "justificativa": reason},
                    follow=True,
                )
                self.question.refresh_from_db()
                self.assertEqual(self.question.status, "PENDENTE")
                self.assertFalse(DecisaoPreAuditoria.objects.exists())
                self.assertContains(response, "exatamente uma alternativa marcada como correta")

    def test_exceptional_assertion_reason_requires_one_correct_canonical_option(self):
        self.question.tipo_questao = "ASSERCAO_RAZAO"
        self.question.proposicao_dois = "A proposição II explica a proposição I."
        self.question.justificativa = ""
        self.question.save()
        self.question.alternativas.all().delete()
        for letter, text in ASSERTION_ANSWERS.items():
            Alternativa.objects.create(
                questao=self.question,
                texto=f"{letter}) {text}",
                eh_correta=False,
            )
        self.client.force_login(self.reviewer)
        url = reverse("pre_auditoria:aprovar", args=[self.question.pk])
        reason = "Pendências avaliadas manualmente e aceitas para uso."

        for correct_indexes in ((), (0, 1)):
            with self.subTest(correct_indexes=correct_indexes):
                self.question.alternativas.update(eh_correta=False)
                alternatives = list(self.question.alternativas.order_by("pk"))
                for index in correct_indexes:
                    alternatives[index].eh_correta = True
                    alternatives[index].save(update_fields=["eh_correta"])
                report = executar_pre_auditoria(self.question, revisor=self.reviewer)
                self.assertEqual(len(alternatives), 5)
                response = self.client.post(
                    url,
                    {"confirmar_aprovacao": "on", "justificativa": reason},
                    follow=True,
                )
                self.question.refresh_from_db()
                self.assertEqual(self.question.status, "PENDENTE")
                self.assertFalse(DecisaoPreAuditoria.objects.exists())
                self.assertContains(response, "exatamente uma resposta marcada como correta")
                self.assertEqual(report.fingerprint, fingerprint_questao(self.question))

    def test_exceptional_assertion_reason_rejects_noncanonical_response_key(self):
        self.question.tipo_questao = "ASSERCAO_RAZAO"
        self.question.proposicao_dois = "A proposição II explica a proposição I."
        self.question.justificativa = ""
        self.question.save()
        self.question.alternativas.all().delete()
        for index, (letter, text) in enumerate(ASSERTION_ANSWERS.items()):
            Alternativa.objects.create(
                questao=self.question,
                texto=f"{letter}) {text}" if index else "A) Opção não canônica",
                eh_correta=index == 0,
            )
        executar_pre_auditoria(self.question, revisor=self.reviewer)
        self.client.force_login(self.reviewer)

        response = self.client.post(
            reverse("pre_auditoria:aprovar", args=[self.question.pk]),
            {
                "confirmar_aprovacao": "on",
                "justificativa": "Pendências avaliadas manualmente e aceitas para uso.",
            },
            follow=True,
        )
        self.question.refresh_from_db()
        self.assertEqual(self.question.status, "PENDENTE")
        self.assertFalse(DecisaoPreAuditoria.objects.exists())
        self.assertContains(response, "chave canônica de cinco alternativas")

    def test_exceptional_multiple_choice_allows_valid_single_correct_generated_option(self):
        self.question.tipo_questao = "MULTIPLA_ESCOLHA"
        self.question.justificativa = ""
        self.question.save()
        self.question.alternativas.all().delete()
        for index in range(3):
            Alternativa.objects.create(
                questao=self.question,
                texto=f"Afirmativa {index + 1}",
                eh_correta=index == 0,
            )
        report = executar_pre_auditoria(self.question, revisor=self.reviewer)
        self.assertTrue(any(not item["conforme"] for item in report.indicadores["validacoes_estruturais"]))
        self.client.force_login(self.reviewer)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("pre_auditoria:aprovar", args=[self.question.pk]),
                {
                    "confirmar_aprovacao": "on",
                    "justificativa": "A pendência foi inspecionada e aceita pelo gestor.",
                },
            )
        self.question.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.question.status, "APROVADA")
        self.assertTrue(DecisaoPreAuditoria.objects.get().aprovacao_excepcional)

    def test_exceptional_multiple_choice_rejects_generated_options_with_multiple_keys(self):
        self.question.tipo_questao = "MULTIPLA_ESCOLHA"
        self.question.justificativa = ""
        self.question.save()
        self.question.alternativas.all().delete()
        for index in range(3):
            Alternativa.objects.create(
                questao=self.question,
                texto=f"Afirmativa {index + 1}",
                eh_correta=index == 0,
            )
        executar_pre_auditoria(self.question, revisor=self.reviewer)
        self.client.force_login(self.reviewer)
        generated_options = [
            {"texto": "Resposta correta 1", "eh_correta": True},
            {"texto": "Resposta correta 2", "eh_correta": True},
            {"texto": "Distrator 1", "eh_correta": False},
            {"texto": "Distrator 2", "eh_correta": False},
        ]

        with patch("questoes.views.gerar_alternativas_multipla_escolha", return_value=generated_options):
            response = self.client.post(
                reverse("pre_auditoria:aprovar", args=[self.question.pk]),
                {
                    "confirmar_aprovacao": "on",
                    "justificativa": "Pendências avaliadas manualmente e aceitas para uso.",
                },
                follow=True,
            )
        self.question.refresh_from_db()
        self.assertEqual(self.question.status, "PENDENTE")
        self.assertFalse(DecisaoPreAuditoria.objects.exists())
        self.assertContains(response, "exatamente uma resposta correta")

    def test_approval_requires_explicit_confirmation_and_is_not_duplicated(self):
        executar_pre_auditoria(self.question, revisor=self.reviewer)
        self.client.force_login(self.reviewer)
        url = reverse("pre_auditoria:aprovar", args=[self.question.pk])
        self.client.post(url)
        self.question.refresh_from_db()
        self.assertEqual(self.question.status, "PENDENTE")
        self.assertFalse(DecisaoPreAuditoria.objects.exists())

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(url, {"confirmar_aprovacao": "on"})
        self.client.post(url, {"confirmar_aprovacao": "on"})
        self.question.refresh_from_db()
        self.assertEqual(self.question.status, "APROVADA")
        self.assertEqual(DecisaoPreAuditoria.objects.count(), 1)
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
        self.client.post(
            reverse("pre_auditoria:aprovar", args=[self.question.pk]),
            {"confirmar_aprovacao": "on"},
        )
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
        detail = self.client.get(reverse("pre_auditoria:detalhe", args=[self.question.pk]))
        self.assertContains(detail, "Auditoria desatualizada — Reavaliar")
        self.client.post(
            reverse("pre_auditoria:aprovar", args=[self.question.pk]),
            {"confirmar_aprovacao": "on"},
        )
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
        self.client.post(
            reverse("pre_auditoria:aprovar", args=[self.question.pk]),
            {"confirmar_aprovacao": "on"},
        )
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
        self.client.post(
            reverse("pre_auditoria:aprovar", args=[self.question.pk]),
            {"confirmar_aprovacao": "on"},
        )
        self.question.refresh_from_db()
        historical.refresh_from_db()
        self.assertEqual(self.question.status, "PENDENTE")
        self.assertEqual(historical.resultado, ParecerPreAuditoria.Resultado.INCONCLUSIVA)
        self.assertTrue(EvidenciaRevisor.objects.filter(pk=evidence.pk).exists())

        current = executar_pre_auditoria(self.question, revisor=self.reviewer)
        self.assertEqual(current.resultado, ParecerPreAuditoria.Resultado.APROVADA)
        response = self.client.get(reverse("pre_auditoria:detalhe", args=[self.question.pk]))
        self.assertContains(response, "Política anterior")
        self.client.post(
            reverse("pre_auditoria:aprovar", args=[self.question.pk]),
            {"confirmar_aprovacao": "on"},
        )
        self.question.refresh_from_db()
        self.assertEqual(self.question.status, "APROVADA")

    def test_individual_historical_report_is_deleted_only_after_count_confirmation(self):
        historical = ParecerPreAuditoria.objects.create(
            questao=self.question,
            resultado=ParecerPreAuditoria.Resultado.PENDENCIAS,
            fingerprint=fingerprint_questao(self.question),
            achados=[{"criterio": "manual.antigo", "gravidade": "mandatory"}],
            indicadores={"politica": "submeter-basica-v0"},
        )
        latest = executar_pre_auditoria(self.question, revisor=self.reviewer)
        self.client.force_login(self.reviewer)
        detail_url = reverse("pre_auditoria:detalhe", args=[self.question.pk])
        cleanup_url = reverse("pre_auditoria:excluir_auditorias")

        preview = self.client.post(cleanup_url, {"parecer_ids": str(historical.pk), "next": detail_url})
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(len(preview.context["plano"]["deletable_ids"]), 1)
        self.assertEqual(preview.context["plano"]["not_eligible"], 0)
        confirm = self.client.post(
            cleanup_url,
            {
                "plano_assinado": preview.context["confirmacao"],
                "confirmar_operacao": "on",
                "next": detail_url,
            },
        )
        self.assertRedirects(confirm, detail_url)
        self.assertFalse(ParecerPreAuditoria.objects.filter(pk=historical.pk).exists())
        self.assertTrue(ParecerPreAuditoria.objects.filter(pk=latest.pk).exists())
        self.assertTrue(Questao.objects.filter(pk=self.question.pk).exists())
        self.assertEqual(self.question.alternativas.count(), 4)
        operation = OperacaoLimpezaPreAuditoria.objects.get()
        self.assertEqual(operation.revisor, self.reviewer)
        self.assertEqual(operation.pareceres_excluidos, [historical.pk])

    def test_bulk_cleanup_archives_protected_history_and_preserves_questions(self):
        historical_delete = ParecerPreAuditoria.objects.create(
            questao=self.question,
            resultado=ParecerPreAuditoria.Resultado.PENDENCIAS,
            fingerprint=fingerprint_questao(self.question),
            achados=[],
            indicadores={"politica": "submeter-basica-v0"},
        )
        historical_protected = ParecerPreAuditoria.objects.create(
            questao=self.question,
            resultado=ParecerPreAuditoria.Resultado.INCONCLUSIVA,
            fingerprint=fingerprint_questao(self.question),
            achados=[],
            indicadores={"politica": "submeter-basica-v0"},
        )
        evidence = EvidenciaRevisor.objects.create(
            parecer=historical_protected,
            criterio_id="10",
            evidencia="Evidência humana que precisa permanecer no histórico.",
            revisor=self.reviewer,
        )
        decision = DecisaoPreAuditoria.objects.create(
            questao=self.question,
            parecer=historical_protected,
            revisor=self.reviewer,
            revisor_nome=self.reviewer.get_username(),
            acao=DecisaoPreAuditoria.Acao.CORRECAO,
            fingerprint=historical_protected.fingerprint,
            justificativa="Decisão administrativa anterior.",
        )
        correction = SolicitacaoCorrecao.objects.create(
            questao=self.question,
            parecer=historical_protected,
            revisor=self.reviewer,
            revisor_nome=self.reviewer.get_username(),
            mensagem="Pedido de correção preservado para rastreabilidade.",
            expira_em=timezone.now() + timedelta(days=7),
        )
        latest = executar_pre_auditoria(self.question, revisor=self.reviewer)

        second = Questao.objects.create(
            componente=self.component,
            professor_nome="Segundo docente",
            professor_email="segundo@example.com",
            texto_base="Outro contexto.",
            enunciado="Questão secundária?",
            justificativa="Justificativa secundária.",
        )
        for index in range(4):
            Alternativa.objects.create(
                questao=second, texto=f"Resposta secundária {index}", eh_correta=index == 0
            )
        second_historical = ParecerPreAuditoria.objects.create(
            questao=second,
            resultado=ParecerPreAuditoria.Resultado.PENDENCIAS,
            fingerprint=fingerprint_questao(second),
            achados=[],
            indicadores={"politica": "submeter-basica-v0"},
        )
        second_latest = executar_pre_auditoria(second, revisor=self.reviewer)

        self.client.force_login(self.reviewer)
        cleanup_url = reverse("pre_auditoria:excluir_auditorias")
        preview = self.client.post(
            cleanup_url,
            {"questao_ids": [str(self.question.pk), str(second.pk)]},
        )
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(len(preview.context["plano"]["deletable_ids"]), 2)
        self.assertEqual(len(preview.context["plano"]["archivable_ids"]), 1)
        confirm = self.client.post(
            cleanup_url,
            {
                "plano_assinado": preview.context["confirmacao"],
                "confirmar_operacao": "on",
            },
        )
        self.assertRedirects(confirm, reverse("pre_auditoria:lista"))
        self.assertFalse(ParecerPreAuditoria.objects.filter(pk=historical_delete.pk).exists())
        self.assertFalse(ParecerPreAuditoria.objects.filter(pk=second_historical.pk).exists())
        historical_protected.refresh_from_db()
        self.assertIsNotNone(historical_protected.arquivado_em)
        self.assertEqual(historical_protected.arquivado_por, self.reviewer)
        self.assertTrue(EvidenciaRevisor.objects.filter(pk=evidence.pk).exists())
        self.assertTrue(DecisaoPreAuditoria.objects.filter(pk=decision.pk).exists())
        self.assertTrue(SolicitacaoCorrecao.objects.filter(pk=correction.pk).exists())
        self.assertEqual(ParecerPreAuditoria.objects.filter(pk__in=[latest.pk, second_latest.pk]).count(), 2)
        self.assertEqual(Questao.objects.filter(pk__in=[self.question.pk, second.pk]).count(), 2)
        self.assertEqual(self.question.alternativas.count(), 4)
        operation = OperacaoLimpezaPreAuditoria.objects.get()
        self.assertEqual(operation.pareceres_excluidos, sorted([historical_delete.pk, second_historical.pk]))
        self.assertEqual(operation.pareceres_arquivados, [historical_protected.pk])
        self.assertEqual(operation.nao_elegiveis, 0)

    def test_cleanup_rechecks_dependencies_before_final_confirmation(self):
        historical = ParecerPreAuditoria.objects.create(
            questao=self.question,
            resultado=ParecerPreAuditoria.Resultado.PENDENCIAS,
            fingerprint=fingerprint_questao(self.question),
            achados=[],
            indicadores={"politica": "submeter-basica-v0"},
        )
        executar_pre_auditoria(self.question, revisor=self.reviewer)
        self.client.force_login(self.reviewer)
        url = reverse("pre_auditoria:excluir_auditorias")
        preview = self.client.post(url, {"parecer_ids": str(historical.pk)})
        EvidenciaRevisor.objects.create(
            parecer=historical,
            criterio_id="10",
            evidencia="A evidência foi registrada após a tela de confirmação.",
            revisor=self.reviewer,
        )
        stale_confirmation = self.client.post(
            url,
            {
                "plano_assinado": preview.context["confirmacao"],
                "confirmar_operacao": "on",
            },
        )
        self.assertEqual(stale_confirmation.status_code, 409)
        self.assertEqual(len(stale_confirmation.context["plano"]["archivable_ids"]), 1)
        self.assertTrue(ParecerPreAuditoria.objects.filter(pk=historical.pk).exists())
        self.assertFalse(OperacaoLimpezaPreAuditoria.objects.exists())

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
