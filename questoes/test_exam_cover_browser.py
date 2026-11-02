"""Regressão da restauração da capa original, anterior a 02/11/2026."""

import os
import re
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
        get_user_model().objects.create_superuser("admin-capa", "admin-capa@example.com", "teste-capa")
        semestre = Semestre.objects.create(nome="2026.2")
        periodo = Periodo.objects.create(nome="4º Período", semestre=semestre)
        cls.componentes = []
        for indice in range(8):
            componente = ComponenteCurricular.objects.create(
                nome=f"Componente Curricular {indice + 1:02d}", periodo=periodo, numero_questoes_prova=1
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
                    questao=questao, texto="a" * tamanho, eh_correta=posicao == indice % 4
                )

    def test_capa_original_carrega_fundo_logo_e_componentes_e_exporta_pdf(self):
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True, executable_path=os.environ.get("BROWSER_EXECUTABLE") or None
            )
            page = browser.new_page(viewport={"width": 1440, "height": 1200})
            page.goto(f"{self.live_server_url}/admin/")
            page.locator("[name=username]").fill("admin-capa")
            page.locator("[name=password]").fill("teste-capa")
            page.locator("[type=submit]").click()
            page.wait_for_url(f"{self.live_server_url}/admin/")

            for total in (1, 8):
                selecionados = self.componentes[:total]
                params = [("componentes", str(c.pk)) for c in selecionados]
                params.extend((f"quantidade_{c.pk}", "1") for c in selecionados)
                page.emulate_media(media="screen")
                page.goto(f"{self.live_server_url}{reverse('prova_gerada')}?{urlencode(params)}")
                page.wait_for_function(
                    "() => { const img = document.querySelector('.capa-footer img'); "
                    "return img && img.complete && img.naturalWidth > 0; }"
                )
                capa = page.locator(".capa-container")
                self.assertTrue(capa.locator(".capa-superior").is_visible())
                self.assertTrue(capa.locator(".capa-inferior").is_visible())
                self.assertEqual(capa.locator(".check-circle").count(), 1)
                self.assertIn(
                    "capa_background.jpg",
                    capa.locator(".capa-superior").evaluate("el => getComputedStyle(el).backgroundImage"),
                )
                self.assertTrue(
                    page.evaluate(
                        """async () => {
                            const imagem = new Image();
                            imagem.src = new URL('capa_background.jpg',
                                document.querySelector('.capa-footer img').src).href;
                            await imagem.decode();
                            return imagem.naturalWidth > 0;
                        }"""
                    )
                )
                self.assertIn("4º PERÍODO", capa.locator(".capa-periodo").inner_text())
                self.assertEqual(
                    capa.locator(".capa-disciplinas p").all_text_contents()[1:],
                    [f"{c.nome} ( \xa0 )" for c in selecionados],
                )
                destino = None
                if os.environ.get("BROWSER_SCREENSHOT"):
                    destino = Path(os.environ["BROWSER_SCREENSHOT"]).parent
                    destino.mkdir(parents=True, exist_ok=True)
                    capa.screenshot(path=str(destino / f"capa-original-{total}.png"))
                page.emulate_media(media="print")
                if total == 1:
                    prova = page.pdf(print_background=True, prefer_css_page_size=True)
                    self.assertEqual(len(re.findall(rb"/Type /Page\b", prova)), 3)
                    if destino:
                        (destino / "prova-capa-original.pdf").write_bytes(prova)
                page.add_style_tag(
                    content="body > .pagina:not(.capa-container) { display: none !important; }"
                )
                pdf = page.pdf(print_background=True, prefer_css_page_size=True)
                self.assertEqual(len(re.findall(rb"/Type /Page\b", pdf)), 1)
                if destino:
                    (destino / f"capa-original-{total}.pdf").write_bytes(pdf)
            browser.close()
