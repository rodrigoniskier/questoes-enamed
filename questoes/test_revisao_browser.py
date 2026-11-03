"""Edição e reenvio pela navegação real, com dados exclusivamente sintéticos."""

import os
from pathlib import Path
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
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
            page.get_by_role("link", name="Visualizar e Gerir Questões Enviadas").click()
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
