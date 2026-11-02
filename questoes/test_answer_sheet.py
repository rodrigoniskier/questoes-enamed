"""Validação do cartão para fotografia e correção posterior com gabarito CSV."""

import csv
import io
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from .answer_sheet import codigo_prova, linhas_do_gabarito, paginas_do_cartao
from .models import Alternativa, ComponenteCurricular, Periodo, Questao, Semestre


class PaginasCartaoTests(SimpleTestCase):
    def gerar(self, quantidade, cinco=None):
        questoes = []
        opcoes = {}
        for indice in range(quantidade):
            questao = SimpleNamespace(pk=indice + 1, enunciado=f"Questão {indice + 1}", texto_base="")
            questoes.append(questao)
            numero_opcoes = 5 if indice == cinco else 4
            opcoes[questao.pk] = [
                {"texto": f"Opção {letra}", "eh_correta": i == 0}
                for i, letra in enumerate("ABCDE"[:numero_opcoes])
            ]
        return questoes, opcoes

    def test_60_cartoes_em_duas_colunas_e_61_em_tres(self):
        for quantidade, colunas in ((1, 2), (60, 2), (61, 3), (100, 3), (120, 3)):
            with self.subTest(quantidade=quantidade):
                questoes, opcoes = self.gerar(quantidade)
                paginas = paginas_do_cartao(questoes, opcoes)
                self.assertEqual(len(paginas), 1)
                self.assertEqual(paginas[0]["numero_colunas"], colunas)
                self.assertEqual(
                    [item["numero"] for coluna in paginas[0]["colunas"] for item in coluna],
                    list(range(1, quantidade + 1)),
                )
                self.assertTrue(
                    all(item["letras"] == "ABCD" for coluna in paginas[0]["colunas"] for item in coluna)
                )

    def test_121_itens_geram_duas_folhas_com_numeracao_sem_lacunas(self):
        questoes, opcoes = self.gerar(121)
        paginas = paginas_do_cartao(questoes, opcoes)
        self.assertEqual(len(paginas), 2)
        self.assertEqual([(p["primeira"], p["ultima"]) for p in paginas], [(1, 120), (121, 121)])
        self.assertEqual([p["total"] for p in paginas], [2, 2])

    def test_cinco_alternativas_apenas_na_linha_com_cinco_opcoes(self):
        questoes, opcoes = self.gerar(4, cinco=2)
        itens = [item for coluna in paginas_do_cartao(questoes, opcoes)[0]["colunas"] for item in coluna]
        self.assertEqual([item["letras"] for item in itens], ["ABCD", "ABCD", "ABCDE", "ABCD"])

    def test_codigo_muda_com_ordem_conteudo_e_gabarito_mas_e_estavel(self):
        questoes, opcoes = self.gerar(4)
        anterior = codigo_prova(questoes, opcoes)
        self.assertEqual(anterior, codigo_prova(questoes, opcoes))
        self.assertNotEqual(anterior, codigo_prova(list(reversed(questoes)), opcoes))
        opcoes[1][0]["eh_correta"] = False
        opcoes[1][2]["eh_correta"] = True
        self.assertNotEqual(anterior, codigo_prova(questoes, opcoes))
        opcoes[1][0]["eh_correta"] = True
        opcoes[1][2]["eh_correta"] = False
        questoes[0].enunciado = "Texto revisto"
        self.assertNotEqual(anterior, codigo_prova(questoes, opcoes))


class GeradorCartaoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        semestre = Semestre.objects.create(nome="2026.2")
        periodo = Periodo.objects.create(nome="5º Período", semestre=semestre)
        cls.componente = ComponenteCurricular.objects.create(
            nome="Imunologia", periodo=periodo, numero_questoes_prova=5
        )
        cls.gestor = get_user_model().objects.create_user("gestor-cartao", is_staff=True)
        cls.questoes = []
        for indice_correto in range(4):
            questao = Questao.objects.create(
                componente=cls.componente,
                tipo_questao="RESPOSTA_UNICA",
                professor_nome="Professor",
                professor_email="professor@example.com",
                texto_base="Caso clínico.",
                enunciado=f"Questão sintética {indice_correto}",
                justificativa="Testes",
                status="APROVADA",
            )
            for indice, tamanho in enumerate((4, 8, 12, 16)):
                Alternativa.objects.create(
                    questao=questao, texto="x" * tamanho, eh_correta=indice == indice_correto
                )
            cls.questoes.append(questao)

        cls.assercao = Questao.objects.create(
            componente=cls.componente,
            tipo_questao="ASSERCAO_RAZAO",
            professor_nome="Professor",
            professor_email="professor@example.com",
            enunciado="Asserção I",
            proposicao_dois="Asserção II",
            justificativa="Justificativa.",
            status="APROVADA",
        )
        for indice in range(5):
            Alternativa.objects.create(
                questao=cls.assercao, texto=f"Alternativa {indice}", eh_correta=indice == 4
            )

    def setUp(self):
        self.client.force_login(self.gestor)

    def ids(self):
        return ",".join(str(q.pk) for q in self.questoes + [self.assercao])

    def test_impressao_exibe_identificacao_e_apenas_opcoes_validas(self):
        # A seleção do banco contém todos os cinco itens.
        resposta = self.client.get(
            reverse("prova_gerada"),
            {
                "componentes": [self.componente.pk],
                f"quantidade_{self.componente.pk}": 5,
            },
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(len(resposta.context["paginas_cartao"]), 1)
        self.assertContains(resposta, "NOME COMPLETO")
        self.assertContains(resposta, "RGM:")
        self.assertContains(resposta, "CPF (somente se não houver RGM)")
        self.assertContains(resposta, "Imprimir somente cartão de respostas")
        self.assertEqual(resposta.content.decode().count('class="cartao-marca '), 4)
        self.assertContains(resposta, "CÓDIGO DA PROVA:")
        self.assertContains(resposta, 'class="cartao-bolha">E</span>', count=1)
        self.assertContains(resposta, 'class="cartao-numero">005</span>')

    def test_capa_moderna_preserva_marca_e_campos_dinamicos(self):
        resposta = self.client.get(
            reverse("prova_gerada"),
            {
                "componentes": [self.componente.pk],
                f"quantidade_{self.componente.pk}": 5,
            },
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "PROVA INTEGRADA")
        self.assertContains(resposta, "MEDICINA")
        self.assertContains(resposta, "Boa Prova!")
        self.assertContains(resposta, "INFORMAÇÕES DO ESTUDANTE")
        self.assertContains(resposta, "Nome completo:")
        self.assertContains(resposta, "RGM:")
        self.assertContains(resposta, "CPF (opcional):")
        self.assertContains(resposta, "Turma:")
        self.assertContains(resposta, "Data:")
        self.assertContains(resposta, "Assinatura:")
        self.assertContains(resposta, "Assinalar as disciplinas em curso:")
        self.assertContains(resposta, self.componente.nome)
        self.assertContains(resposta, "5º Período")
        self.assertContains(resposta, resposta.context["codigo_prova"])
        self.assertContains(resposta, "images/logo.jpg")
        self.assertContains(resposta, "capa-logo-oficial")
        self.assertNotContains(resposta, "<svg")
        self.assertNotContains(resposta, "capa-arte-vetorial")
        self.assertContains(resposta, "capa-disciplinas")
        self.assertNotContains(resposta, "images/capa_background.jpg")

    def test_capa_cartao_e_csv_compartilham_codigo_da_selecao_real(self):
        resposta = self.client.get(
            reverse("prova_gerada"),
            {"componentes": [self.componente.pk], f"quantidade_{self.componente.pk}": 5},
        )
        codigo = resposta.context["codigo_prova"]
        ids = ",".join(str(pk) for pk in resposta.context["questao_ids"])
        csv_resposta = self.client.get(reverse("prova_gabarito_csv"), {"ids": ids})
        linhas = list(csv.DictReader(io.StringIO(csv_resposta.content.decode("utf-8-sig"))))
        self.assertTrue(linhas)
        self.assertEqual({linha["codigo_prova"] for linha in linhas}, {codigo})
        html = resposta.content.decode()
        capa = html.split('aria-label="Capa da avaliação"', 1)[1].split('<div class="pagina">', 1)[0]
        cartao = html.split('<section class="pagina cartao-respostas">', 1)[1]
        self.assertIn(codigo, capa)
        self.assertIn(codigo, cartao)

    def test_capa_preserva_modalidades_e_identificacao_multiperiodo(self):
        for tipo, uso, titulo in (
            ("reposicao", "REPOSICAO", "PROVA DE REPOSIÇÃO"),
            ("residencia", "RESIDENCIA", "PROVA DE RESIDÊNCIA MÉDICA - UNIPÊ"),
            ("simulado", "SIMULADO", "SIMULADO"),
        ):
            with self.subTest(tipo=tipo):
                Questao.objects.filter(componente=self.componente).update(uso_prova=uso)
                resposta = self.client.get(
                    reverse("prova_gerada"),
                    {"tipo_prova": tipo, "componentes": [self.componente.pk]},
                )
                self.assertEqual(resposta.status_code, 200)
                self.assertEqual(resposta.context["titulo_topo"], titulo)
                self.assertContains(resposta, titulo)

        Questao.objects.filter(componente=self.componente).update(uso_prova="INTEGRADA")
        outro_periodo = Periodo.objects.create(nome="6º Período", semestre=self.componente.periodo.semestre)
        outro_componente = ComponenteCurricular.objects.create(
            nome="Clínica Integrada", periodo=outro_periodo, numero_questoes_prova=1
        )
        self.assercao.componente = outro_componente
        self.assercao.save(update_fields=["componente"])
        resposta = self.client.get(
            reverse("prova_gerada"), {"componentes": [self.componente.pk, outro_componente.pk]}
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.context["titulo_topo"], "PROVA INTEGRADA MULTIPERÍODO")
        self.assertContains(resposta, self.componente.nome)
        self.assertContains(resposta, outro_componente.nome)

    def test_csv_tem_codigo_igual_ao_gabarito_impresso(self):
        ids = self.ids()
        resposta = self.client.get(reverse("prova_gabarito_csv"), {"ids": ids})
        self.assertEqual(resposta.status_code, 200)
        self.assertIn("attachment;", resposta["Content-Disposition"])
        texto = resposta.content.decode("utf-8-sig")
        linhas = list(csv.DictReader(io.StringIO(texto)))
        self.assertEqual(len(linhas), 5)
        codigo = linhas[0]["codigo_prova"]
        self.assertTrue(all(linha["codigo_prova"] == codigo for linha in linhas))
        self.assertEqual([int(linha["numero_questao"]) for linha in linhas], list(range(1, 6)))
        self.assertEqual([linha["alternativas_validas"] for linha in linhas], ["ABCD"] * 4 + ["ABCDE"])
        self.assertEqual(linhas[-1]["resposta_correta"], "E")
        self.assertEqual([linha["total_questoes"] for linha in linhas], ["5"] * 5)
        gabarito = self.client.get(reverse("prova_gabarito"), {"ids": ids})
        self.assertContains(gabarito, codigo)

    def test_exportacao_nao_aceita_questoes_invalidas_ou_usuario_nao_autorizado(self):
        url = reverse("prova_gabarito_csv")
        self.client.logout()
        self.assertEqual(self.client.get(url, {"ids": self.ids()}).status_code, 302)
        self.client.force_login(self.gestor)
        for ids in ("", "abc", "1,1", "999999999", "1,-1"):
            self.assertEqual(self.client.get(url, {"ids": ids}).status_code, 400)

    def test_helper_registra_numeracao_sem_alterar_gabarito(self):
        questoes = self.questoes
        alternativas = {q.pk: list(q.alternativas.order_by("pk")) for q in questoes}
        linhas = linhas_do_gabarito(questoes, alternativas)
        self.assertEqual([linha["correta"] for linha in linhas], list("ABCD"))
