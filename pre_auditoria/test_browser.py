"""Conferência visual com dados sintéticos e impressão sem respostas."""

import os
from pathlib import Path
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse

from questoes.models import Alternativa, ComponenteCurricular, Periodo, Questao, Semestre
from questoes.submission import ASSERTION_ANSWERS


@skipUnless(os.environ.get("RUN_BROWSER_TESTS") == "1", "Habilite RUN_BROWSER_TESTS=1 para Chromium")
class BrowserPreAuditoriaTests(StaticLiveServerTestCase):
    def test_answers_visible_on_desktop_and_mobile_and_hidden_when_printing(self):
        from playwright.sync_api import sync_playwright

        semester = Semestre.objects.create(nome="2026.2")
        period = Periodo.objects.create(nome="2º Período", semestre=semester)
        component = ComponenteCurricular.objects.create(nome="Componente de demonstração", periodo=period)
        reviewer = get_user_model().objects.create_user("auditor-visual")
        reviewer.user_permissions.add(Permission.objects.get(codename="view_parecerpreauditoria"))
        questions = []
        for kind, _label in Questao.TIPO_QUESTAO_CHOICES:
            question = Questao.objects.create(
                componente=component,
                professor_nome="Docente fictício",
                professor_email="docente@example.com",
                tipo_questao=kind,
                texto_base="Cenário fictício para conferência do painel.",
                enunciado="Enunciado de demonstração.",
                proposicao_dois="Segunda proposição de demonstração.",
                justificativa="Justificativa de demonstração.\nExplicação da resposta e dos distratores.",
            )
            if kind == "ASSERCAO_RAZAO":
                answers = [(f"{letter}) {text}", letter == "B") for letter, text in ASSERTION_ANSWERS.items()]
            else:
                answers = [(f"Alternativa de demonstração {i + 1}", i == 1) for i in range(4)]
            for text, correct in answers:
                Alternativa.objects.create(questao=question, texto=text, eh_correta=correct)
            questions.append(question)

        self.client.force_login(reviewer)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True, executable_path=os.environ.get("BROWSER_EXECUTABLE") or None
            )
            page = browser.new_page()
            page.context.add_cookies(
                [
                    {
                        "name": "sessionid",
                        "value": self.client.cookies["sessionid"].value,
                        "url": self.live_server_url,
                    }
                ]
            )
            for route, count in (
                (reverse("pre_auditoria:lista"), 3),
                (reverse("pre_auditoria:detalhe", args=[questions[0].pk]), 1),
            ):
                for width, height in ((1440, 1100), (390, 844)):
                    page.set_viewport_size({"width": width, "height": height})
                    page.goto(self.live_server_url + route)
                    reviews = page.get_by_role(
                        "region", name="Gabarito e justificativas para conferência visual"
                    )
                    self.assertEqual(reviews.count(), count)
                    for review in reviews.all():
                        self.assertTrue(review.is_visible())
                        self.assertIn("Gabarito:", review.inner_text())
                        self.assertIn("Explicação da resposta e dos distratores.", review.inner_text())
                    self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width)
                    self.assertTrue(
                        page.get_by_text("RESULTADO DA PRÉ-AUDITORIA", exact=True).first.is_visible()
                    )
                    if os.environ.get("BROWSER_SCREENSHOT"):
                        screenshot = Path(os.environ["BROWSER_SCREENSHOT"])
                        name = "painel" if count == 3 else "detalhe"
                        page.screenshot(
                            path=str(screenshot.with_name(f"{screenshot.stem}-{name}-{width}.png")),
                            full_page=True,
                        )
                page.emulate_media(media="print")
                self.assertEqual(page.locator(".audit-answer-review:visible").count(), 0)
                if count == 1:
                    self.assertTrue(page.locator(".question-print-preview").is_visible())
                    self.assertEqual(page.locator(".question-print-preview .audit-answer-review").count(), 0)
                page.emulate_media(media="screen")
            browser.close()
