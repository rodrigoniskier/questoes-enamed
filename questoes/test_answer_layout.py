"""Regressão da ordem das alternativas do autor nos documentos da prova."""

import csv
import io

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .answer_layout import montar_alternativas, resumo_gabarito
from .models import Alternativa, ComponenteCurricular, Periodo, Questao, Semestre
from .multiple_choice import gerar_alternativas_multipla_escolha


class AnswerLayoutTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        semestre = Semestre.objects.create(nome="2026.2", ativo=True)
        periodo = Periodo.objects.create(nome="4º Período", semestre=semestre)
        cls.componente = ComponenteCurricular.objects.create(
            nome="Clínica Médica", periodo=periodo, numero_questoes_prova=8
        )
        cls.gestor = get_user_model().objects.create_user("gestor-layout", is_staff=True)

    def criar_questao(self, gabarito, tipo="RESPOSTA_UNICA", combinacoes=None):
        questao = Questao.objects.create(
            componente=self.componente,
            tipo_questao=tipo,
            professor_nome="Docente",
            professor_email="docente@example.com",
            enunciado="Selecione a opção correta.",
            justificativa="Justificativa.",
            status="APROVADA",
            combinacoes_personalizadas=combinacoes or [],
        )
        textos = ("Média", "A alternativa mais extensa", "Curta", "Outra alternativa")[: len(gabarito)]
        if len(gabarito) == 5:
            textos += ("E",)
        for texto, correta in zip(textos, gabarito):
            Alternativa.objects.create(questao=questao, texto=texto, eh_correta=correta)
        return questao

    def test_respostas_unicas_e_assercao_preservam_ordem_e_letra_inclusive_e(self):
        questoes = [
            self.criar_questao([False, True, False, False]),
            self.criar_questao([False, False, False, False, True]),
            self.criar_questao([False, False, True, False], tipo="ASSERCAO_RAZAO"),
        ]
        alternativas = montar_alternativas(questoes)
        for questao in questoes:
            self.assertEqual(alternativas[questao.pk], list(questao.alternativas.order_by("pk")))
        resumo = resumo_gabarito(questoes, alternativas)
        self.assertEqual(
            {item["letra"]: item["quantidade"] for item in resumo["distribuicao"]},
            {"A": 0, "B": 1, "C": 1, "D": 0, "E": 1},
        )

    def test_resposta_multipla_preserva_combinacoes_editadas_e_automaticas(self):
        combinacoes = [[1, 3], [2, 3], [1], [2]]
        manual = self.criar_questao([True, False, True], "MULTIPLA_ESCOLHA", combinacoes)
        automaticas = self.criar_questao([True, True, False], "MULTIPLA_ESCOLHA")
        layout = montar_alternativas([manual, automaticas])
        self.assertEqual([alt["indices"] for alt in layout[manual.pk]], combinacoes)
        self.assertTrue(layout[manual.pk][0]["eh_correta"])
        self.assertEqual(layout[automaticas.pk], gerar_alternativas_multipla_escolha(automaticas))

    def test_prova_previa_gabaritos_e_txt_conservam_letras_repetidas(self):
        questoes = [self.criar_questao([False, True, False, False]) for _ in range(4)]
        self.client.force_login(self.gestor)
        parametros = {"componentes": self.componente.pk, f"quantidade_{self.componente.pk}": 4}
        previa = self.client.get(reverse("prova_gerada"), {**parametros, "preflight": "1"}).json()
        self.assertEqual(
            {item["letra"]: item["quantidade"] for item in previa["distribuicao"]},
            {"A": 0, "B": 4, "C": 0, "D": 0},
        )
        prova = self.client.get(previa["url_abertura"])
        self.assertEqual(prova.context["codigo_prova"], previa["codigo_prova"])
        selecionadas = prova.context["questoes_selecionadas"]
        self.assertCountEqual(selecionadas, questoes)
        for questao in selecionadas:
            self.assertEqual(questao.alternativas_finais, list(questao.alternativas.order_by("pk")))

        gabarito = self.client.get(
            reverse("prova_gabarito") + "?" + prova.context["parametros_gabarito_comentado"]
        )
        self.assertEqual(gabarito.context["codigo_prova"], prova.context["codigo_prova"])
        self.assertEqual([q.letra_gabarito for q in gabarito.context["questoes_ordenadas"]], ["B"] * 4)
        exportacao = self.client.get(
            reverse("prova_gabarito_csv") + "?" + prova.context["parametros_gabarito"]
        )
        linhas = list(csv.DictReader(io.StringIO(exportacao.content.decode("utf-8-sig"))))
        self.assertEqual([linha["resposta_correta"] for linha in linhas], ["B"] * 4)

        texto = self.client.get(reverse("prova_gerada"), {**parametros, "formato_txt": "1"})
        self.assertEqual(texto.status_code, 200)
        linhas = [linha.split("\t") for linha in texto.content.decode().splitlines() if linha]
        self.assertEqual(len(linhas), 4)
        for linha in linhas:
            self.assertEqual(
                linha[2::2], ["Média", "A alternativa mais extensa", "Curta", "Outra alternativa"]
            )
            self.assertEqual(linha[3::2], ["incorrect", "correct", "incorrect", "incorrect"])
