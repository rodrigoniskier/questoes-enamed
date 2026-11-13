"""Regressão da prévia do gabarito e do link assinado da mesma prova."""

import csv
import io
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.core import signing
from django.test import TestCase
from django.urls import reverse

from .models import Alternativa, ComponenteCurricular, Periodo, Questao, Semestre


class ExamPreflightTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        semestre = Semestre.objects.create(nome="2026.2")
        periodo = Periodo.objects.create(nome="7º Período", semestre=semestre)
        cls.componente = ComponenteCurricular.objects.create(
            nome="Clínica", periodo=periodo, numero_questoes_prova=8
        )
        cls.gestor = get_user_model().objects.create_user("gestor-previa", is_staff=True)
        cls.outro_gestor = get_user_model().objects.create_user("outro-previa", is_staff=True)
        for numero in range(8):
            questao = Questao.objects.create(
                componente=cls.componente,
                professor_nome="Docente",
                professor_email="docente@example.com",
                enunciado=f"Comando {numero}",
                justificativa="Resposta.",
                status="APROVADA",
            )
            for indice, tamanho in enumerate((4, 8, 12, 16)):
                Alternativa.objects.create(
                    questao=questao,
                    texto="z" * tamanho,
                    eh_correta=indice == numero % 4,
                )

    def setUp(self):
        self.client.force_login(self.gestor)

    def parametros(self, quantidade=4):
        return {
            "componentes": [self.componente.pk],
            f"quantidade_{self.componente.pk}": quantidade,
            "tipo_prova": "integrada",
            "periodo_prova": "8º Período",
            "turma_prova": "B & C",
        }

    def test_previa_antecede_prova_sem_ressorteio(self):
        resposta = self.client.get(reverse("prova_gerada"), {**self.parametros(), "preflight": "1"})
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta["Cache-Control"], "no-store")
        dados = resposta.json()
        self.assertEqual(dados["total"], 4)
        self.assertEqual(sum(item["quantidade"] for item in dados["distribuicao"]), 4)
        self.assertEqual(sum(item["percentual"] for item in dados["distribuicao"]), 100.0)
        destino = dados["url_abertura"]
        self.assertEqual(urlparse(destino).path, reverse("prova_gerada"))
        token = parse_qs(urlparse(destino).query)["prova_token"][0]
        payload = signing.loads(token, salt="questoes.prova.preflight.v1", max_age=3600)
        self.assertEqual(payload["usuario"], self.gestor.pk)
        self.assertEqual(payload["codigo"], dados["codigo_prova"])
        self.assertEqual(len(payload["ids"]), 4)
        self.assertEqual(len(set(payload["ids"])), 4)
        self.assertEqual(payload["periodo_prova"], "8º Período")
        self.assertEqual(payload["turma_prova"], "B & C")

        prova = self.client.get(destino + "&periodo_prova=adulterado&turma_prova=adulterada")
        self.assertEqual(prova.status_code, 200)
        self.assertEqual(prova.context["codigo_prova"], dados["codigo_prova"])
        self.assertEqual(prova.context["titulo_topo"], "8º Período")
        self.assertContains(prova, "B &amp; C")
        ids_impressos = [questao.pk for questao in prova.context["questoes_selecionadas"]]
        self.assertEqual(ids_impressos, payload["ids"])
        comentado = self.client.get(
            reverse("prova_gabarito") + "?" + prova.context["parametros_gabarito_comentado"]
        )
        self.assertContains(comentado, "8º Período")
        self.assertContains(comentado, "B &amp; C")
        self.assertContains(comentado, dados["codigo_prova"])

        gabarito = self.client.get(
            reverse("prova_gabarito_csv"),
            {"ids": ",".join(str(pk) for pk in payload["ids"])},
        )
        self.assertEqual(gabarito.status_code, 200)
        linhas = list(csv.DictReader(io.StringIO(gabarito.content.decode("utf-8-sig"))))
        self.assertEqual(len(linhas), 4)
        self.assertTrue(all(linha["codigo_prova"] == dados["codigo_prova"] for linha in linhas))

        # A letra correta que sai na prova deve ser a anunciada no popup.
        contagens = {letra: 0 for letra in "ABCD"}
        for questao in prova.context["questoes_selecionadas"]:
            for indice, alternativa in enumerate(questao.alternativas_finais):
                if alternativa.eh_correta:
                    contagens["ABCD"[indice]] += 1
        self.assertEqual(
            {item["letra"]: item["quantidade"] for item in dados["distribuicao"]},
            contagens,
        )

    def test_previa_exportacao_txt_usa_mesma_selecao(self):
        parametros = {**self.parametros(), "preflight": "1", "formato_txt": "1"}
        previa = self.client.get(reverse("prova_gerada"), parametros).json()
        self.assertTrue(previa["exportar_txt"])
        final = self.client.get(previa["url_abertura"])
        self.assertEqual(final.status_code, 200)
        self.assertIn("text/plain", final["Content-Type"])
        self.assertEqual(len(final.content.decode().split("\n\n")), 4)

    def test_link_falsificado_ou_outro_usuario_nao_abre(self):
        previa = self.client.get(reverse("prova_gerada"), {**self.parametros(), "preflight": "1"}).json()
        endereco = previa["url_abertura"]
        self.client.force_login(self.outro_gestor)
        self.assertRedirects(self.client.get(endereco), reverse("pagina_gerar_prova"))
        self.client.force_login(self.gestor)
        self.assertRedirects(self.client.get(endereco + "alterado"), reverse("pagina_gerar_prova"))

    def test_retorna_erro_json_para_formulario_invalido(self):
        resposta = self.client.get(reverse("prova_gerada"), {"preflight": "1"})
        self.assertEqual(resposta.status_code, 400)
        self.assertIn("erro", resposta.json())
        for campos in (
            {"turma_prova": " "},
            {"periodo_prova": "x" * 61},
            {"turma_prova": "B\nC"},
        ):
            with self.subTest(campos=campos):
                params = {**self.parametros(), **campos, "preflight": "1"}
                resposta = self.client.get(reverse("prova_gerada"), params)
                self.assertEqual(resposta.status_code, 400)
                self.assertIn("período da prova e a turma", resposta.json()["erro"])

    def test_gabaritos_da_prova_aberta_detectam_alteracao_da_letra(self):
        previa = self.client.get(reverse("prova_gerada"), {**self.parametros(), "preflight": "1"}).json()
        prova = self.client.get(previa["url_abertura"])
        questao = prova.context["questoes_selecionadas"][0]
        alternativas = questao.alternativas.order_by("pk")
        incorreta = alternativas.filter(eh_correta=False).first()
        alternativas.update(eh_correta=False)
        Alternativa.objects.filter(pk=incorreta.pk).update(eh_correta=True)
        comentado = reverse("prova_gabarito") + "?" + prova.context["parametros_gabarito_comentado"]
        csv_url = reverse("prova_gabarito_csv") + "?" + prova.context["parametros_gabarito"]
        self.assertRedirects(self.client.get(comentado), reverse("pagina_gerar_prova"))
        self.assertEqual(self.client.get(csv_url).status_code, 422)

    def test_modal_e_codigo_visiveis_no_gerador(self):
        resposta = self.client.get(reverse("pagina_gerar_prova"))
        self.assertContains(resposta, 'id="modal-gabarito"')
        self.assertContains(resposta, "modalGabarito.showModal()")
        self.assertContains(resposta, "Confirmar e abrir prova")
        self.assertContains(resposta, "preserva a ordem das alternativas")
        self.assertContains(resposta, 'name="periodo_prova"')
        self.assertContains(resposta, 'name="turma_prova"')
