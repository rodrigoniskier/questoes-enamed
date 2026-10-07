import json
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import OperationalError
from django.test import Client, TestCase
from django.urls import reverse

from .models import Alternativa, ComponenteCurricular, Periodo, Questao, Semestre, SubmissionReceipt
from .submission import new_submission_token


class SubmissionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        semester = Semestre.objects.create(nome="2026.1")
        period = Periodo.objects.create(nome="3º Período", semestre=semester)
        cls.component = ComponenteCurricular.objects.create(nome="Clínica", periodo=period)
        cls.other = ComponenteCurricular.objects.create(nome="Cirurgia", periodo=period)
        internship = Periodo.objects.create(nome="Estágio Curricular Obrigatório", semestre=semester)
        cls.internship = ComponenteCurricular.objects.create(nome="Internato", periodo=internship)
        cls.staff = get_user_model().objects.create_user("reviewer", is_staff=True)

    def form_url(self, style="RESPOSTA_UNICA", component=None):
        routes = {
            "RESPOSTA_UNICA": "submeter_resposta_unica",
            "MULTIPLA_ESCOLHA": "submeter_resposta_multipla",
            "ASSERCAO_RAZAO": "submeter_assercao_razao",
        }
        return reverse(routes[style]) + f"?componente={(component or self.component).pk}"

    def payload(self, style="RESPOSTA_UNICA", action="same_style", component=None):
        component = component or self.component
        token = self.client.get(self.form_url(style, component)).context["submission_token"]
        count = 4 if component == self.internship else 5
        data = {
            "professor_nome": "Professora Exemplo",
            "professor_email": "professora@example.com",
            "uso_prova": "REPOSICAO",
            "componente": str(component.pk),
            "tipo_questao": style,
            "texto_base": "Contexto de uma questão sintética.",
            "enunciado": "Qual a conduta?",
            "justificativa": "Justificativa sintética completa.",
            "submission_token": token,
            "submit_action": action,
            "alternativas-TOTAL_FORMS": str(count),
            "alternativas-INITIAL_FORMS": "0",
            "alternativas-MAX_NUM_FORMS": str(count),
        }
        for index in range(count):
            data[f"alternativas-{index}-texto"] = f"Alternativa {index + 1}"
        data["alternativas-0-eh_correta"] = "on"
        if style == "MULTIPLA_ESCOLHA":
            data["alternativas-1-eh_correta"] = "on"
        if style == "ASSERCAO_RAZAO":
            data["proposicao_dois"] = "Proposição II sintética."
            data["assercao_gabarito"] = "B"
        return data

    def test_same_style_preserves_only_header_and_clears_all_content(self):
        for style in ("RESPOSTA_UNICA", "MULTIPLA_ESCOLHA", "ASSERCAO_RAZAO"):
            with self.subTest(style=style):
                response = self.client.post(self.form_url(style), self.payload(style), follow=True)
                self.assertEqual(response.status_code, 200)
                form = response.context["questao_form"]
                self.assertEqual(form["professor_nome"].value(), "Professora Exemplo")
                self.assertEqual(form["professor_email"].value(), "professora@example.com")
                self.assertEqual(form["uso_prova"].value(), "REPOSICAO")
                self.assertEqual(form["componente"].value(), self.component.pk)
                self.assertEqual(form["tipo_questao"].value(), style)
                for field in ("texto_base", "enunciado", "proposicao_dois", "justificativa", "imagem"):
                    self.assertFalse(form[field].value())
                self.assertTrue(all(not f["texto"].value() for f in response.context["alternativa_formset"]))
                self.assertEqual(response.context["assercao_gabarito"], "")
        self.assertEqual(Questao.objects.count(), 3)

    def test_other_style_returns_to_style_selection_for_same_component(self):
        response = self.client.post(self.form_url(), self.payload(action="other_style"), follow=True)
        self.assertContains(response, "Selecione outro estilo")
        self.assertEqual(response.context["componente_continuacao"], self.component)
        for style in response.context["estilos"]:
            next_page = self.client.get(style["url"])
            self.assertEqual(
                next_page.context["questao_form"]["professor_email"].value(), "professora@example.com"
            )
            self.assertFalse(next_page.context["questao_form"]["texto_base"].value())

    def test_finish_does_not_prefill_unrelated_new_submission(self):
        response = self.client.post(self.form_url(), self.payload(action="finish"))
        self.assertRedirects(response, reverse("submeter_selecao"))
        self.assertFalse(self.client.get(self.form_url()).context["questao_form"]["professor_nome"].value())

    def test_replayed_post_creates_only_one_question(self):
        data = self.payload()
        self.client.post(self.form_url(), data)
        self.client.post(self.form_url(), data)
        self.assertEqual(Questao.objects.count(), 1)
        self.assertEqual(Alternativa.objects.count(), 5)
        self.assertEqual(SubmissionReceipt.objects.count(), 1)

    def test_deleted_question_cannot_be_recreated_by_replayed_post(self):
        for action in ("finish", "same_style", "other_style"):
            with self.subTest(action=action):
                data = self.payload(action=action)
                self.client.post(self.form_url(), data)
                question = Questao.objects.get()
                receipt = SubmissionReceipt.objects.get(questao=question)
                question.delete()
                receipt.refresh_from_db()
                self.assertIsNone(receipt.questao_id)
                response = self.client.post(self.form_url(), data)
                self.assertRedirects(response, reverse("submeter_selecao"))
                self.assertEqual(Questao.objects.count(), 0)
                self.assertTrue(SubmissionReceipt.objects.filter(pk=receipt.pk).exists())

    def test_bad_submission_preserves_text_without_partial_save(self):
        for field, value in [
            ("enunciado", ""),
            ("professor_email", "invalid"),
            ("texto_base", ""),
            ("componente", str(self.other.pk)),
            ("tipo_questao", "MULTIPLA_ESCOLHA"),
            ("submission_token", "tampered"),
            ("submit_action", "invalid"),
        ]:
            with self.subTest(field=field):
                data = self.payload()
                data[field] = value
                response = self.client.post(self.form_url(), data)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["questao_form"].errors)
                self.assertEqual(
                    response.context["questao_form"]["justificativa"].value(), data["justificativa"]
                )
                self.assertEqual(Questao.objects.count(), 0)

    def test_invalid_alternative_counts_and_gabarito(self):
        for change in ("none_correct", "two_correct", "missing_text", "duplicate_text", "too_many"):
            data = self.payload()
            if change == "none_correct":
                del data["alternativas-0-eh_correta"]
            if change == "two_correct":
                data["alternativas-1-eh_correta"] = "on"
            if change == "missing_text":
                data["alternativas-2-texto"] = ""
            if change == "duplicate_text":
                data["alternativas-2-texto"] = data["alternativas-0-texto"]
            if change == "too_many":
                data["alternativas-TOTAL_FORMS"] = "1000000"
            response = self.client.post(self.form_url(), data)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(Questao.objects.count(), 0)

    def test_assertion_requires_proposition_and_answer(self):
        for field in ("proposicao_dois", "assercao_gabarito"):
            data = self.payload("ASSERCAO_RAZAO")
            data[field] = ""
            self.client.post(self.form_url("ASSERCAO_RAZAO"), data)
            self.assertEqual(Questao.objects.count(), 0)

    def test_multiple_answer_requires_true_and_false_statements(self):
        for all_true in (False, True):
            data = self.payload("MULTIPLA_ESCOLHA")
            for index in range(5):
                if all_true:
                    data[f"alternativas-{index}-eh_correta"] = "on"
                else:
                    data.pop(f"alternativas-{index}-eh_correta", None)
            self.client.post(self.form_url("MULTIPLA_ESCOLHA"), data)
            self.assertEqual(Questao.objects.count(), 0)

    def test_atomic_save_rolls_back_question_and_receipt_if_alternatives_fail(self):
        data = self.payload()
        with patch(
            "questoes.submission.Alternativa.objects.bulk_create",
            side_effect=OperationalError("private details"),
        ):
            response = self.client.post(self.form_url(), data)
        self.assertContains(response, "Seus textos foram preservados")
        self.assertNotContains(response, "private details")
        self.assertEqual(Questao.objects.count(), 0)
        self.assertEqual(SubmissionReceipt.objects.count(), 0)
        retry = self.client.post(self.form_url(), data)
        self.assertEqual(retry.status_code, 302)
        self.assertEqual(Questao.objects.count(), 1)

    def test_header_is_private_to_session_component_and_browser_tab(self):
        first = self.client.post(self.form_url(), self.payload())["Location"]
        second_data = self.payload()
        second_data["professor_nome"] = "Outro nome"
        second = self.client.post(self.form_url(), second_data)["Location"]
        self.assertEqual(
            self.client.get(first).context["questao_form"]["professor_nome"].value(), "Professora Exemplo"
        )
        self.assertEqual(
            self.client.get(second).context["questao_form"]["professor_nome"].value(), "Outro nome"
        )
        self.assertFalse(Client().get(first).context["questao_form"]["professor_nome"].value())
        unrelated = first.replace(f"componente={self.component.pk}", f"componente={self.other.pk}")
        self.assertFalse(self.client.get(unrelated).context["questao_form"]["professor_nome"].value())

    def test_internship_has_four_alternatives_and_only_single_answer(self):
        response = self.client.get(self.form_url(component=self.internship))
        self.assertEqual(len(response.context["alternativa_formset"]), 4)
        self.assertEqual(self.client.get(self.form_url("MULTIPLA_ESCOLHA", self.internship)).status_code, 302)
        response = self.client.post(
            self.form_url(component=self.internship),
            self.payload(component=self.internship, action="other_style"),
            follow=True,
        )
        self.assertEqual(len(response.context["estilos"]), 1)

    def test_csrf_is_required(self):
        response = Client(enforce_csrf_checks=True).post(self.form_url(), self.payload())
        self.assertEqual(response.status_code, 403)

    def test_management_write_requires_staff_and_is_component_scoped(self):
        question = Questao.objects.create(
            componente=self.other,
            professor_nome="Example",
            professor_email="example@example.com",
            enunciado="Example",
            justificativa="Example",
        )
        url = reverse("submeter_visualizar_questoes") + f"?componente={self.component.pk}"
        payload = {f"uso_prova_{question.pk}": "SIMULADO"}
        self.assertEqual(self.client.post(url, payload).status_code, 403)
        self.client.force_login(self.staff)
        self.client.post(url, payload)
        question.refresh_from_db()
        self.assertEqual(question.uso_prova, "INTEGRADA")

    def test_exam_and_bulk_tools_require_staff(self):
        for name in (
            "pagina_gerar_prova",
            "montador_manual",
            "prova_gerada",
            "prova_gabarito",
            "bulk_submit_form",
            "upload_grade_csv",
        ):
            self.assertEqual(self.client.get(reverse(name)).status_code, 302)

    def test_staff_manual_builder_renders_both_steps(self):
        self.client.force_login(self.staff)
        url = reverse("montador_manual")
        response = self.client.get(url)
        self.assertContains(response, "Passo 1: Selecione o Período")
        self.assertNotContains(response, "Passo 2: Selecione as Questões")
        question = Questao.objects.create(
            componente=self.component,
            professor_nome="Professor sintético",
            professor_email="professor@example.com",
            enunciado="Comando sintético",
            justificativa="Justificativa sintética",
        )
        Alternativa.objects.create(questao=question, texto="Resposta sintética", eh_correta=True)
        response = self.client.get(url, {"periodo_id": self.component.periodo_id})
        self.assertContains(response, "Passo 2: Selecione as Questões")
        self.assertNotContains(response, "Passo 1: Selecione o Período")
        self.assertContains(response, "A</p>")

    def test_bad_query_ids_return_client_errors(self):
        self.assertEqual(self.client.get(reverse("api_get_componentes") + "?periodo_id=abc").status_code, 400)
        self.assertEqual(
            self.client.get(reverse("api_get_historico") + "?componente_id=abc").status_code, 400
        )


