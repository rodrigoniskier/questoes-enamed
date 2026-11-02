"""Regressão visual da capa em Chrome: A4, marca real e dados variáveis."""

import os
from pathlib import Path
from unittest import skipUnless
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse

from .models import Alternativa, ComponenteCurricular, Periodo, Questao, Semestre


@skipUnless(os.environ.get("RUN_BROWSER_TESTS") == "1", "Habilite RUN_BROWSER_TESTS=1 para Chromium")
class CapaProvaBrowserTests(StaticLiveServerTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.administrador = get_user_model().objects.create_superuser(
            "admin-capa", "admin-capa@example.com", "teste-capa"
        )
        semestre = Semestre.objects.create(nome="2026.2")
        periodo = Periodo.objects.create(nome="4º Período", semestre=semestre)
        cls.componentes = []
        for indice in range(24):
            componente = ComponenteCurricular.objects.create(
                nome=f"Componente Curricular {indice + 1:02d} de Medicina",
                periodo=periodo,
                numero_questoes_prova=1,
            )
            cls.componentes.append(componente)
            questao = Questao.objects.create(
                componente=componente,
                professor_nome="Docente",
                professor_email="docente@example.com",
                enunciado="Selecione a alternativa correta.",
                justificativa="Justificativa.",
                tipo_questao="RESPOSTA_UNICA",
                status="APROVADA",
            )
            for posicao, tamanho in enumerate((5, 10, 15, 20)):
                Alternativa.objects.create(
                    questao=questao,
                    texto="a" * tamanho,
                    eh_correta=posicao == indice % 4,
                )

    def test_capa_impressa_cabe_em_uma_pagina_e_logo_carrega(self):
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                executable_path=os.environ.get("BROWSER_EXECUTABLE") or None,
            )
            page = browser.new_page(viewport={"width": 1440, "height": 1100})
            page.goto(f"{self.live_server_url}/admin/")
            page.locator("[name=username]").fill("admin-capa")
            page.locator("[name=password]").fill("teste-capa")
            page.locator("[type=submit]").click()
            page.wait_for_url(f"{self.live_server_url}/admin/")

            for total in (1, 12, 24):
                params = [("componentes", str(c.pk)) for c in self.componentes[:total]]
                params.extend((f"quantidade_{c.pk}", "1") for c in self.componentes[:total])
                url = f"{self.live_server_url}{reverse('prova_gerada')}?{urlencode(params)}"
                page.goto(url)
                page.locator(".capa-logo-oficial").wait_for()
                self.assertEqual(page.locator("svg.capa-arte-vetorial").count(), 1)
                self.assertTrue(page.locator("svg.capa-arte-vetorial").is_visible())
                page.wait_for_function(
                    "() => { const img = document.querySelector('.capa-logo-oficial'); "
                    "return img.complete && img.naturalWidth > 0; }"
                )
                self.assertEqual(page.locator(".capa-componente").count(), total)
                self.assertIn("4º PERÍODO", page.locator(".capa-periodo-valor").inner_text())
                self.assertIn("PROVA INTEGRADA", page.locator(".capa-titulo-bloco").inner_text())
                if total > 20:
                    self.assertIn("capa-muito-densa", page.locator(".capa-container").get_attribute("class"))
                elif total > 8:
                    self.assertIn("capa-densa", page.locator(".capa-container").get_attribute("class"))

                page.emulate_media(media="print")
                geometria = page.locator(".capa-container").evaluate(
                    """root => {
                        const pagina = root.getBoundingClientRect();
                        const rodape = root.querySelector('.capa-rodape').getBoundingClientRect();
                        const identificacao = root.querySelector('.capa-identificacao').getBoundingClientRect();
                        const logo = root.querySelector('.capa-logo-oficial').getBoundingClientRect();
                        return {
                            largura: pagina.width, altura: pagina.height,
                            interno: rodape.bottom <= pagina.bottom + 2 &&
                                     identificacao.bottom <= rodape.top &&
                                     logo.left >= pagina.left &&
                                     logo.right <= pagina.right
                        };
                    }"""
                )
                self.assertGreater(geometria["largura"], 780)
                self.assertGreater(geometria["altura"], 1100)
                self.assertTrue(geometria["interno"], f"A capa excedeu a página: {geometria}")
                self.assertEqual(
                    page.locator(".capa-identificacao h3").evaluate("el => getComputedStyle(el).color"),
                    "rgb(17, 17, 17)",
                )
                if total == 1:
                    # Regressão da falha real: salvar PDF sem imprimir fundos
                    # deve incluir os vetores decorativos da capa.
                    com_arte = page.pdf(
                        print_background=False, prefer_css_page_size=True, page_ranges="1"
                    )
                    page.locator("svg.capa-arte-vetorial").evaluate(
                        "elemento => elemento.style.visibility = 'hidden'"
                    )
                    sem_arte = page.pdf(
                        print_background=False, prefer_css_page_size=True, page_ranges="1"
                    )
                    self.assertGreater(
                        len(com_arte) - len(sem_arte),
                        120,
                        "Arte vetorial ausente do PDF quando fundos não são impressos.",
                    )
                    page.locator("svg.capa-arte-vetorial").evaluate(
                        "elemento => elemento.style.removeProperty('visibility')"
                    )

                if os.environ.get("BROWSER_SCREENSHOT"):
                    captura = Path(os.environ["BROWSER_SCREENSHOT"])
                    captura.parent.mkdir(parents=True, exist_ok=True)
                    page.locator(".capa-container").screenshot(
                        path=str(captura.with_name(f"capa-impressao-{total}-componentes.png"))
                    )
                page.emulate_media(media="screen")
                if total == 12 and os.environ.get("BROWSER_SCREENSHOT"):
                    page.locator(".capa-container").screenshot(
                        path=str(captura.with_name("capa-colorida-12-componentes.png"))
                    )
            browser.close()
