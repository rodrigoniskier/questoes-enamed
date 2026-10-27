"""Regressões dos relatos do NAPED, sem dados ou serviços de produção."""

import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import OperationalError
from django.test import Client, TestCase
from django.urls import reverse

from questoes.avaliacao import notificar_decisao, registrar_decisao
from questoes.models import Alternativa, ComponenteCurricular, Periodo, Questao, Semestre

from .models import AlteracaoPreAuditoria, DecisaoPreAuditoria, SolicitacaoCorrecao
from .services import executar_pre_auditoria, fingerprint_questao


class PareceresNapedTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.semester = Semestre.objects.create(nome="2026.2")
        cls.period = Periodo.objects.create(nome="2º Período", semestre=cls.semester)
        cls.component = ComponenteCurricular.objects.create(nome="Componente sintético", periodo=cls.period)
        cls.question = Questao.objects.create(
            componente=cls.component,
            professor_nome="Docente sintético",
            professor_email="docente@example.com",
            texto_base="Muitas enfermidades provocam dor.",
            enunciado="Qual a alternativa correta?",
            justificativa="A alternativa A é correta; as demais são inadequadas.",
        )
        for index in range(4):
            Alternativa.objects.create(
                questao=cls.question, texto=f"Alternativa {index}", eh_correta=index == 0
            )
        cls.reviewer = get_user_model().objects.create_user("revisor-naped")
        cls.reviewer.user_permissions.add(
            *Permission.objects.filter(
                codename__in=[
                    "review_parecerpreauditoria",
                    "view_parecerpreauditoria",
                ]
            )
        )
        cls.admin = get_user_model().objects.create_superuser("admin-naped", "admin@example.com", "teste")

    def setUp(self):
        self.client.force_login(self.reviewer)
        self.report = executar_pre_auditoria(self.question, revisor=self.reviewer, semantica=False)

    def payload(self, status, note="", **extra):
        self.question.refresh_from_db()
        return {
            "classificacao": status,
            "parecer": note,
            "confirmar": "on",
            "fingerprint": fingerprint_questao(self.question),
            "request_id": str(uuid.uuid4()),
            **extra,
        }

    def decide(self, status, note="", **extra):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(
                reverse("pre_auditoria:decidir", args=[self.question.pk]), self.payload(status, note, **extra)
            )

    def edit_data(self, **extra):
        self.question.refresh_from_db()
        data = {
            "tipo_questao": self.question.tipo_questao,
            "uso_prova": self.question.uso_prova,
            "texto_base": self.question.texto_base or "",
            "enunciado": self.question.enunciado,
            "justificativa": self.question.justificativa,
            "comentario_validacao": "",
            "fingerprint": fingerprint_questao(self.question),
            "alternativas-TOTAL_FORMS": "4",
            "alternativas-INITIAL_FORMS": "4",
            "alternativas-MIN_NUM_FORMS": "0",
            "alternativas-MAX_NUM_FORMS": "5",
        }
        for index, alt in enumerate(self.question.alternativas.order_by("pk")):
            data[f"alternativas-{index}-id"] = str(alt.pk)
            data[f"alternativas-{index}-texto"] = alt.texto
            if alt.eh_correta:
                data[f"alternativas-{index}-eh_correta"] = "on"
        return {**data, **extra}

    def test_all_classifications_and_pending_have_distinct_effects(self):
        for status in ("APROVADA", "APROVADA_SUGESTOES", "REPROVADA", "PENDENTE"):
            with self.subTest(status=status):
                response = self.decide(
                    status, "Revise a clareza do comando, preservando o objetivo de aprendizagem."
                )
                self.assertEqual(response.status_code, 302)
                self.question.refresh_from_db()
                decision = self.question.decisoes_pre_auditoria.first()
                self.assertEqual(self.question.status, status)
                self.assertEqual(decision.acao, status)
                self.assertEqual(decision.revisor, self.reviewer)
                self.assertIsNotNone(decision.criada_em)
                self.assertEqual(
                    decision.notificacao_estado, "SEM_ENVIO" if status == "PENDENTE" else "ENVIADO"
                )
        self.assertEqual(self.question.decisoes_pre_auditoria.count(), 4)
        self.assertEqual(len(mail.outbox), 3)
        self.assertIn("Aprovada com sugestões", mail.outbox[1].subject)
        self.assertIn("Revisão necessária", mail.outbox[2].subject)

    def test_long_reports_are_saved_and_emailed_in_full(self):
        for size in (2256, 6001, 20000):
            with self.subTest(size=size):
                note = "Texto-base e contextualização:\n\n" + "á" * (size - 32)
                note = note[:size].ljust(size, "x")
                self.assertEqual(self.decide("REPROVADA", note).status_code, 302)
                self.assertEqual(self.question.decisoes_pre_auditoria.first().justificativa, note)
                self.assertIn(note, mail.outbox[-1].body)
                self.assertIn("Reprovada", mail.outbox[-1].body)
                self.assertIn(str(self.question.pk), mail.outbox[-1].subject)
                self.assertIn(self.component.nome, mail.outbox[-1].body)

    def test_browser_crlf_uses_same_20000_character_limit_as_textarea(self):
        note = "x\n" * 9999 + "xx"
        self.assertEqual(len(note), 20000)
        self.assertEqual(self.decide("REPROVADA", note.replace("\n", "\r\n")).status_code, 302)
        self.assertEqual(self.question.decisoes_pre_auditoria.get().justificativa, note)

    def test_optional_and_required_feedback_and_confirmation(self):
        self.assertEqual(self.decide("APROVADA").status_code, 302)
        self.assertEqual(self.decide("PENDENTE").status_code, 302)
        for status in ("APROVADA_SUGESTOES", "REPROVADA"):
            for note in ("", "curto", " " * 30):
                with self.subTest(status=status, note=note):
                    response = self.decide(status, note)
                    self.assertContains(response, "pelo menos 20 caracteres", status_code=400)
                    self.assertEqual(response.context["form"]["parecer"].value(), note)
        response = self.decide("APROVADA", confirmar="")
        self.assertEqual(response.status_code, 400)

    def test_overlong_report_shows_maximum_error_and_preserves_text(self):
        note = "x" * 20001
        response = self.decide("REPROVADA", note)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.context["form"]["parecer"].value(), note)
        self.assertEqual(response.context["form"].errors.as_data()["parecer"][0].code, "max_length")
        self.assertNotIn(
            "min_length", [error.code for error in response.context["form"].errors.as_data()["parecer"]]
        )
        self.assertFalse(DecisaoPreAuditoria.objects.exists())

    def test_legacy_correction_form_accepts_2256_characters(self):
        note = "x" * 2256
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("pre_auditoria:solicitar_correcao", args=[self.question.pk]), {"mensagem": note}
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SolicitacaoCorrecao.objects.get().mensagem, note)
        self.assertIn(note, mail.outbox[0].body)

    def test_text_only_move_persists_and_rechecks_latest_version(self):
        original = self.question.texto_base
        new_command = original + "\n\nQual a conduta?"
        original_alts = list(self.question.alternativas.values_list("pk", "texto", "eh_correta"))
        response = self.client.post(
            reverse("pre_auditoria:editar", args=[self.question.pk]),
            self.edit_data(texto_base="", enunciado=new_command),
        )
        self.assertEqual(response.status_code, 302)
        self.question.refresh_from_db()
        self.assertEqual(self.question.texto_base, "")
        self.assertEqual(self.question.enunciado, new_command)
        self.assertEqual(
            list(self.question.alternativas.values_list("pk", "texto", "eh_correta")), original_alts
        )
        change = AlteracaoPreAuditoria.objects.get()
        self.assertEqual(change.antes["texto_base"], original)
        self.assertEqual(change.depois["texto_base"], "")
        report = self.question.pareceres_pre_auditoria.first()
        self.assertEqual(report.fingerprint, fingerprint_questao(self.question))
        self.assertNotEqual(report.pk, self.report.pk)
        self.assertTrue(any(f["criterio"] == "texto-base" for f in report.achados))
        detail = self.client.get(response.url)
        self.assertContains(detail, original)
        reopened = self.client.get(reverse("pre_auditoria:editar", args=[self.question.pk]))
        self.assertEqual(reopened.context["form"]["texto_base"].value(), "")
        self.assertEqual(reopened.context["form"]["enunciado"].value(), new_command)

    def test_multiple_answer_text_move_preserves_statements_and_history(self):
        self.question.tipo_questao = "MULTIPLA_ESCOLHA"
        self.question.save()
        data = self.edit_data(texto_base="", enunciado="Novo comando com o contexto transferido.")
        self.assertEqual(
            self.client.post(reverse("pre_auditoria:editar", args=[self.question.pk]), data).status_code, 302
        )
        self.question.refresh_from_db()
        self.assertEqual(self.question.alternativas.count(), 4)
        self.assertEqual(
            AlteracaoPreAuditoria.objects.get().antes["enunciado"], "Qual a alternativa correta?"
        )
        self.assertEqual(
            self.question.pareceres_pre_auditoria.first().fingerprint, fingerprint_questao(self.question)
        )

    def test_edit_validation_preserves_text_and_rolls_back_on_bad_key(self):
        data = self.edit_data(texto_base="", enunciado="Conteúdo transferido para o enunciado.")
        data.pop("alternativas-0-eh_correta")
        response = self.client.post(reverse("pre_auditoria:editar", args=[self.question.pk]), data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"]["enunciado"].value(), data["enunciado"])
        self.question.refresh_from_db()
        self.assertEqual(self.question.texto_base, "Muitas enfermidades provocam dor.")
        self.assertFalse(AlteracaoPreAuditoria.objects.exists())

    def test_feedback_is_escaped_in_history_forms_and_html_email(self):
        note = 'Revisar o comando.\n\n</textarea><script>alert("teste")</script>'
        self.decide("APROVADA_SUGESTOES", note)
        email = mail.outbox[-1]
        self.assertIn(note, email.body)
        self.assertNotIn("<script>", email.alternatives[0].content)
        self.assertIn("&lt;script&gt;", email.alternatives[0].content)
        detail = self.client.get(reverse("pre_auditoria:detalhe", args=[self.question.pk]))
        self.assertNotContains(detail, "<script>alert")
        self.assertContains(detail, "&lt;script&gt;")

    def test_stale_and_invalid_decisions_do_not_change_data(self):
        for extra in (
            {"fingerprint": "stale"},
            {"classificacao": "CORRECAO"},
            {"classificacao": "desconhecida"},
        ):
            with self.subTest(extra=extra):
                data = {**self.payload("REPROVADA", "Revise o gabarito e a fundamentação do item."), **extra}
                response = self.client.post(reverse("pre_auditoria:decidir", args=[self.question.pk]), data)
                self.assertEqual(response.status_code, 400)
        self.assertFalse(DecisaoPreAuditoria.objects.exists())
        self.question.refresh_from_db()
        self.assertEqual(self.question.status, "PENDENTE")

    def test_request_replay_cannot_duplicate_mail_or_reverse_newer_decision(self):
        data = self.payload("APROVADA")
        url = reverse("pre_auditoria:decidir", args=[self.question.pk])
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(url, data)
            self.client.post(url, data)
        self.assertEqual(len(mail.outbox), 1)
        self.decide("REPROVADA", "Revisar o gabarito e justificar cada alternativa.")
        self.client.post(url, data)
        self.question.refresh_from_db()
        self.assertEqual(self.question.status, "REPROVADA")
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(DecisaoPreAuditoria.objects.count(), 2)

    def test_repeated_decision_with_new_request_is_also_idempotent(self):
        note = "Revise a justificativa de cada distrator da questão."
        self.decide("REPROVADA", note)
        self.decide("REPROVADA", note)
        self.assertEqual(DecisaoPreAuditoria.objects.count(), 1)
        self.assertEqual(SolicitacaoCorrecao.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_equivalent_request_token_remains_bound_after_a_later_decision(self):
        url = reverse("pre_auditoria:decidir", args=[self.question.pk])
        note = "Revisar a justificativa de cada alternativa da questão."
        first = self.payload("REPROVADA", note)
        equivalent = self.payload("REPROVADA", note)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(url, first)
            self.client.post(url, equivalent)
        self.decide("PENDENTE")
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(url, equivalent)
        self.question.refresh_from_db()
        self.assertEqual(self.question.status, "PENDENTE")
        self.assertEqual(DecisaoPreAuditoria.objects.count(), 2)
        self.assertEqual(len(mail.outbox), 1)

    def test_professor_text_only_resubmission_returns_to_pending_and_preserves_old_rejection(self):
        self.decide("REPROVADA", "Revisar o comando e apresentar o contexto no enunciado.")
        link = (
            mail.outbox[0]
            .body.split("Acesse o link seguro para revisar e reenviar a questão: ")[1]
            .split()[0]
        )
        data = self.edit_data(texto_base="", enunciado="Contexto transferido. Qual a conduta agora?")
        self.client.logout()
        response = self.client.post(link, data)
        self.assertContains(response, "Correção reenviada")
        self.question.refresh_from_db()
        self.assertEqual(self.question.status, "PENDENTE")
        self.assertEqual(self.question.texto_base, "")
        history = self.question.alteracoes_pre_auditoria.get()
        self.assertEqual(history.antes["enunciado"], "Qual a alternativa correta?")
        self.assertEqual(history.origem, "PROFESSOR")
        self.assertEqual(
            list(self.question.decisoes_pre_auditoria.values_list("acao", flat=True)),
            ["PENDENTE", "REPROVADA"],
        )
        self.assertEqual(
            self.question.pareceres_pre_auditoria.first().fingerprint, fingerprint_questao(self.question)
        )
        self.assertEqual(self.client.get(link).status_code, 410)
        self.assertEqual(len(mail.outbox), 1)

    def test_known_email_failure_is_recoverable_without_duplicate_decision(self):
        with patch("questoes.avaliacao.enviar_email_status", side_effect=RuntimeError("SMTP")):
            self.decide("APROVADA")
        self.assertEqual(DecisaoPreAuditoria.objects.get().notificacao_estado, "FALHOU")
        self.decide("APROVADA")
        self.decide("APROVADA")
        self.assertEqual(DecisaoPreAuditoria.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(DecisaoPreAuditoria.objects.get().notificacao_estado, "ENVIADO")

    def test_notification_database_failure_does_not_report_false_decision_failure(self):
        with patch("questoes.avaliacao.notificar_decisao", side_effect=OperationalError("synthetic")):
            response = self.decide("APROVADA")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(DecisaoPreAuditoria.objects.get().notificacao_estado, "PENDENTE")
        self.decide("APROVADA")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(DecisaoPreAuditoria.objects.count(), 1)

    def test_history_and_in_progress_notifications_are_not_resent(self):
        for state in ("LEGADO", "ENVIANDO", "ENVIADO", "SEM_ENVIO"):
            decision = DecisaoPreAuditoria.objects.create(
                questao=self.question,
                parecer=self.report,
                acao="APROVADA",
                fingerprint=fingerprint_questao(self.question),
                notificacao_estado=state,
            )
            notificar_decisao(decision.pk)
        self.assertEqual(len(mail.outbox), 0)

    def test_superseded_notification_is_cancelled(self):
        with self.captureOnCommitCallbacks(execute=False):
            old = registrar_decisao(self.question.pk, self.reviewer, "APROVADA")
            registrar_decisao(self.question.pk, self.reviewer, "PENDENTE")
        notificar_decisao(old.pk)
        old.refresh_from_db()
        self.assertEqual(old.notificacao_estado, "SUPERADO")
        self.assertEqual(len(mail.outbox), 0)

    def test_atomic_failure_keeps_status_and_posted_text(self):
        note = "Revise o comando e a fundamentação dos distratores."
        with patch(
            "pre_auditoria.models.DecisaoPreAuditoria.objects.create",
            side_effect=OperationalError("synthetic"),
        ):
            response = self.decide("REPROVADA", note)
        self.assertContains(response, "Seu texto foi preservado", status_code=400)
        self.assertEqual(response.context["form"]["parecer"].value(), note)
        self.question.refresh_from_db()
        self.assertEqual(self.question.status, "PENDENTE")
        self.assertFalse(SolicitacaoCorrecao.objects.exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_permissions_csrf_and_archived_semester(self):
        ordinary = get_user_model().objects.create_user("sem-permissao", is_staff=True)
        self.client.force_login(ordinary)
        self.assertEqual(
            self.client.get(reverse("pre_auditoria:decidir", args=[self.question.pk])).status_code, 403
        )
        self.assertEqual(
            self.client.get(reverse("admin:questoes_questao_parecer", args=[self.question.pk])).status_code,
            403,
        )
        with self.assertRaises(PermissionDenied):
            registrar_decisao(self.question.pk, ordinary, "APROVADA")
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.reviewer)
        self.assertEqual(
            csrf_client.post(
                reverse("pre_auditoria:decidir", args=[self.question.pk]), self.payload("APROVADA")
            ).status_code,
            403,
        )
        self.semester.ativo = False
        self.semester.save()
        with self.assertRaises(ValidationError):
            registrar_decisao(self.question.pk, self.reviewer, "APROVADA")

    def test_admin_and_pre_audit_use_same_validation_and_outcomes(self):
        self.client.force_login(self.admin)
        url = reverse("admin:questoes_questao_parecer", args=[self.question.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(url, self.payload("REPROVADA", "curto"))
        self.assertEqual(response.status_code, 400)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                url, self.payload("APROVADA_SUGESTOES", "Sugere-se tornar o comando mais claro e conciso.")
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(DecisaoPreAuditoria.objects.get().acao, "APROVADA_SUGESTOES")
        self.assertIn("Aprovada com sugestões", mail.outbox[0].subject)
        admin_form = self.client.get(reverse("admin:questoes_questao_change", args=[self.question.pk]))
        self.assertNotContains(admin_form, 'select name="status"')

    def test_suggestions_count_as_approved_and_rejected_items_are_excluded_from_exams(self):
        self.decide("APROVADA_SUGESTOES", "Sugere-se simplificar o comando mantendo sua precisão.")
        rejected = Questao.objects.create(
            componente=self.component,
            professor_nome="Outro docente",
            professor_email="outro@example.com",
            enunciado="Item reprovado não pode entrar.",
            justificativa="Revisar",
            status="REPROVADA",
        )
        self.client.force_login(self.admin)
        generated = self.client.get(
            reverse("prova_gerada"),
            {"componentes": [self.component.pk], f"quantidade_{self.component.pk}": "1"},
        )
        self.assertContains(generated, self.question.enunciado)
        self.assertNotContains(generated, rejected.enunciado)
        manual = self.client.get(reverse("montador_manual"), {"periodo_id": self.period.pk})
        self.assertEqual([question.pk for question in manual.context["questoes"]], [self.question.pk])
        self.assertNotContains(manual, rejected.enunciado)
        dashboard = self.client.get(reverse("pre_auditoria:lista"), {"status": "APROVADA_SUGESTOES"})
        self.assertEqual(dashboard.context["aprovadas"], 1)
        self.assertEqual(dashboard.context["reprovadas"], 1)
        self.assertEqual(len(dashboard.context["questoes"]), 1)
        summary = self.client.get(reverse("admin:questoes_semestre_changelist"))
        self.assertEqual(summary.context["cl"].result_list[0]._aprovadas, 1)

    def test_approval_requires_current_structural_report_in_both_interfaces(self):
        self.question.enunciado = "O conteúdo mudou após a verificação estrutural."
        self.question.save()
        for status in Questao.STATUS_APROVADOS:
            with self.assertRaises(ValidationError):
                registrar_decisao(
                    self.question.pk,
                    self.admin,
                    status,
                    "Foi realizada inspeção das alternativas e do comando.",
                )
        self.assertFalse(DecisaoPreAuditoria.objects.exists())