class NotificationTests(TestCase):
    def setUp(self):
        from django.contrib.admin.sites import AdminSite

        from .admin import QuestaoAdmin

        period = Periodo.objects.create(nome="1º Período")
        component = ComponenteCurricular.objects.create(nome="Anatomia", periodo=period)
        self.questions = [
            Questao.objects.create(
                componente=component,
                professor_nome="Professor sintético",
                professor_email=f"professor{index}@example.com",
                enunciado="Comando sintético",
                justificativa="Justificativa sintética",
            )
            for index in range(2)
        ]
        self.admin = QuestaoAdmin(Questao, AdminSite())

    @patch("questoes.admin.QuestaoAdmin.message_user")
    @patch("questoes.admin.enviar_email_status")
    def test_retry_sends_only_failed_notifications(self, notify, message):
        notify.side_effect = [None, RuntimeError("SMTP failure")]
        self.admin._set_status_and_notify(None, Questao.objects.order_by("pk"), "APROVADA")
        self.assertEqual(notify.call_count, 2)
        self.questions[0].refresh_from_db()
        self.questions[1].refresh_from_db()
        self.assertEqual(self.questions[0].notified_status, "APROVADA")
        self.assertEqual(self.questions[1].notified_status, "")
        notify.reset_mock()
        notify.side_effect = None
        self.admin._set_status_and_notify(None, Questao.objects.order_by("pk"), "APROVADA")
        self.assertEqual(notify.call_count, 1)
        self.assertEqual(notify.call_args.args[0].pk, self.questions[1].pk)
        notify.reset_mock()
        self.admin._set_status_and_notify(None, Questao.objects.order_by("pk"), "APROVADA")
        notify.assert_not_called()

    @patch("questoes.admin.QuestaoAdmin.message_user")
    @patch("questoes.admin.enviar_email_status")
    def test_new_status_notifies_again_after_success(self, notify, message):
        self.admin._set_status_and_notify(None, Questao.objects.order_by("pk"), "APROVADA")
        self.admin._set_status_and_notify(None, Questao.objects.order_by("pk"), "REPROVADA")
        self.assertEqual(notify.call_count, 4)
        self.assertEqual(Questao.objects.filter(notified_status="REPROVADA").count(), 2)


