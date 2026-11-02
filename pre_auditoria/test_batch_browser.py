"""Seleção, exportação e aprovação em navegador com dados exclusivamente sintéticos."""

import os
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse
from PIL import Image

from questoes.models import Alternativa, ComponenteCurricular, Periodo, Questao, Semestre
from questoes.submission import ASSERTION_ANSWERS

from .models import DecisaoPreAuditoria
from .services import executar_pre_auditoria


@skipUnless(os.environ.get("RUN_BROWSER_TESTS") == "1", "Habilite RUN_BROWSER_TESTS=1 para Chromium")
class BrowserBatchPreAuditoriaTests(StaticLiveServerTestCase):
    def setUp(self):
        super().setUp()
        media = TemporaryDirectory()
        self.addCleanup(media.cleanup)
        settings = override_settings(MEDIA_ROOT=media.name)
        settings.enable()
        self.addCleanup(settings.disable)
        semester = Semestre.objects.create(nome="2026.2")
        period = Periodo.objects.create(nome="2º Período", semestre=semester)
        component = ComponenteCurricular.objects.create(nome="Revisão sintética em lote", periodo=period)
        self.reviewer = get_user_model().objects.create_superuser(
            "gestor-lote", "gestor@example.com", "teste"
        )
        self.viewer = get_user_model().objects.create_user("visualizador-lote")
        self.viewer.user_permissions.add(Permission.objects.get(codename="view_parecerpreauditoria"))
        self.questions = []
        for number in range(1, 28):
            kind = ("RESPOSTA_UNICA", "MULTIPLA_ESCOLHA", "ASSERCAO_RAZAO")[(number - 1) % 3]
            question = Questao.objects.create(
                componente=component,
                professor_nome="Docente fictício",
                professor_email="docente@example.com",
                tipo_questao=kind,
                texto_base=f"Contexto exclusivo da questão sintética {number:02d}.",
                enunciado=f"Comando exclusivo da questão sintética {number:02d}?",
                proposicao_dois="Segunda asserção sintética.",
                justificativa=f"Justificativa exclusiva {number:02d}: explicação da resposta e dos distratores.",
            )
            answers = (
                [(f"{letter}) {text}", letter == "B") for letter, text in ASSERTION_ANSWERS.items()]
                if kind == "ASSERCAO_RAZAO"
                else [(f"Resposta sintética {number:02d}-{index}", index == 1) for index in range(4)]
            )
            for text, correct in answers:
                Alternativa.objects.create(questao=question, texto=text, eh_correta=correct)
            self.questions.append(question)
        image = BytesIO()
        Image.new("RGB", (320, 180), "#185c7a").save(image, format="PNG")
        self.questions[-1].imagem = SimpleUploadedFile("figura-sintetica.png", image.getvalue(), "image/png")
        self.questions[-1].save()
        for question in self.questions[-3:]:
            executar_pre_auditoria(question, revisor=self.reviewer, semantica=False)
        executar_pre_auditoria(self.questions[-1], revisor=self.reviewer, semantica=False)
        self.sessions = {}
        for user in (self.reviewer, self.viewer):
            client = Client()
            client.force_login(user)
            self.sessions[user.pk] = client.cookies["sessionid"].value

    def authenticated_page(self, browser, user):
        context = browser.new_context(viewport={"width": 1440, "height": 1100})
        context.add_cookies(
            [
                {
                    "name": "sessionid",
                    "value": self.sessions[user.pk],
                    "url": self.live_server_url,
                }
            ]
        )
        return context.new_page()

    def review_layout(self, page, stage):
        for width, height in ((1440, 1100), (390, 844), (320, 740)):
            page.set_viewport_size({"width": width, "height": height})
            self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width)
            if os.environ.get("BATCH_REVIEW_DIR"):
                output = Path(os.environ["BATCH_REVIEW_DIR"])
                output.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(output / f"{stage}-{width}.png"), full_page=False)
                if stage == "painel-selecao":
                    page.locator(".audit-selection-controls").scroll_into_view_if_needed()
                    page.screenshot(path=str(output / f"{stage}-controles-{width}.png"), full_page=False)
                elif stage == "confirmacao-lote":
                    page.locator('[name="justificativa"]').scroll_into_view_if_needed()
                    page.screenshot(path=str(output / f"{stage}-formulario-{width}.png"), full_page=False)
                page.evaluate("window.scrollTo(0, 0)")
        page.set_viewport_size({"width": 1440, "height": 1100})

    def export_popup(self, page):
        with page.expect_popup() as popup:
            page.get_by_role("button", name="EXPORTAR SELECIONADAS EM PDF", exact=True).click()
        export = popup.value
        export.wait_for_load_state("networkidle")
        return export

    def save_pdf(self, page, name):
        if os.environ.get("BATCH_REVIEW_DIR"):
            output = Path(os.environ["BATCH_REVIEW_DIR"])
            output.mkdir(parents=True, exist_ok=True)
            page.pdf(path=str(output / name), format="A4", prefer_css_page_size=True)

    def test_selection_scope_export_all_and_view_only_pdf(self):
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True, executable_path=os.environ.get("BROWSER_EXECUTABLE") or None
            )
            page = self.authenticated_page(browser, self.reviewer)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            url = self.live_server_url + reverse("pre_auditoria:lista")
            page.goto(url)
            selected = page.locator('input[form="audit-batch"][name="questoes"]')
            self.assertEqual(selected.count(), 25)
            page.get_by_role("button", name="EXPORTAR SELECIONADAS EM PDF", exact=True).click()
            self.assertTrue(page.locator("#audit-selection-error").is_visible())
            page.locator("#audit-select-all").check()
            self.assertEqual(page.locator("#audit-selection-count").inner_text(), "25 questões selecionadas")
            self.assertEqual(page.locator('input[form="audit-cleanup"]:checked').count(), 0)
            export = self.export_popup(page)
            self.assertEqual(export.locator(".export-question").count(), 25)
            self.assertIn(
                "Contexto exclusivo da questão sintética 27.",
                export.locator(".export-question").first.inner_text(),
            )
            self.assertIn(
                "Contexto exclusivo da questão sintética 03.",
                export.locator(".export-question").last.inner_text(),
            )
            self.assertNotIn(
                "Contexto exclusivo da questão sintética 02.", export.locator("body").inner_text()
            )
            self.assertTrue(
                export.locator(".question-preview-image img").evaluate(
                    "el => el.complete && el.naturalWidth > 0"
                )
            )
            export.emulate_media(media="print")
            self.assertEqual(export.locator(".audit-answer-review:visible").count(), 25)
            self.assertFalse(export.locator(".export-controls").is_visible())
            self.save_pdf(export, "todas-25.pdf")
            export.close()
            selected.last.uncheck()
            self.assertTrue(page.locator("#audit-select-all").evaluate("el => el.indeterminate"))
            page.locator("#audit-select-all").check()
            self.assertEqual(page.locator("#audit-selection-count").inner_text(), "25 questões selecionadas")
            self.review_layout(page, "painel-selecao")
            page.get_by_role("link", name="Próxima", exact=True).click()
            self.assertEqual(page.locator("#audit-selection-count").inner_text(), "0 questões selecionadas")
            self.assertEqual(page.locator('input[form="audit-batch"][name="questoes"]').count(), 2)
            page.locator("#audit-select-all").check()
            page.locator('[name="status"]').select_option("TODAS")
            page.wait_for_url("**status=TODAS*")
            self.assertEqual(page.locator("#audit-selection-count").inner_text(), "0 questões selecionadas")

            viewer_page = self.authenticated_page(browser, self.viewer)
            viewer_page.goto(url)
            self.assertEqual(
                viewer_page.get_by_role("button", name="APROVAR SELECIONADAS EM LOTE").count(), 0
            )
            for checkbox in viewer_page.locator('input[form="audit-batch"][name="questoes"]').all()[:3]:
                checkbox.check()
            export = self.export_popup(viewer_page)
            export.on("pageerror", lambda error: errors.append(str(error)))
            self.assertEqual(export.locator(".export-question").count(), 3)
            self.assertNotIn(
                "Contexto exclusivo da questão sintética 24.", export.locator("body").inner_text()
            )
            self.assertIn("Resposta Múltipla", export.locator("body").inner_text())
            self.assertIn("Asserção-Razão", export.locator("body").inner_text())
            self.assertIn("Resposta Única", export.locator("body").inner_text())
            self.review_layout(export, "pdf-selecionadas")
            export.evaluate("window.print = () => { window.printRequested = true; }")
            export.get_by_role("button", name="SALVAR COMO PDF", exact=True).click()
            self.assertTrue(export.evaluate("window.printRequested"))
            self.save_pdf(export, "selecionadas-3.pdf")
            self.assertEqual(errors, [])
            browser.close()

    def test_batch_approval_preview_and_explicit_confirmation(self):
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True, executable_path=os.environ.get("BROWSER_EXECUTABLE") or None
            )
            page = self.authenticated_page(browser, self.reviewer)
            page.goto(self.live_server_url + reverse("pre_auditoria:lista"))
            for checkbox in page.locator('input[form="audit-batch"][name="questoes"]').all()[:3]:
                checkbox.check()
            page.get_by_role("button", name="APROVAR SELECIONADAS EM LOTE", exact=True).click()
            self.assertEqual(page.locator(".audit-approval-list li").count(), 3)
            self.review_layout(page, "confirmacao-lote")
            page.locator('[name="justificativa"]').fill("Conferência visual sintética concluída pelo gestor.")
            page.locator('[name="confirmar_aprovacao"]').check()
            page.get_by_role("button", name="CONFIRMAR APROVAÇÃO DE 3 QUESTÕES", exact=True).click()
            page.wait_for_url(self.live_server_url + reverse("pre_auditoria:lista"))
            browser.close()
        self.assertEqual(DecisaoPreAuditoria.objects.count(), 3)
        for question in self.questions[-3:]:
            question.refresh_from_db()
            self.assertEqual(question.status, "APROVADA")
            self.assertEqual(question.decisoes_pre_auditoria.get().revisor, self.reviewer)
