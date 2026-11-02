"""Capa editorial no Chromium: composição, paginação A4 e PDF sem fundos."""

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
        cls.administrador = get_user_model().objects.create_superuser(
            "admin-capa", "admin-capa@example.com", "teste-capa"
        )
        semestre = Semestre.objects.create(nome="2026.2")
        cls.periodo = Periodo.objects.create(nome="4º Período", semestre=semestre)
        cls.periodo_longo = Periodo.objects.create(
            nome="Estágio Curricular Obrigatório — Avaliação Integrada de Competências Clínicas e Atenção à Saúde",
            semestre=semestre,
        )
        cls.componentes = []
        for indice in range(80):
            nome = f"Componente Curricular {indice + 1:02d} de Medicina"
            if indice >= 24:
                nome += " — Atenção Integral à Saúde da Família e da Comunidade"
            componente = ComponenteCurricular.objects.create(
                nome=nome, periodo=cls.periodo if indice < 24 else cls.periodo_longo, numero_questoes_prova=1
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

    def abrir_prova(self, page, total, inicio=0):
        self.selecionados = self.componentes[inicio : inicio + total]
        params = [("componentes", str(c.pk)) for c in self.selecionados]
        params.extend((f"quantidade_{c.pk}", "1") for c in self.selecionados)
        page.goto(f"{self.live_server_url}{reverse('prova_gerada')}?{urlencode(params)}")
        page.wait_for_function(
            "() => { const img = document.querySelector('.capa-logo-oficial'); "
            "return img && img.complete && img.naturalWidth > 0; }"
        )

    def conferir_composicao(self, page, total):
        capa = page.locator(".capa-container")
        self.assertEqual(capa.locator("img").count(), 1)
        self.assertEqual(capa.locator("svg, canvas, iframe").count(), 0)
        self.assertEqual(capa.locator(".capa-componente").count(), total)
        self.assertEqual(
            [texto.strip() for texto in capa.locator(".capa-componente").all_text_contents()],
            [c.nome for c in self.selecionados],
        )
        geometria = capa.evaluate(
            """root => {
                const box = el => el.getBoundingClientRect();
                const pagina = box(root);
                const boa = box(root.querySelector('.capa-boa-prova'));
                const identificacao = box(root.querySelector('.capa-identificacao'));
                const disciplinas = box(root.querySelector('.capa-disciplinas'));
                const rodape = box(root.querySelector('.capa-rodape'));
                const componentes = [...root.querySelectorAll('.capa-componente')];
                const elementos = [root, ...root.querySelectorAll('*')];
                return {
                    altura: pagina.height,
                    intervalo: identificacao.top - boa.bottom,
                    ordem: identificacao.bottom <= disciplinas.top && disciplinas.bottom <= rodape.top,
                    dentro: componentes.every(el => box(el).left >= pagina.left &&
                                                   box(el).right <= pagina.right &&
                                                   box(el).bottom <= rodape.top),
                    legivel: componentes.every(el => parseFloat(getComputedStyle(el).fontSize) >= 13),
                    semArte: elementos.every(el => getComputedStyle(el).backgroundImage === 'none' &&
                        ['::before', '::after'].every(p => ['none', 'normal'].includes(
                            getComputedStyle(el, p).content)))
                };
            }"""
        )
        self.assertTrue(geometria["ordem"] and geometria["dentro"], geometria)
        self.assertTrue(geometria["legivel"] and geometria["semArte"], geometria)
        self.assertGreaterEqual(geometria["intervalo"], 15)
        self.assertLessEqual(geometria["intervalo"], 21)
        return geometria

    def salvar_pdf_capa(self, page, nome, paginas_esperadas=1):
        # Oculta apenas o miolo/cartão para contar as páginas reais da capa.
        page.add_style_tag(content="body > .pagina:not(.capa-container) { display: none !important; }")
        pdf = page.pdf(print_background=False, prefer_css_page_size=True)
        self.assertEqual(len(re.findall(rb"/Type /Page\b", pdf)), paginas_esperadas)
        self.assertTrue(pdf.startswith(b"%PDF-"))
        if os.environ.get("BROWSER_SCREENSHOT"):
            destino = Path(os.environ["BROWSER_SCREENSHOT"]).parent / f"{nome}.pdf"
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_bytes(pdf)

    def captura(self, page, nome):
        if os.environ.get("BROWSER_SCREENSHOT"):
            destino = Path(os.environ["BROWSER_SCREENSHOT"]).parent / f"{nome}.png"
            destino.parent.mkdir(parents=True, exist_ok=True)
            page.locator(".capa-container").screenshot(path=str(destino))

    def test_capa_editorial_a4_sem_fundos_e_continuacao_legivel(self):
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

            for total in (1, 8, 12, 24):
                with self.subTest(componentes=total):
                    page.emulate_media(media="screen")
                    self.abrir_prova(page, total)
                    geometria = self.conferir_composicao(page, total)
                    self.assertAlmostEqual(geometria["altura"], 297 * 96 / 25.4, delta=2)
                    self.assertIn("4º PERÍODO", page.locator(".capa-periodo-valor").inner_text())
                    self.captura(page, f"capa-colorida-{total}")
                    page.emulate_media(media="print")
                    if total == 1:
                        prova_completa = page.pdf(
                            path=str(Path(os.environ["BROWSER_SCREENSHOT"]).parent / "prova-completa.pdf")
                            if os.environ.get("BROWSER_SCREENSHOT")
                            else None,
                            print_background=False,
                            prefer_css_page_size=True,
                        )
                        self.assertEqual(len(re.findall(rb"/Type /Page\b", prova_completa)), 3)
                    self.conferir_composicao(page, total)
                    self.salvar_pdf_capa(page, f"capa-sem-fundos-{total}")
                    page.add_style_tag(
                        content=(
                            ".capa-container { --azul-unipe: #111; --dourado-unipe: #555; color: #111; }"
                            ".capa-logo-oficial { filter: grayscale(1); }"
                            ".capa-container * { border-color: #666; }"
                        )
                    )
                    self.captura(page, f"capa-pb-{total}")
                    self.salvar_pdf_capa(page, f"capa-pb-{total}")

            self.abrir_prova(page, 24, inicio=24)
            self.assertEqual(
                page.locator(".capa-periodo-valor").inner_text(), self.periodo_longo.nome.upper()
            )
            self.conferir_composicao(page, 24)
            self.captura(page, "capa-titulo-e-componentes-longos")
            self.salvar_pdf_capa(page, "capa-titulo-e-componentes-longos", paginas_esperadas=2)

            # Excepcional: continuação automática sem reduzir ou truncar texto.
            self.abrir_prova(page, 80)
            self.conferir_composicao(page, 80)
            self.salvar_pdf_capa(page, "capa-continuacao-80", paginas_esperadas=3)
            browser.close()
