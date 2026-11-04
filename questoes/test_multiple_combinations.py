import csv
import io
from types import SimpleNamespace
from unittest.mock import Mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from pre_auditoria.services import fingerprint_questao

from .historico import contexto_historico
from .models import Alternativa, ComponenteCurricular, Periodo, Questao, Semestre
from .multiple_choice import (
    gerar_alternativas_multipla_escolha,
    interpretar_combinacoes,
    linhas_combinacoes,
    validar_combinacoes,
)


class CombinationBalanceTests(SimpleTestCase):
    def test_every_truth_pattern_and_seed_requires_more_than_one_statement(self):
        for count in (3, 4, 5):
            for key in range(1, (1 << count) - 1):
                statements = [SimpleNamespace(eh_correta=bool(key & (1 << i))) for i in range(count)]
                manager = Mock()
                manager.all.return_value.order_by.return_value = statements
                for seed in range(1, 21):
                    with self.subTest(count=count, key=key, seed=seed):
                        question = SimpleNamespace(
                            pk=seed, alternativas=manager, combinacoes_personalizadas=[]
                        )
                        options = gerar_alternativas_multipla_escolha(question)
                        self.assertEqual(len(options), 4)
                        self.assertEqual(len({tuple(o["indices"]) for o in options}), 4)
                        self.assertEqual(sum(o["eh_correta"] for o in options), 1)
                        correct = next(o for o in options if o["eh_correta"])
                        self.assertEqual(correct["indices"], [i + 1 for i in range(count) if key & (1 << i)])
                        for i, statement in enumerate(statements, start=1):
                            compatible = [o for o in options if (i in o["indices"]) == statement.eh_correta]
                            self.assertEqual(len(compatible), 2)
                            self.assertIn(correct, compatible)
                        self.assertEqual(options, gerar_alternativas_multipla_escolha(question))

    def test_manual_parser_and_validator_reject_missing_key_duplicates_ranges_and_clues(self):
        statements = [{"eh_correta": True}, {"eh_correta": True}, {"eh_correta": False}]
        valid = [[1, 2], [1, 3], [2], [3]]
        self.assertEqual(interpretar_combinacoes(linhas_combinacoes(valid)), valid)
        validar_combinacoes(valid, statements)
        for invalid in (
            [[1, 2], [1, 2], [2], [3]],
            [[1], [2], [3], [1, 3]],
            [[1, 2], [1, 3], [2], [4]],
            [[1, 2], [1, 3], [1], [1, 2, 3]],
            [[1, 2], [1, 3], [True], [3]],
            [[1, 2], [], [2], [3]],
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                validar_combinacoes(invalid, statements)
        for invalid in ("I, I", "<script>alert(1)</script>", "A) VI"):
            with self.assertRaises(ValidationError):
                interpretar_combinacoes(invalid)


class ManualCombinationAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        semester = Semestre.objects.create(nome="2026.2")
        period = Periodo.objects.create(nome="4º Período", semestre=semester)
        cls.component = ComponenteCurricular.objects.create(
            nome="Clínica", periodo=period, numero_questoes_prova=1
        )
        cls.user = get_user_model().objects.create_superuser("comb-admin", "admin@example.com", "teste")
        cls.question = Questao.objects.create(
            componente=cls.component,
            tipo_questao="MULTIPLA_ESCOLHA",
            status="APROVADA",
            professor_nome="Docente sintético",
            professor_email="docente@example.com",
            texto_base="Contexto sintético",
            enunciado="Comando personalizado da questão.",
            justificativa="Justificativa das afirmativas.",
        )
        for i in range(3):
            Alternativa.objects.create(questao=cls.question, texto=f"Afirmativa {i + 1}", eh_correta=i < 2)

    def setUp(self):
        self.client.force_login(self.user)
        self.url = reverse("admin:questoes_questao_change", args=[self.question.pk])
        self.manual = [[1, 2], [1, 3], [2], [3]]

    def payload(self, lines=None):
        question = self.question
        data = {
            "componente": self.component.pk,
            "tipo_questao": "MULTIPLA_ESCOLHA",
            "uso_prova": "INTEGRADA",
            "texto_base": question.texto_base,
            "enunciado": question.enunciado,
            "justificativa": question.justificativa,
            "comentario_validacao": "",
            "combinacoes_personalizadas": lines if lines is not None else linhas_combinacoes(self.manual),
            "alternativas-TOTAL_FORMS": "3",
            "alternativas-INITIAL_FORMS": "3",
            "_save": "Salvar",
        }
        for i, alt in enumerate(question.alternativas.order_by("pk")):
            data[f"alternativas-{i}-id"] = alt.pk
            data[f"alternativas-{i}-texto"] = alt.texto
            if alt.eh_correta:
                data[f"alternativas-{i}-eh_correta"] = "on"
        return data

    def test_edit_statements_and_combinations_persists_and_history_matches_exam(self):
        self.assertContains(self.client.get(self.url), "Carregar combinações atuais para editar")
        data = self.payload()
        data["alternativas-0-texto"] = "Afirmativa I editada no admin"
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        self.question.refresh_from_db()
        self.assertEqual(self.question.combinacoes_personalizadas, self.manual)
        self.assertEqual(
            self.question.alternativas.order_by("pk").first().texto, data["alternativas-0-texto"]
        )
        options = gerar_alternativas_multipla_escolha(self.question)
        self.assertEqual([o["indices"] for o in options], self.manual)
        fields = {f["campo"]: f for f in contexto_historico(self.question)["registros"][0]["campos"]}
        self.assertEqual(fields["Combinações finais (A–D)"]["antes"], "Geradas automaticamente")
        self.assertIn("A) I, II", fields["Combinações finais (A–D)"]["depois"])
        self.assertEqual(
            self.question.pareceres_pre_auditoria.first().fingerprint, fingerprint_questao(self.question)
        )
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        self.assertEqual(self.question.alteracoes_pre_auditoria.count(), 1)
        Semestre.objects.create(nome="2027.1", ativo=True)
        params = {"componentes": self.component.pk, f"quantidade_{self.component.pk}": 1}
        preview = self.client.get(reverse("prova_gerada"), {**params, "preflight": "1"}).json()
        exam = self.client.get(preview["url_abertura"])
        self.assertContains(exam, self.question.enunciado)
        self.assertContains(exam, "Semestre 2026.2")
        self.assertNotContains(exam, "2027.1")
        selected = exam.context["questoes_selecionadas"][0]
        self.assertEqual(
            {tuple(o["indices"]) for o in selected.alternativas_finais}, {tuple(x) for x in self.manual}
        )
        key = next("ABCD"[i] for i, option in enumerate(selected.alternativas_finais) if option["eh_correta"])
        exported = self.client.get(reverse("prova_gabarito_csv"), {"ids": self.question.pk})
        rows = list(csv.DictReader(io.StringIO(exported.content.decode("utf-8-sig"))))
        self.assertEqual(rows[0]["resposta_correta"], key)
        self.assertEqual(rows[0]["codigo_prova"], exam.context["codigo_prova"])

    def test_resubmission_reactivates_auto_generation_if_truth_changes_invalidate_manual_options(self):
        self.question.combinacoes_personalizadas = self.manual
        self.question.save()
        url = reverse("submeter_editar_questao", args=[self.question.pk]) + f"?componente={self.component.pk}"
        token = self.client.get(url).context["edicao_token"]
        data = self.payload()
        data.update(
            edicao_token=token,
            confirmar_reenvio="on",
            professor_nome=self.question.professor_nome,
            professor_email=self.question.professor_email,
        )
        data.pop("alternativas-0-eh_correta")
        data["alternativas-2-eh_correta"] = "on"
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.question.refresh_from_db()
        self.assertEqual(self.question.combinacoes_personalizadas, [])
        self.assertEqual(
            sum(option["eh_correta"] for option in gerar_alternativas_multipla_escolha(self.question)), 1
        )
        fields = {f["campo"]: f for f in contexto_historico(self.question)["registros"][0]["campos"]}
        self.assertEqual(fields["Combinações finais (A–D)"]["depois"], "Geradas automaticamente")

    def test_invalid_combinations_or_updated_truth_pattern_roll_back_all_fields(self):
        for lines in ("I, II\nI, III\nI\nI, II, III", "I, II\nI, III\nII\nIV", "I, II\nII\nIII"):
            data = self.payload(lines)
            data["enunciado"] = "Não deve ser salvo"
            response = self.client.post(self.url, data)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Não deve ser salvo")
            self.question.refresh_from_db()
            self.assertEqual(self.question.enunciado, "Comando personalizado da questão.")
            self.assertEqual(self.question.combinacoes_personalizadas, [])
        data = self.payload()
        data.pop("alternativas-0-eh_correta")
        data["alternativas-2-eh_correta"] = "on"
        self.assertContains(self.client.post(self.url, data), "exatamente a todas as afirmativas corretas")
        self.assertFalse(self.question.alteracoes_pre_auditoria.exists())

    def test_blank_restores_automatic_and_view_only_user_cannot_change_combinations(self):
        self.question.combinacoes_personalizadas = self.manual
        self.question.save()
        self.assertEqual(self.client.post(self.url, self.payload("")).status_code, 302)
        self.question.refresh_from_db()
        self.assertEqual(self.question.combinacoes_personalizadas, [])
        viewer = get_user_model().objects.create_user("comb-viewer", is_staff=True)
        viewer.user_permissions.add(Permission.objects.get(codename="view_questao"))
        self.client.force_login(viewer)
        self.assertContains(self.client.get(self.url), "Combinações utilizadas")
        self.assertEqual(self.client.post(self.url, self.payload()).status_code, 403)

    def test_invalid_stored_configuration_blocks_exam(self):
        self.question.combinacoes_personalizadas = [[1], [2], [3], [1, 3]]
        self.question.save()
        response = self.client.get(
            reverse("prova_gerada"), {"componentes": self.component.pk, "preflight": "1"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("inválidas", response.json()["erro"])