class GenerationTests(TestCase):
    def setUp(self):
        period = Periodo.objects.create(nome="1º Período")
        self.component = ComponenteCurricular.objects.create(nome="Anatomia", periodo=period)
        self.payload = {
            "submission_token": new_submission_token(self.component, "RESPOSTA_UNICA"),
            "request_id": str(uuid.uuid4()),
            "prompt_professor": "Tema sintético",
            "componente_id": self.component.pk,
            "tipo_questao": "RESPOSTA_UNICA",
            "num_alternativas": 5,
        }

    def post(self, payload):
        return self.client.post(
            reverse("api_gerar_questao"), json.dumps(payload), content_type="application/json"
        )

    def draft(self):
        return {
            "texto_base": "Contexto",
            "enunciado": "Comando",
            "justificativa": "Justificativa",
            "alternativas": [{"texto": f"Opção {i}", "eh_correta": i == 0} for i in range(5)],
        }

    @patch("questoes.views.gerar_questao_com_ia")
    def test_invalid_inputs_do_not_call_provider(self, generate):
        for payload in [
            [],
            None,
            {},
            {**self.payload, "tipo_questao": "bad"},
            {**self.payload, "num_alternativas": "5"},
            {**self.payload, "prompt_professor": " "},
            {**self.payload, "area_primaria": []},
            {**self.payload, "componente_id": "invalid"},
        ]:
            self.assertEqual(self.post(payload).status_code, 400)
        generate.assert_not_called()

    @patch("questoes.views.gerar_questao_com_ia")
    def test_malformed_drafts_never_reach_form(self, generate):
        for draft in [
            [],
            None,
            {},
            {**self.draft(), "alternativas": []},
            {**self.draft(), "alternativas": [{"texto": "x", "eh_correta": "false"}] * 5},
        ]:
            generate.return_value = draft
            self.assertEqual(self.post(self.payload).status_code, 502)
        self.assertEqual(Questao.objects.count(), 0)

    @patch("questoes.views.gerar_questao_com_ia")
    def test_valid_draft_is_returned_without_persisting(self, generate):
        generate.return_value = self.draft()
        self.assertEqual(self.post(self.payload).json(), self.draft())
        self.assertEqual(Questao.objects.count(), 0)

    @patch("questoes.views.gerar_questao_com_ia", side_effect=RuntimeError("secret details"))
    def test_external_failure_has_safe_message(self, generate):
        response = self.post(self.payload)
        self.assertEqual(response.status_code, 503)
        self.assertNotContains(response, "secret details", status_code=503)


