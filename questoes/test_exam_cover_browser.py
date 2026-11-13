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
                self.assertEqual(capa.locator(".capa-semestre").inner_text(), "Semestre 2026.2")
                self.assertGreater(
                    capa.locator(".capa-semestre").bounding_box()["y"],
                    capa.locator(".capa-periodo").bounding_box()["y"],
                )
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
            page.emulate_media(media="screen")
            page.set_viewport_size({"width": 390, "height": 850})
            page.goto(self.live_server_url + reverse("pagina_gerar_prova"))
            page.locator("#periodos").select_option(str(self.componentes[0].periodo_id))
            page.locator("#componentes").select_option(str(self.componentes[0].pk))
            periodo = page.get_by_label("Período da prova", exact=True)
            self.assertEqual(periodo.input_value(), "4º Período")
            periodo.fill("8º Período")
            page.get_by_label("Turma", exact=True).fill("B & C")
            page.get_by_role("button", name="Gerar Prova Integrada").click()
            self.assertIn("8º Período · Turma B & C", page.get_by_role("dialog").inner_text())
            with page.expect_popup() as abertura:
                page.get_by_role("link", name="Confirmar e abrir prova").click()
            prova = abertura.value
            self.assertIn("8º PERÍODO", prova.locator(".capa-periodo").inner_text())
            self.assertIn("TURMA: B & C", prova.locator(".capa-identificacao").inner_text())
            pdf = prova.pdf(print_background=True, prefer_css_page_size=True)
            self.assertEqual(len(re.findall(rb"/Type /Page\b", pdf)), 3)
            with prova.expect_popup() as abertura:
                prova.get_by_role("link", name="Ver Gabarito").click()
            gabarito = abertura.value
            self.assertIn("PERÍODO: 8º Período · TURMA: B & C", gabarito.locator(".cabecalho").inner_text())
            if destino:
                prova.locator(".capa-container").screenshot(path=str(destino / "capa-periodo-turma.png"))
                gabarito.locator(".cabecalho").screenshot(path=str(destino / "gabarito-periodo-turma.png"))

            manual = reverse("montador_manual") + f"?periodo_id={self.componentes[0].periodo_id}"
            page.goto(self.live_server_url + manual)
            self.assertEqual(page.get_by_label("Período da prova", exact=True).input_value(), "4º Período")
            page.get_by_label("Turma", exact=True).fill("D")
            page.locator(".seletor-questao").nth(1).check()
            page.locator(".seletor-questao").nth(0).check()
            with page.expect_popup() as abertura:
                page.get_by_role("button", name="Gerar Gabarito Comentado").click()
            gabarito_manual = abertura.value
            self.assertIn(
                "PERÍODO: 4º Período · TURMA: D", gabarito_manual.locator(".cabecalho").inner_text()
            )
            self.assertEqual(
                [item.split() for item in gabarito_manual.locator(".gabarito-item").all_inner_texts()],
                [["1.", "B"], ["2.", "A"]],
            )
            browser.close()
