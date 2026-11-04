"""Edição e reenvio pela navegação real, com dados exclusivamente sintéticos."""

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.db import close_old_connections
from django.urls import reverse

from pre_auditoria.models import DecisaoPreAuditoria
from pre_auditoria.services import executar_pre_auditoria

from .models import Alternativa, ComponenteCurricular, Periodo, Questao, Semestre


@skipUnless(os.environ.get("RUN_BROWSER_TESTS") == "1", "Habilite RUN_BROWSER_TESTS=1 para Chromium")
class RevisaoEnviadasBrowserTests(StaticLiveServerTestCase):
    def test_select_component_edit_resubmit_and_change_style_on_desktop_and_mobile(self):
        from playwright.sync_api import sync_playwright

        semester = Semestre.objects.create(nome="2026.2")
        period = Periodo.objects.create(nome="2º Período", semestre=semester)
        component = ComponenteCurricular.objects.create(nome="Clínica sintética", periodo=period)
        user = get_user_model().objects.create_superuser("editor-browser", "editor@example.com", "teste")
        question = Questao.objects.create(
            componente=component,
            professor_nome="Docente sintético",
            professor_email="docente@example.com",
            texto_base="Contexto sintético para revisão.",
            enunciado="Enunciado a reformular.",
            justificativa="A opção A responde ao comando; as demais são distratores.",
            status="REPROVADA",
        )
        for index in range(4):
            Alternativa.objects.create(
                questao=question, texto=f"Alternativa {index + 1}", eh_correta=index == 0
            )
        original_date = question.enviada_em
        report = executar_pre_auditoria(question)
        decision = DecisaoPreAuditoria.objects.create(
            questao=question,
            parecer=report,
            acao="REPROVADA",
            fingerprint=report.fingerprint,
            justificativa="Reformule o comando e confira a justificativa da resposta.",
        )
        self.client.force_login(user)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True, executable_path=os.environ.get("BROWSER_EXECUTABLE") or None
            )
            page = browser.new_page(viewport={"width": 1440, "height": 1100})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.context.add_cookies(
                [
                    {
                        "name": "sessionid",
                        "value": self.client.cookies["sessionid"].value,
                        "url": self.live_server_url,
                    }
                ]
            )
            page.goto(self.live_server_url + reverse("submeter_selecao"))
            page.get_by_label("Período", exact=True).select_option(str(period.pk))
            page.get_by_label("Componente curricular", exact=True).select_option(str(component.pk))
            page.get_by_label("Resposta Única", exact=True).check()
            page.get_by_role("button", name="Avançar para o Formulário").click()
            self.assertTrue(page.get_by_role("dialog").is_visible())
            self.check_layout(page, "popup")
            page.get_by_role("link", name="Reenvio de questão ajustada", exact=True).click()
            bank_url = (
                self.live_server_url + reverse("submeter_visualizar_questoes") + f"?componente={component.pk}"
            )
            page.wait_for_url(bank_url)
            self.assertIn("Reformule o comando", page.locator("body").inner_text())
            self.check_layout(page, "banco")
            page.get_by_role("link", name="EDITAR E REENVIAR", exact=True).click()
            self.assertEqual(page.get_by_label("Enunciado / Proposição I").input_value(), question.enunciado)
            self.check_layout(page, "editor")
            page.get_by_label("Enunciado / Proposição I").fill("Comando reformulado no navegador.")
            page.get_by_label("Justificativa").fill("Justificativa corrigida com orientações completas.")
            page.get_by_label("Confirmo o reenvio", exact=False).check()
            page.get_by_role("button", name="SALVAR E REENVIAR PARA ANÁLISE", exact=True).click()
            page.wait_for_url(bank_url)
            self.assertIn("Comando reformulado no navegador.", page.locator("body").inner_text())
            self.assertIn("Questão salva e reenviada", page.locator("body").inner_text())
            self.check_layout(page, "reenviada")

            page.get_by_role("link", name="EDITAR E REENVIAR", exact=True).click()
            page.get_by_label("Tipo da questão").select_option("ASSERCAO_RAZAO")
            page.get_by_label("Proposição II").fill("Segunda proposição sintética.")
            page.get_by_text("Usar as cinco alternativas padrão de asserção-razão", exact=True).click()
            page.locator("#assertion-key").select_option("1")
            page.once("dialog", lambda dialog: dialog.accept())
            page.get_by_role("button", name="Substituir pelas cinco alternativas padrão", exact=True).click()
            self.assertTrue(page.locator("#id_alternativas-1-eh_correta").is_checked())
            self.assertFalse(page.locator("#id_alternativas-0-eh_correta").is_checked())
            page.get_by_label("Confirmo o reenvio", exact=False).check()
            page.get_by_role("button", name="SALVAR E REENVIAR PARA ANÁLISE", exact=True).click()
            page.wait_for_url(bank_url)
            page.goto(self.live_server_url + reverse("admin:questoes_questao_change", args=[question.pk]))
            page.get_by_role("tab", name="Histórico de Alterações", exact=True).click()
            history = page.locator(".question-history")
            history.wait_for(state="visible")
            self.assertTrue(history.is_visible())
            self.assertIn("Reformule o comando", history.inner_text())
            self.assertIn("Segunda proposição sintética.", history.inner_text())
            page.get_by_text("Comparar com a versão original registrada", exact=True).click()
            self.assertIn("Enunciado a reformular.", history.inner_text())
            self.check_layout(page, "historico-admin")
            self.assertEqual(errors, [])
            browser.close()
        question.refresh_from_db()
        self.assertEqual(question.status, "PENDENTE")
        self.assertEqual(question.tipo_questao, "ASSERCAO_RAZAO")
        self.assertEqual(question.enviada_em, original_date)
        self.assertEqual(question.alternativas.count(), 5)
        self.assertEqual(question.pareceres_pre_auditoria.first().resultado, "APROVADA")
        self.assertTrue(DecisaoPreAuditoria.objects.filter(pk=decision.pk).exists())
        self.assertEqual(Questao.objects.count(), 1)

    def test_popup_new_question_routes_cancel_keyboard_and_public_component_scope(self):
        from playwright.sync_api import sync_playwright

        semester = Semestre.objects.create(nome="2026.2")
        period = Periodo.objects.create(nome="2º Período", semestre=semester)
        component = ComponenteCurricular.objects.create(nome="Componente escolhido", periodo=period)
        other = ComponenteCurricular.objects.create(nome="Outro componente", periodo=period)
        Questao.objects.create(componente=component, enunciado="Questão deste componente", justificativa="J")
        Questao.objects.create(componente=other, enunciado="Questão de outro componente", justificativa="J")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True, executable_path=os.environ.get("BROWSER_EXECUTABLE") or None
            )
            page = browser.new_page()
            for width in (1440, 390):
                page.set_viewport_size({"width": width, "height": 1000})
                for label, route in (
                    ("Resposta Única", "submeter_resposta_unica"),
                    ("Resposta Múltipla", "submeter_resposta_multipla"),
                    ("Asserção-Razão", "submeter_assercao_razao"),
                ):
                    page.goto(self.live_server_url + reverse("submeter_selecao"))
                    page.get_by_label("Período", exact=True).select_option(str(period.pk))
                    page.get_by_label("Componente curricular", exact=True).select_option(str(component.pk))
                    page.get_by_label(label, exact=True).check()
                    advance = page.get_by_role("button", name="Avançar para o Formulário")
                    advance.click()
                    self.assertTrue(page.get_by_role("dialog").is_visible())
                    page.keyboard.press("Escape")
                    self.assertFalse(page.get_by_role("dialog").is_visible())
                    self.assertTrue(advance.evaluate("el => el === document.activeElement"))
                    self.assertEqual(
                        page.get_by_label("Componente curricular").input_value(), str(component.pk)
                    )
                    self.assertTrue(page.get_by_label(label, exact=True).is_checked())
                    advance.click()
                    page.get_by_role("button", name="Voltar à seleção", exact=True).click()
                    self.assertFalse(page.get_by_role("dialog").is_visible())
                    advance.click()
                    self.assertEqual(page.get_by_role("dialog").get_by_role("link").count(), 2)
                    page.get_by_role("link", name="Nova questão", exact=True).click()
                    page.wait_for_url(self.live_server_url + reverse(route) + f"?componente={component.pk}")
                    self.assertEqual(page.locator("#id_texto_base").input_value(), "")
            page.goto(
                self.live_server_url + reverse("submeter_visualizar_questoes") + f"?componente={component.pk}"
            )
            self.assertIn("Questão deste componente", page.locator("body").inner_text())
            self.assertNotIn("Questão de outro componente", page.locator("body").inner_text())
            self.assertEqual(page.get_by_role("link", name="EDITAR E REENVIAR", exact=True).count(), 0)
            browser.close()
        self.assertEqual(Questao.objects.count(), 2)

    def check_layout(self, page, stage):
        for width, height in ((1440, 1100), (390, 844)):
            page.set_viewport_size({"width": width, "height": height})
            self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width)
            if os.environ.get("BROWSER_SCREENSHOT"):
                screenshot = Path(os.environ["BROWSER_SCREENSHOT"])
                screenshot.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(
                    path=str(screenshot.with_name(f"reenvio-{stage}-{width}.png")), full_page=True
                )
                if stage == "historico-admin":
                    page.screenshot(path=str(screenshot.with_name(f"historico-viewport-{width}.png")))

    def test_custom_multiple_prompt_manual_combinations_and_exam_cover(self):
        from playwright.sync_api import sync_playwright

        def db_call(operation):
            def run():
                close_old_connections()
                try:
                    return operation()
                finally:
                    close_old_connections()

            with ThreadPoolExecutor(max_workers=1) as executor:
                return executor.submit(run).result()

        semester = Semestre.objects.create(nome="2026.2")
        period = Periodo.objects.create(nome="4º Período", semestre=semester)
        component = ComponenteCurricular.objects.create(
            nome="Clínica múltipla", periodo=period, numero_questoes_prova=1
        )
        user = get_user_model().objects.create_superuser("comb-browser", "comb@example.com", "teste")
        self.client.force_login(user)
        custom = "Analise o caso clínico e selecione as afirmativas verdadeiras."
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True, executable_path=os.environ.get("BROWSER_EXECUTABLE") or None
            )
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.context.add_cookies(
                [
                    {
                        "name": "sessionid",
                        "value": self.client.cookies["sessionid"].value,
                        "url": self.live_server_url,
                    }
                ]
            )
            page.goto(
                self.live_server_url + reverse("submeter_resposta_multipla") + f"?componente={component.pk}"
            )
            prompt = page.get_by_label("Enunciado", exact=True)
            self.assertEqual(
                prompt.input_value(), "Considerando o texto apresentado, avalie as afirmações a seguir."
            )
            prompt.fill(custom)
            page.locator('[name="professor_nome"]').fill("Docente sintético")
            page.locator('[name="professor_email"]').fill("docente@example.com")
            page.get_by_label("Texto-base", exact=True).fill("Contexto clínico sintético.")
            page.get_by_label("Justificativa", exact=True).fill(
                "I e II são verdadeiras; III é falsa, conforme o caso."
            )
            for i in range(3):
                page.locator(f"#id_alternativas-{i}-texto").fill(f"Afirmativa {i + 1}")
                if i < 2:
                    page.locator(f"#id_alternativas-{i}-eh_correta").check()
            for checkbox in page.locator(".checklist-item").all():
                checkbox.check()
            self.check_layout(page, "formulario-multipla")
            page.get_by_role("button", name="Enviar e concluir", exact=True).click()
            page.wait_for_url(self.live_server_url + reverse("submeter_selecao"))
            question = db_call(lambda: Questao.objects.get(componente=component))
            self.assertEqual(question.enunciado, custom)
            page.goto(self.live_server_url + reverse("admin:questoes_questao_change", args=[question.pk]))
            page.get_by_role("tab", name="Alternativas", exact=True).click()
            page.locator("#id_alternativas-0-texto").fill("Afirmativa I revista pela gestão")
            page.get_by_role("tab", name="Combinações A–D", exact=True).click()
            page.get_by_role("button", name="Carregar combinações atuais para editar").click()
            field = page.get_by_label("Combinações finais A–D", exact=True)
            self.assertTrue(field.input_value())
            field.fill("A) I, II\nB) I, III\nC) II\nD) III")
            self.check_layout(page, "combinacoes-admin")
            page.locator('[name="_continue"]').click()
            page.wait_for_load_state()
            db_call(question.refresh_from_db)
            self.assertEqual(question.combinacoes_personalizadas, [[1, 2], [1, 3], [2], [3]])
            db_call(lambda: Questao.objects.filter(pk=question.pk).update(status="APROVADA"))
            page.goto(self.live_server_url + reverse("prova_gerada") + f"?componentes={component.pk}")
            self.assertEqual(page.locator(".capa-semestre").inner_text(), "Semestre 2026.2")
            self.assertEqual(page.locator(".enunciado").inner_text(), custom)
            self.assertIn("Afirmativa I revista pela gestão", page.locator("body").inner_text())
            browser.close()
