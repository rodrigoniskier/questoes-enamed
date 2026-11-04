"""Regressão de comandos duplicados na prova, com banco e serviços isolados."""

import os
from pathlib import Path
from unittest import skipUnless
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from .models import Alternativa, ComponenteCurricular, Periodo, Questao, Semestre
from .submission import MULTIPLE_STATEMENT_PROMPT


class ExamPromptTests(SimpleTestCase):
    def question(self, texto_base, enunciado=MULTIPLE_STATEMENT_PROMPT, tipo="MULTIPLA_ESCOLHA"):
        return Questao(texto_base=texto_base, enunciado=enunciado, tipo_questao=tipo)

    def test_automatic_prompt_is_omitted_after_existing_instruction(self):
        for comando in (
            MULTIPLE_STATEMENT_PROMPT,
            "Analise as afirmativas a seguir:",
            "Com base no caso, julgue os itens abaixo.",
            "Acerca do tema apresentado, avalie as assertivas a seguir.",
            "Considere as proposições abaixo.",
            "Assinale a alternativa correta.",
            "Selecione a opção que apresenta apenas afirmativas corretas.",
        ):
            for separador in (" ", "\n\n", "\r\n"):
                with self.subTest(comando=comando, separador=separador):
                    questao = self.question(f"Contexto do caso.{separador}{comando}")
                    self.assertEqual(questao.enunciado_para_prova, "")
                    self.assertEqual(questao.enunciado, MULTIPLE_STATEMENT_PROMPT)

    def test_accents_case_spaces_and_final_punctuation_do_not_duplicate(self):
        questao = self.question(
            "Contexto do caso. CONSIDERANDO O TEXTO APRESENTADO, AVALIE AS AFIRMACOES A SEGUIR:",
            "  Considerando o texto apresentado,   avalie as afirmações a seguir.  ",
        )
        self.assertEqual(questao.enunciado_para_prova, "")

    def test_wrapped_instructions_and_paragraph_without_final_period(self):
        for texto_base in (
            "Contexto do caso. Considerando o texto apresentado, avalie as\nafirmações a seguir.",
            "Contexto sem ponto final\nAnalise as afirmativas a seguir:",
            "Contexto do caso.\nAnalise as\nafirmativas a seguir:",
        ):
            with self.subTest(texto_base=texto_base):
                self.assertEqual(self.question(texto_base).enunciado_para_prova, "")

    def test_narrative_mentions_and_clinical_instructions_keep_prompt(self):
        for texto_base in (
            None,
            "",
            "Contexto clínico sem comando.",
            "O médico avalia as afirmações do paciente.",
            "O preceptor pediu que o aluno analise as afirmativas do prontuário.",
            "Avalie o paciente e solicite exames.",
            "A palavra analise aparece em um relato sobre afirmações.",
        ):
            with self.subTest(texto_base=texto_base):
                self.assertEqual(self.question(texto_base).enunciado_para_prova, MULTIPLE_STATEMENT_PROMPT)

    def test_custom_prompt_is_preserved_unless_repeated_in_full(self):
        personalizado = "Identifique as afirmativas incorretas sobre o tratamento."
        self.assertEqual(
            self.question("Contexto. Avalie as afirmativas a seguir.", personalizado).enunciado_para_prova,
            personalizado,
        )
        self.assertEqual(self.question(f"Contexto. {personalizado}", personalizado).enunciado_para_prova, "")

    def test_legacy_empty_prompt_uses_same_duplication_check_and_fallback(self):
        self.assertEqual(self.question("Contexto. Analise as afirmativas:", "").enunciado_para_prova, "")
        self.assertEqual(self.question("Contexto.", "").enunciado_para_prova, MULTIPLE_STATEMENT_PROMPT)

    def test_other_question_types_keep_their_prompt(self):
        for tipo in ("RESPOSTA_UNICA", "ASSERCAO_RAZAO"):
            with self.subTest(tipo=tipo):
                self.assertEqual(
                    self.question(MULTIPLE_STATEMENT_PROMPT, tipo=tipo).enunciado_para_prova,
                    MULTIPLE_STATEMENT_PROMPT,
                )


def criar_questao():
    semestre = Semestre.objects.create(nome="2026.2")
    periodo = Periodo.objects.create(nome="4º Período", semestre=semestre)
    componente = ComponenteCurricular.objects.create(nome="Clínica", periodo=periodo, numero_questoes_prova=1)
    questao = Questao.objects.create(
        componente=componente,
        professor_nome="Docente",
        professor_email="docente@example.com",
        texto_base="Contexto sintético do caso. Analise as afirmativas a seguir:",
        enunciado=MULTIPLE_STATEMENT_PROMPT,
        tipo_questao="MULTIPLA_ESCOLHA",
        justificativa="Justificativa sintética.",
        status="APROVADA",
    )
    for indice in range(3):
        Alternativa.objects.create(
            questao=questao, texto=f"Afirmativa sintética {indice + 1}.", eh_correta=indice < 2
        )
    return questao


class ExamPromptIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.gestor = get_user_model().objects.create_user("gestor-comando", is_staff=True)
        cls.questao = criar_questao()

    def setUp(self):
        self.client.force_login(self.gestor)

    def gerar(self, **parametros):
        return self.client.get(
            reverse("prova_gerada"), {"componentes": [self.questao.componente_id], **parametros}
        )

    def test_print_and_txt_keep_one_command_without_changing_saved_content_or_answers(self):
        antes = Questao.objects.values().get(pk=self.questao.pk)
        alternativas_antes = list(self.questao.alternativas.values())
        for tipo_prova in ("integrada", "internato", "reposicao", "simulado", "residencia"):
            destino = {"reposicao": "REPOSICAO", "simulado": "SIMULADO", "residencia": "RESIDENCIA"}.get(
                tipo_prova, "INTEGRADA"
            )
            Questao.objects.filter(pk=self.questao.pk).update(uso_prova=destino)
            for formato_txt in ("0", "1"):
                with self.subTest(tipo_prova=tipo_prova, formato_txt=formato_txt):
                    previa = self.gerar(preflight="1", formato_txt=formato_txt, tipo_prova=tipo_prova)
                    self.assertEqual(previa.status_code, 200)
                    prova = self.client.get(previa.json()["url_abertura"])
                    self.assertContains(prova, "Analise as afirmativas a seguir:", count=1)
                    self.assertNotContains(prova, MULTIPLE_STATEMENT_PROMPT)
                    if formato_txt == "0":
                        self.assertContains(prova, "Afirmativa sintética 1.")
                        self.assertContains(prova, 'class="enunciado"', count=0)
                        codigo = prova.context["codigo_prova"]
                        self.assertEqual(codigo, previa.json()["codigo_prova"])
                        gabarito = self.client.get(reverse("prova_gabarito"), {"ids": self.questao.pk})
                        self.assertEqual(gabarito.context["codigo_prova"], codigo)
                    else:
                        self.assertNotContains(prova, "Comando:")
                        self.assertEqual(prova.content.decode().count("\tcorrect"), 1)
                        self.assertEqual(prova.content.decode().count("\tincorrect"), 3)
        Questao.objects.filter(pk=self.questao.pk).update(uso_prova=antes["uso_prova"])
        self.assertEqual(Questao.objects.values().get(pk=self.questao.pk), antes)
        self.assertEqual(list(self.questao.alternativas.values()), alternativas_antes)

    def test_command_is_shown_when_base_has_no_instruction(self):
        Questao.objects.filter(pk=self.questao.pk).update(texto_base="Contexto sintético sem comando.")
        for formato_txt in ("0", "1"):
            with self.subTest(formato_txt=formato_txt):
                resposta = self.gerar(formato_txt=formato_txt)
                self.assertContains(resposta, MULTIPLE_STATEMENT_PROMPT, count=1)

    def test_custom_instruction_survives_in_both_formats(self):
        personalizado = "Avalie apenas as afirmativas sobre prevenção secundária."
        Questao.objects.filter(pk=self.questao.pk).update(enunciado=personalizado)
        for formato_txt in ("0", "1"):
            with self.subTest(formato_txt=formato_txt):
                self.assertContains(self.gerar(formato_txt=formato_txt), personalizado, count=1)


@skipUnless(os.environ.get("RUN_BROWSER_TESTS") == "1", "Habilite RUN_BROWSER_TESTS=1 para Chromium")
class ExamPromptBrowserTests(StaticLiveServerTestCase):
    def test_single_command_on_desktop_mobile_and_print(self):
        from playwright.sync_api import sync_playwright

        get_user_model().objects.create_superuser("admin-comando", "admin@example.com", "teste-comando")
        questao = criar_questao()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True, executable_path=os.environ.get("BROWSER_EXECUTABLE") or None
            )
            page = browser.new_page()
            page.goto(f"{self.live_server_url}/admin/")
            page.locator("[name=username]").fill("admin-comando")
            page.locator("[name=password]").fill("teste-comando")
            page.locator("[type=submit]").click()
            page.wait_for_url(f"{self.live_server_url}/admin/")
            params = urlencode({"componentes": questao.componente_id})
            page.goto(f"{self.live_server_url}{reverse('prova_gerada')}?{params}")
            for largura in (1440, 390):
                page.set_viewport_size({"width": largura, "height": 1000})
                for media in ("screen", "print"):
                    page.emulate_media(media=media)
                    item = page.locator(".questao")
                    self.assertEqual(item.inner_text().count("Analise as afirmativas a seguir:"), 1)
                    self.assertNotIn(MULTIPLE_STATEMENT_PROMPT, item.inner_text())
                    self.assertEqual(item.locator(".enunciado").count(), 0)
                    self.assertEqual(item.locator(".afirmativas p").count(), 3)
                    self.assertEqual(item.locator(".alternativas p").count(), 4)
                if os.environ.get("BROWSER_SCREENSHOT"):
                    destino = Path(os.environ["BROWSER_SCREENSHOT"]).parent
                    destino.mkdir(parents=True, exist_ok=True)
                    item.screenshot(path=str(destino / f"comando-unico-{largura}.png"))
                    if largura == 1440:
                        page.pdf(path=str(destino / "comando-unico.pdf"), print_background=True)
            browser.close()