class AILimitResponseTests(GenerationTests):
    @patch("questoes.views.gerar_questao_com_ia")
    def test_429_contract_and_retry_header(self, generate):
        from .ai_control import AIControlError

        generate.side_effect = AIControlError("Aguarde; seus dados foram preservados.", "cota_ia", 429, 60)
        response = self.post(self.payload)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Retry-After"], "60")
        self.assertEqual(response.json()["codigo"], "cota_ia")

    @patch("questoes.views.gerar_questao_com_ia")
    def test_invalid_authorization_never_calls_provider(self, generate):
        for field in ("submission_token", "request_id"):
            self.assertEqual(self.post({**self.payload, field: "invalid"}).status_code, 400)
        generate.assert_not_called()


class AsyncSubmissionTests(SubmissionTests):
    def test_json_submission_and_replay_return_same_continuation(self):
        data = self.payload()
        response = self.client.post(self.form_url(), data, HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 200)
        follow = self.client.get(response.json()["redirect"])
        self.assertEqual(follow.context["questao_form"]["professor_nome"].value(), "Professora Exemplo")
        self.assertFalse(follow.context["questao_form"]["enunciado"].value())
        replay = self.client.post(self.form_url(), data, HTTP_ACCEPT="application/json")
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(Questao.objects.count(), 1)

    def test_json_failure_keeps_receipt_for_safe_retry(self):
        data = self.payload()
        with patch("questoes.views.save_submission", side_effect=OperationalError("synthetic")):
            response = self.client.post(self.form_url(), data, HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["submission_token"], data["submission_token"])
        self.assertEqual(Questao.objects.count(), 0)
        self.assertEqual(
            self.client.post(self.form_url(), data, HTTP_ACCEPT="application/json").status_code, 200
        )
        self.assertEqual(Questao.objects.count(), 1)

    def test_json_csrf_failure_is_recoverable(self):
        response = Client(enforce_csrf_checks=True).post(
            self.form_url(), self.payload(), HTTP_ACCEPT="application/json"
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["codigo"], "csrf_invalido")
