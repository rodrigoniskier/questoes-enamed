"""Regressão da ordenação por extensão e do balanceamento do gabarito."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .answer_layout import LayoutBalanceError, montar_layout_balanceado
from .models import Alternativa, ComponenteCurricular, Periodo, Questao, Semestre
from .views import gerar_alternativas_multipla_escolha


class AnswerLayoutTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        semestre = Semestre.objects.create(nome="2026.2", ativo=True)
        periodo = Periodo.objects.create(nome="4º Período", semestre=semestre)
        cls.componente = ComponenteCurricular.objects.create(
            nome="Clínica Médica", periodo=periodo, numero_questoes_prova=8
        )
        cls.gestor = get_user_model().objects.create_user(
            "gestor-layout", is_staff=True
        )

    def criar_unica(self, indice_correto, comprimentos=(4, 8, 12, 16)):
        questao = Questao.objects.create(
            componente=self.componente,
            tipo_questao="RESPOSTA_UNICA",
            professor_nome="Docente",
            professor_email="docente@example.com",
            enunciado="Selecione a opção correta.",
            justificativa="Justificativa.",
            status="APROVADA",
        )
        for indice, comprimento in enumerate(comprimentos):
            Alternativa.objects.create(
                questao=questao,
                texto="x" * comprimento,
                eh_correta=indice == indice_correto,
            )
        return questao

    def criar_multipla(self):
        questao = Questao.objects.create(
            componente=self.componente,
            tipo_questao="MULTIPLA_ESCOLHA",
            professor_nome="Docente",
            professor_email="docente@example.com",
            enunciado="Avalie as afirmativas.",
            justificativa="Justificativa.",
            status="APROVADA",
        )
        for indice in range(3):
            Alternativa.objects.create(
                questao=questao,
                texto=f"Afirmativa {indice + 1}",
                eh_correta=indice < 2,
            )
        return questao

    def layout(self, questoes):
        return montar_layout_balanceado(
            questoes, gerar_alternativas_multipla_escolha
        )

    def testar_balanceamento(self, questoes, layout):
        contagens = [0, 0, 0, 0]
        for questao in questoes:
            alternativas = layout[questao.pk]
            correta = [
                indice for indice, alternativa in enumerate(alternativas)
                if (
                    alternativa["eh_correta"]
                    if isinstance(alternativa, dict)
                    else alternativa.eh_correta
                )
            ]
            self.assertEqual(len(correta), 1)
            contagens[correta[0]] += 1
        self.assertLessEqual(max(contagens) - min(contagens), 1)

    def test_quatro_unicas_ficam_balanceadas_e_ordenadas(self):
        questoes = [self.criar_unica(indice) for indice in range(4)]
        layout = self.layout(questoes)
        self.testar_balanceamento(questoes, layout)
        for questao in questoes:
            extensoes = [len(a.texto) for a in layout[questao.pk]]
            self.assertIn(
                extensoes,
                [sorted(extensoes), sorted(extensoes, reverse=True)],
            )
        self.assertEqual(
            {
                q.pk: [a.pk for a in alternativas]
                for q, alternativas in ((q, layout[q.pk]) for q in questoes)
            },
            {
                q.pk: [a.pk for a in alternativas]
                for q, alternativas in (
                    (q, self.layout(questoes)[q.pk]) for q in questoes
                )
            },
        )

    def test_multiplas_preenchem_letras_ausentes(self):
        questoes = [self.criar_unica(0) for _ in range(4)]
        questoes.extend([self.criar_multipla(), self.criar_multipla()])
        layout = self.layout(questoes)
        self.testar_balanceamento(questoes, layout)
        for questao in questoes[-2:]:
            self.assertEqual(len(layout[questao.pk]), 4)
            self.assertEqual(
                len({a["texto"] for a in layout[questao.pk]}), 4
            )

    def test_empates_de_extensao_permitidos(self):
        questoes = [
            self.criar_unica(0, comprimentos=(6, 6, 6, 6))
            for _ in range(4)
        ]
        self.testar_balanceamento(questoes, self.layout(questoes))

    def test_distribuicao_exata_impossivel_bloqueia_geracao(self):
        questoes = [self.criar_unica(0) for _ in range(4)]
        with self.assertRaises(LayoutBalanceError):
            self.layout(questoes)

        self.client.force_login(self.gestor)
        url = reverse("prova_gerada")
        resposta = self.client.get(
            url,
            {
                "componentes": [self.componente.pk],
                f"quantidade_{self.componente.pk}": 4,
            },
            follow=True,
        )
        self.assertContains(
            resposta,
            "Não é possível equilibrar A, B, C e D",
        )
        self.assertTemplateUsed(resposta, "questoes/gerar_prova.html")

    def test_prova_gabarito_e_txt_usam_mesma_regra(self):
        questoes = [self.criar_unica(indice) for indice in range(4)]
        self.client.force_login(self.gestor)
        parametros = {
            "componentes": [self.componente.pk],
            f"quantidade_{self.componente.pk}": 4,
        }
        prova = self.client.get(reverse("prova_gerada"), parametros)
        self.assertEqual(prova.status_code, 200)
        selecionadas = prova.context["questoes_selecionadas"]
        self.assertEqual(set(questoes), set(selecionadas))
        self.testar_balanceamento(
            selecionadas,
            {q.pk: q.alternativas_finais for q in selecionadas},
        )

        gabarito = self.client.get(
            reverse("prova_gabarito"),
            {"ids": ",".join(str(q.pk) for q in selecionadas)},
        )
        self.assertEqual(gabarito.status_code, 200)
        for original, gabaritada in zip(
            selecionadas, gabarito.context["questoes_ordenadas"]
        ):
            self.assertEqual(
                [a.pk for a in original.alternativas_finais],
                [a.pk for a in gabaritada.alternativas_finais_exibicao],
            )
            letra = next(
                "ABCD"[i]
                for i, a in enumerate(original.alternativas_finais)
                if a.eh_correta
            )
            self.assertEqual(gabaritada.letra_gabarito, letra)

        texto = self.client.get(
            reverse("prova_gerada"), {**parametros, "formato_txt": "1"}
        )
        self.assertEqual(texto.status_code, 200)
        linhas = [l for l in texto.content.decode().splitlines() if l]
        self.assertEqual(len(linhas), 4)
        contagens = [0, 0, 0, 0]
        for linha in linhas:
            partes = linha.split("\t")
            self.assertEqual(partes[0], "MC")
            marcadores = partes[2::2]
            self.assertEqual(len(marcadores), 4)
            self.assertEqual(marcadores.count("correct"), 1)
            contagens[marcadores.index("correct")] += 1
        self.assertLessEqual(max(contagens) - min(contagens), 1)
