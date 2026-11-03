import tempfile
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import OperationalError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from pre_auditoria.models import DecisaoPreAuditoria, ParecerPreAuditoria, SolicitacaoCorrecao
from pre_auditoria.services import executar_pre_auditoria, fingerprint_questao, snapshot_questao

from .models import Alternativa, ComponenteCurricular, Periodo, Questao, Semestre
from .submission import ASSERTION_ANSWERS


class RevisaoEnviadasTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        semester = Semestre.objects.create(nome="2026.2")
        period = Periodo.objects.create(nome="2º Período", semestre=semester)
        cls.component = ComponenteCurricular.objects.create(nome="Clínica", periodo=period)
        cls.other = ComponenteCurricular.objects.create(nome="Cirurgia", periodo=period)
        cls.user = get_user_model().objects.create_superuser("editor", "editor@example.com", "teste")
        cls.question = Questao.objects.create(
            componente=cls.component,
            professor_nome="Docente sintético",
            professor_email="docente@example.com",
            texto_base="Contexto sintético",
            enunciado="Qual a conduta?",
            justificativa="Justificativa sintética",
        )
        for index in range(4):
            Alternativa.objects.create(
                questao=cls.question, texto=f"Opção {index + 1}", eh_correta=index == 0
            )

    def setUp(self):
        self.client.force_login(self.user)
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        setting = override_settings(MEDIA_ROOT=self.media.name)
        setting.enable()
        self.addCleanup(setting.disable)

    def url(self, question=None, component=None):
        question = question or self.question
        component = component or self.component
        return reverse("submeter_editar_questao", args=[question.pk]) + f"?componente={component.pk}"

    def payload(self, question=None):
        question = question or self.question
        response = self.client.get(self.url(question))
        self.assertEqual(response.status_code, 200)
        question.refresh_from_db()
        alternatives = list(question.alternativas.order_by("pk"))
        data = {
            "edicao_token": response.context["edicao_token"],
            "professor_nome": question.professor_nome,
            "professor_email": question.professor_email,
            "componente": question.componente_id,
            "tipo_questao": question.tipo_questao,
            "uso_prova": question.uso_prova,
            "texto_base": question.texto_base or "",
            "enunciado": "Enunciado reformulado",
            "proposicao_dois": question.proposicao_dois or "",
            "justificativa": question.justificativa,
            "confirmar_reenvio": "on",
            "alternativas-TOTAL_FORMS": "5",
            "alternativas-INITIAL_FORMS": str(len(alternatives)),
        }
        for index in range(5):
            data[f"alternativas-{index}-texto"] = (
                alternatives[index].texto if index < len(alternatives) else ""
            )
            if index < len(alternatives):
                data[f"alternativas-{index}-id"] = str(alternatives[index].pk)
                if alternatives[index].eh_correta:
                    data[f"alternativas-{index}-eh_correta"] = "on"
        return data

    def test_component_bank_offers_full_editor_and_displays_feedback(self):
        DecisaoPreAuditoria.objects.create(
            questao=self.question,
            acao="REPROVADA",
            fingerprint=fingerprint_questao(self.question),
            justificativa="Reformule o comando e corrija a justificativa.",
        )
        url = reverse("submeter_visualizar_questoes") + f"?componente={self.component.pk}"
        response = self.client.get(url)
        self.assertContains(response, "EDITAR E REENVIAR")
        self.assertContains(response, "Reformule o comando")
        self.assertContains(response, self.url())
        response = self.client.get(self.url())
        self.assertEqual(response.context["form"].fields["componente"].queryset.count(), 2)
        self.assertNotIn("status", response.context["form"].fields)
        self.assertNotIn("comentario_validacao", response.context["form"].fields)

    def test_editor_requires_staff_and_question_change_permission_and_csrf(self):
        data = self.payload()
        self.client.logout()
        self.assertEqual(self.client.get(self.url()).status_code, 302)
        self.assertEqual(self.client.post(self.url(), data).status_code, 302)
        staff = get_user_model().objects.create_user("sem-permissao", is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(self.url()).status_code, 403)
        self.assertEqual(self.client.post(self.url(), data).status_code, 403)
        staff.user_permissions.add(Permission.objects.get(codename="change_questao"))
        self.assertEqual(self.client.get(self.url()).status_code, 200)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        self.assertEqual(csrf_client.post(self.url(), data).status_code, 403)

    def test_edit_and_resubmit_from_every_status_preserves_history_date_and_question_identity(self):
        for status in dict(Questao.STATUS_CHOICES):
            with self.subTest(status=status):
                Questao.objects.filter(pk=self.question.pk).update(status=status, notified_status=status)
                self.question.refresh_from_db()
                original_date = self.question.enviada_em
                previous = executar_pre_auditoria(self.question)
                data = self.payload()
                data.update(
                    professor_nome="Docente corrigido",
                    professor_email="novo@example.com",
                    texto_base="Novo contexto",
                    justificativa="Nova justificativa",
                    uso_prova="SIMULADO",
                    status="APROVADA",
                    comentario_validacao="Parecer forjado",
                )
                data["alternativas-0-eh_correta"] = ""
                data["alternativas-1-eh_correta"] = "on"
                with patch("pre_auditoria.services._semantic_review") as ai:
                    response = self.client.post(self.url(), data)
                self.assertEqual(response.status_code, 302)
                ai.assert_not_called()
                self.question.refresh_from_db()
                self.assertEqual(self.question.status, "PENDENTE")
                self.assertEqual(self.question.notified_status, "")
                self.assertEqual(self.question.enviada_em, original_date)
                self.assertEqual(self.question.professor_email, "novo@example.com")
                self.assertEqual(self.question.texto_base, "Novo contexto")
                self.assertNotEqual(self.question.comentario_validacao, "Parecer forjado")
                self.assertTrue(ParecerPreAuditoria.objects.filter(pk=previous.pk).exists())
                self.assertEqual(
                    self.question.pareceres_pre_auditoria.first().fingerprint,
                    fingerprint_questao(self.question),
                )
                self.assertEqual(self.question.alternativas.filter(eh_correta=True).get().texto, "Opção 2")
                self.assertEqual(Questao.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 0)

    def test_previous_feedback_and_correction_link_are_preserved_but_link_expires(self):
        report = executar_pre_auditoria(self.question)
        correction = SolicitacaoCorrecao.objects.create(
            questao=self.question,
            parecer=report,
            mensagem="Corrija o enunciado.",
            expira_em=timezone.now() + timedelta(days=7),
        )
        decision = DecisaoPreAuditoria.objects.create(
            questao=self.question,
            parecer=report,
            acao="REPROVADA",
            fingerprint=report.fingerprint,
            justificativa="Orientações anteriores completas e preservadas.",
        )
        Questao.objects.filter(pk=self.question.pk).update(status="REPROVADA")
        data = self.payload()
        self.assertContains(self.client.get(self.url()), decision.justificativa)
        self.client.post(self.url(), data)
        correction.refresh_from_db()
        decision.refresh_from_db()
        self.assertLessEqual(correction.expira_em, timezone.now())
        self.assertEqual(decision.justificativa, "Orientações anteriores completas e preservadas.")
        change = self.question.alteracoes_pre_auditoria.get()
        self.assertEqual(change.antes["status"], "REPROVADA")
        self.assertEqual(change.depois["status"], "PENDENTE")

    def test_repeat_does_not_duplicate_audit_decision_history_or_overwrite_later_changes(self):
        data = self.payload()
        self.client.post(self.url(), data)
        counts = (
            ParecerPreAuditoria.objects.count(),
            DecisaoPreAuditoria.objects.count(),
            self.question.alteracoes_pre_auditoria.count(),
        )
        Questao.objects.filter(pk=self.question.pk).update(enunciado="Alteração posterior", status="APROVADA")
        self.assertEqual(self.client.post(self.url(), data).status_code, 302)
        self.question.refresh_from_db()
        self.assertEqual(self.question.enunciado, "Alteração posterior")
        self.assertEqual(self.question.status, "APROVADA")
        self.assertEqual(
            counts,
            (
                ParecerPreAuditoria.objects.count(),
                DecisaoPreAuditoria.objects.count(),
                self.question.alteracoes_pre_auditoria.count(),
            ),
        )

    def test_token_is_bound_to_user_question_and_component(self):
        data = self.payload()
        for token in ("invalido", data["edicao_token"][:-5] + "outro"):
            self.assertEqual(self.client.post(self.url(), {**data, "edicao_token": token}).status_code, 400)
        second = get_user_model().objects.create_superuser("outro", "outro@example.com", "teste")
        self.client.force_login(second)
        self.assertEqual(self.client.post(self.url(), data).status_code, 400)
        self.assertEqual(self.client.post(self.url(component=self.other), data).status_code, 404)
        self.assertFalse(ParecerPreAuditoria.objects.exists())

    def test_stale_content_and_new_human_decision_reject_overwrite_and_preserve_text(self):
        data = self.payload()
        Questao.objects.filter(pk=self.question.pk).update(enunciado="Edição em outra aba")
        response = self.client.post(self.url(), data)
        self.assertEqual(response.status_code, 409)
        self.assertContains(response, "Enunciado reformulado", status_code=409)
        self.assertContains(response, "mudou em outra sessão", status_code=409)
        data = self.payload()
        DecisaoPreAuditoria.objects.create(questao=self.question, acao="PENDENTE", fingerprint="f" * 64)
        self.assertEqual(self.client.post(self.url(), data).status_code, 409)
        self.assertFalse(self.question.alteracoes_pre_auditoria.exists())

    def test_invalid_confirmation_gabarito_and_management_form_never_save(self):
        data = self.payload()
        for changes in (
            {"confirmar_reenvio": ""},
            {"alternativas-1-eh_correta": "on"},
            {"alternativas-TOTAL_FORMS": "0", "alternativas-INITIAL_FORMS": "0"},
            {"professor_email": "email-invalido"},
            {"justificativa": ""},
        ):
            with self.subTest(changes=changes):
                self.assertEqual(self.client.post(self.url(), {**data, **changes}).status_code, 400)
                self.question.refresh_from_db()
                self.assertEqual(self.question.enunciado, "Qual a conduta?")
                self.assertFalse(ParecerPreAuditoria.objects.exists())

    def test_foreign_alternative_id_cannot_be_edited_or_silently_omitted(self):
        foreign = Questao.objects.create(componente=self.other, enunciado="Outra", justificativa="J")
        alternative = Alternativa.objects.create(questao=foreign, texto="Alternativa alheia", eh_correta=True)
        data = self.payload()
        data["alternativas-0-id"] = alternative.pk
        self.assertEqual(self.client.post(self.url(), data).status_code, 400)
        alternative.refresh_from_db()
        self.assertEqual(alternative.texto, "Alternativa alheia")
        self.assertFalse(self.question.alteracoes_pre_auditoria.exists())
        data = self.payload()
        data["alternativas-0-id"], data["alternativas-1-id"] = (
            data["alternativas-1-id"],
            data["alternativas-0-id"],
        )
        self.assertEqual(self.client.post(self.url(), data).status_code, 400)

    def test_move_within_semester_and_replay_works_without_moving_twice(self):
        data = self.payload()
        data["componente"] = self.other.pk
        response = self.client.post(self.url(), data)
        self.assertRedirects(
            response, reverse("submeter_visualizar_questoes") + f"?componente={self.other.pk}"
        )
        self.assertEqual(self.client.post(self.url(), data).status_code, 302)
        self.question.refresh_from_db()
        self.assertEqual(self.question.componente_id, self.other.pk)
        self.assertEqual(self.client.get(self.url()).status_code, 404)

    def test_archived_component_or_another_semester_is_not_a_destination(self):
        semester = Semestre.objects.create(nome="2025.2", ativo=True)
        period = Periodo.objects.create(nome="Histórico", semestre=semester)
        foreign = ComponenteCurricular.objects.create(nome="Outro semestre", periodo=period)
        data = self.payload()
        self.assertEqual(self.client.post(self.url(), {**data, "componente": foreign.pk}).status_code, 400)
        Periodo.objects.filter(pk=self.component.periodo_id).update(ativo=False)
        self.assertEqual(self.client.post(self.url(), data).status_code, 409)
        self.assertFalse(ParecerPreAuditoria.objects.exists())

    def test_style_can_change_to_multiple_choice_and_assertion_reason(self):
        data = self.payload()
        data["tipo_questao"] = "MULTIPLA_ESCOLHA"
        self.assertEqual(self.client.post(self.url(), data).status_code, 302)
        self.question.refresh_from_db()
        self.assertEqual(self.question.tipo_questao, "MULTIPLA_ESCOLHA")
        data = self.payload()
        data.update(tipo_questao="ASSERCAO_RAZAO", proposicao_dois="Segunda proposição")
        for index, (letter, text) in enumerate(ASSERTION_ANSWERS.items()):
            data[f"alternativas-{index}-texto"] = f"{letter}) {text}"
        self.assertEqual(self.client.post(self.url(), data).status_code, 302)
        self.question.refresh_from_db()
        self.assertEqual(self.question.tipo_questao, "ASSERCAO_RAZAO")
        self.assertEqual(self.question.alternativas.count(), 5)
        self.assertEqual(self.question.pareceres_pre_auditoria.first().resultado, "APROVADA")

    def test_invalid_assertion_choices_and_restricted_component_style_are_rejected(self):
        data = self.payload()
        data.update(tipo_questao="ASSERCAO_RAZAO", proposicao_dois="Proposição II")
        data["alternativas-4-texto"] = "Alternativa livre, incompatível com o padrão"
        self.assertEqual(self.client.post(self.url(), data).status_code, 400)
        period = Periodo.objects.create(
            nome="ESTÁGIO CURRICULAR OBRIGATÓRIO", semestre=self.component.periodo.semestre
        )
        component = ComponenteCurricular.objects.create(nome="Internato", periodo=period)
        data.update(componente=component.pk, tipo_questao="MULTIPLA_ESCOLHA")
        self.assertEqual(self.client.post(self.url(), data).status_code, 400)

    def image(self):
        buffer = BytesIO()
        Image.new("RGB", (2, 2), color="white").save(buffer, format="PNG")
        return SimpleUploadedFile("figura.png", buffer.getvalue(), content_type="image/png")

    def test_image_can_be_added_and_removed_and_original_date_does_not_change(self):
        original_date = self.question.enviada_em
        data = self.payload()
        data["imagem"] = self.image()
        self.assertEqual(self.client.post(self.url(), data).status_code, 302)
        self.question.refresh_from_db()
        self.assertTrue(self.question.imagem.storage.exists(self.question.imagem.name))
        data = self.payload()
        data["imagem-clear"] = "on"
        self.assertEqual(self.client.post(self.url(), data).status_code, 302)
        self.question.refresh_from_db()
        self.assertFalse(self.question.imagem)
        self.assertEqual(self.question.enviada_em, original_date)

    def test_failure_rolls_back_content_alternatives_audit_history_and_uploaded_file(self):
        Questao.objects.filter(pk=self.question.pk).update(status="REPROVADA")
        before = snapshot_questao(self.question)
        data = self.payload()
        data["alternativas-0-texto"] = "Texto a reverter"
        data["imagem"] = self.image()
        with patch("questoes.revisao.registrar_decisao", side_effect=OperationalError("private detail")):
            response = self.client.post(self.url(), data)
        self.assertEqual(response.status_code, 503)
        self.assertContains(response, "Enunciado reformulado", status_code=503)
        self.assertNotContains(response, "private detail", status_code=503)
        self.assertContains(response, "Precisa de Revisões", status_code=503)
        self.question.refresh_from_db()
        self.assertEqual(snapshot_questao(self.question), before)
        self.assertFalse(ParecerPreAuditoria.objects.exists())
        self.assertFalse(self.question.alteracoes_pre_auditoria.exists())
        self.assertEqual([path for path in Path(self.media.name).rglob("*") if path.is_file()], [])

    def test_failed_image_replacement_preserves_original_file_and_all_records(self):
        self.question.imagem = self.image()
        self.question.save()
        original_name = self.question.imagem.name
        before = snapshot_questao(self.question)
        data = self.payload()
        data["imagem"] = self.image()
        with self.captureOnCommitCallbacks(execute=True):
            with patch("questoes.revisao.registrar_decisao", side_effect=OperationalError("falha simulada")):
                self.assertEqual(self.client.post(self.url(), data).status_code, 503)
        self.question.refresh_from_db()
        self.assertEqual(snapshot_questao(self.question), before)
        self.assertTrue(self.question.imagem.storage.exists(original_name))
        self.assertEqual(len([path for path in Path(self.media.name).rglob("*") if path.is_file()]), 1)
