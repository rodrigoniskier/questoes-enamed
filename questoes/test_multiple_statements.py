"""The current semester accepts 3–5 statements, independently of final options."""

import json
import uuid
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from bulk_submit.utils import validate_import
from questoes.models import ComponenteCurricular, Periodo, Questao, Semestre
from questoes.submission import new_submission_token
from questoes.utils import gerar_questao_com_ia
from questoes.views import gerar_alternativas_multipla_escolha


class MultipleStatementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        semester = Semestre.objects.create(nome="2026.2")
        period = Periodo.objects.create(nome="4º Período", semestre=semester)
        cls.component = ComponenteCurricular.objects.create(nome="Clínica", periodo=period)

    def form_url(self):
        return reverse("submeter_resposta_multipla") + f"?componente={self.component.pk}"

    def draft(self, count):
        return {
            "texto_base": "Contexto sintético para teste isolado.",
            "enunciado": "Avalie as afirmativas.",
            "justificativa": "Justificativa sintética de todas as afirmativas.",
            "alternativas": [{"texto": f"Afirmativa {i + 1}", "eh_correta": i < 2} for i in range(count)],
        }

    def submission(self, count):
        response = self.client.get(self.form_url())
        draft = self.draft(count)
        data = {
            **{key: value for key, value in draft.items() if key != "alternativas"},
            "professor_nome": "Professor Sintético",
            "professor_email": "professor@example.com",
            "uso_prova": "INTEGRADA",
            "componente": str(self.component.pk),
            "tipo_questao": "MULTIPLA_ESCOLHA",
            "submission_token": response.context["submission_token"],
            "submit_action": "finish",
            "alternativas-TOTAL_FORMS": str(max(5, count)),
            "alternativas-INITIAL_FORMS": "0",
            "alternativas-MAX_NUM_FORMS": "5",
        }
        for i, statement in enumerate(draft["alternativas"]):
            data[f"alternativas-{i}-texto"] = statement["texto"]
            if statement["eh_correta"]:
                data[f"alternativas-{i}-eh_correta"] = "on"
        return data

    def test_form_has_five_slots_and_single_answer_keeps_four(self):
        response = self.client.get(self.form_url())
        self.assertContains(response, "Crie de 3 a 5 afirmativas.")
        self.assertEqual(response.context["alternativa_formset"].total_form_count(), 5)
        self.assertContains(response, 'id="id_alternativas-4-texto"')
        single = self.client.get(reverse("submeter_resposta_unica") + f"?componente={self.component.pk}")
        self.assertEqual(single.context["alternativa_formset"].total_form_count(), 4)

    def test_three_four_five_statements_save_and_generate_four_unique_options(self):
        for count in (3, 4, 5):
            with self.subTest(count=count):
                response = self.client.post(self.form_url(), self.submission(count))
                self.assertEqual(response.status_code, 302)
                question = Questao.objects.latest("pk")
                self.assertEqual(question.alternativas.count(), count)
                options = gerar_alternativas_multipla_escolha(question)
                self.assertEqual(len(options), 4)
                self.assertEqual(len({option["texto"] for option in options}), 4)
                self.assertEqual(sum(option["eh_correta"] for option in options), 1)
                self.assertTrue(
                    any("I e II" in option["texto"] and option["eh_correta"] for option in options)
                )

    def test_out_of_range_and_invalid_truth_patterns_do_not_save(self):
        cases = [self.submission(2), self.submission(6)]
        all_true = self.submission(5)
        all_false = self.submission(5)
        for i in range(5):
            all_true[f"alternativas-{i}-eh_correta"] = "on"
            all_false.pop(f"alternativas-{i}-eh_correta", None)
        marked_empty = self.submission(3)
        marked_empty["alternativas-4-eh_correta"] = "on"
        for data in [*cases, all_true, all_false, marked_empty]:
            response = self.client.post(self.form_url(), data, HTTP_ACCEPT="application/json")
            self.assertEqual(response.status_code, 422)
            self.assertIn("erro", response.json())
            self.assertIn("submission_token", response.json())
        self.assertEqual(Questao.objects.count(), 0)

    @patch("questoes.views.gerar_questao_com_ia")
    def test_ai_accepts_three_to_five_including_previous_four_slot_forms(self, generate):
        for capacity in (3, 4, 5):
            for count in (3, 4, 5):
                with self.subTest(capacity=capacity, count=count):
                    generate.return_value = self.draft(count)
                    payload = {
                        "prompt_professor": "Tema sintético",
                        "componente_id": self.component.pk,
                        "tipo_questao": "MULTIPLA_ESCOLHA",
                        "num_alternativas": capacity,
                        "submission_token": new_submission_token(self.component, "MULTIPLA_ESCOLHA"),
                        "request_id": str(uuid.uuid4()),
                    }
                    response = self.client.post(
                        reverse("api_gerar_questao"), json.dumps(payload), content_type="application/json"
                    )
                    self.assertEqual(response.status_code, 200 if count <= capacity else 502)
                    if count <= capacity:
                        self.assertEqual(len(response.json()["alternativas"]), count)
        self.assertEqual(Questao.objects.count(), 0)

    @patch("questoes.views.gerar_questao_com_ia")
    def test_ai_rejects_out_of_range_request_before_provider_and_invalid_draft(self, generate):
        payload = {
            "prompt_professor": "Tema sintético",
            "componente_id": self.component.pk,
            "tipo_questao": "MULTIPLA_ESCOLHA",
            "submission_token": new_submission_token(self.component, "MULTIPLA_ESCOLHA"),
            "request_id": str(uuid.uuid4()),
        }
        for count in (2, 6, "5", True):
            response = self.client.post(
                reverse("api_gerar_questao"),
                json.dumps({**payload, "num_alternativas": count}),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 400)
        generate.assert_not_called()
        for count in (2, 6):
            generate.return_value = self.draft(count)
            response = self.client.post(
                reverse("api_gerar_questao"),
                json.dumps({**payload, "num_alternativas": 5}),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 502)

    def test_new_imports_accept_three_four_five_statements(self):
        for count in (3, 4, 5):
            item = {**self.draft(count), "tipo_questao": "MULTIPLA_ESCOLHA"}
            self.assertEqual(validate_import([item], self.component), [item])

    @override_settings(GEMINI_API_KEY="synthetic-only")
    @patch("questoes.utils.generate_text")
    def test_ai_instructions_use_statement_range_and_multiple_true_answers(self, generate):
        generate.return_value = json.dumps(self.draft(5))
        result = gerar_questao_com_ia(
            "Tema sintético",
            self.component,
            "MULTIPLA_ESCOLHA",
            5,
            request_id=str(uuid.uuid4()),
            subject="synthetic",
        )
        self.assertEqual(len(result["alternativas"]), 5)
        prompt = generate.call_args.args[0]
        self.assertIn("Crie de 3 a 5 afirmativas", prompt)
        self.assertNotIn("Crie exatas 5 alternativas", prompt)
        self.assertNotIn("APENAS UMA resposta", prompt)
        self.assertIn("quatro alternativas finais", prompt)
