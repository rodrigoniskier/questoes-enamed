"""Regressões de seleção sem repetição, Residência e cartões independentes."""

from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core import signing
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from .exam_selection import identidade_questao, questoes_sao_unicas, selecionar_questoes_unicas
from .models import Alternativa, ComponenteCurricular, Periodo, Questao, Semestre


class SelecaoUnicaUnitTests(SimpleTestCase):
    def questao(self, pk, enunciado, texto_base="Caso clínico.", imagem=""):
        return SimpleNamespace(
            pk=pk,
            tipo_questao="RESPOSTA_UNICA",
            texto_base=texto_base,
            enunciado=enunciado,
            proposicao_dois="",
            imagem=imagem,
        )

    def test_copias_com_ids_diferentes_sao_reconhecidas(self):
        primeira = self.questao(1, "  Qual o diagnóstico? ")
        copia = self.questao(2, "qual o DIAGNÓSTICO?\n")
        self.assertEqual(identidade_questao(primeira), identidade_questao(copia))
        self.assertFalse(questoes_sao_unicas([primeira, copia]))
        self.assertEqual(len(selecionar_questoes_unicas([(1, [primeira, copia])], {1: 2})), 1)

    def test_emparelhamento_preserva_a_cota_dos_componentes(self):
        comum_a = self.questao(1, "Comando compartilhado")
        exclusivo_a = self.questao(2, "Comando exclusivo")
        comum_b = self.questao(3, "Comando compartilhado")
        escolhidas = selecionar_questoes_unicas(
            [(10, [comum_a, exclusivo_a]), (20, [comum_b])], {10: 1, 20: 1}
        )
        self.assertEqual([q.pk for q in escolhidas], [2, 3])
        self.assertTrue(questoes_sao_unicas(escolhidas))

    def test_mesmo_comando_com_casos_ou_imagens_distintos_nao_e_duplicata(self):
        a = self.questao(1, "Qual o diagnóstico?", texto_base="Caso clínico A")
        b = self.questao(2, "Qual o diagnóstico?", texto_base="Caso clínico B")
        c = self.questao(3, "Qual o diagnóstico?", imagem="imagem1.jpg")
        d = self.questao(4, "Qual o diagnóstico?", imagem="imagem2.jpg")
        self.assertTrue(questoes_sao_unicas([a, b, c, d]))


class GeradorQuestaoUnicaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        semestre = Semestre.objects.create(nome="2027.1")
        periodo = Periodo.objects.create(nome="5º Período", semestre=semestre)
        cls.a = ComponenteCurricular.objects.create(
            nome="Clínica Médica", periodo=periodo, numero_questoes_prova=2
        )
        cls.b = ComponenteCurricular.objects.create(nome="Cirurgia", periodo=periodo, numero_questoes_prova=2)
        cls.gestor = get_user_model().objects.create_user("gerador-unico", is_staff=True)
        cls.q1 = cls.fazer(cls.a, "Comando igual", "INTEGRADA")
        cls.q2 = cls.fazer(cls.b, "  comando IGUAL  ", "INTEGRADA")
        cls.q3 = cls.fazer(cls.b, "Comando exclusivo", "INTEGRADA")
        cls.qr = cls.fazer(cls.a, "Questão de Residência", "RESIDENCIA")

    @classmethod
    def fazer(cls, componente, enunciado, uso):
        questao = Questao.objects.create(
            componente=componente,
            professor_nome="Professor",
            professor_email="docente@example.com",
            texto_base="História clínica do paciente.",
            enunciado=enunciado,
            justificativa="Gabarito comentado.",
            tipo_questao="RESPOSTA_UNICA",
            status="APROVADA",
            uso_prova=uso,
        )
        for indice, letra in enumerate("ABCD"):
            Alternativa.objects.create(questao=questao, texto=f"Alternativa {letra}", eh_correta=indice == 0)
        return questao

    def setUp(self):
        self.client.force_login(self.gestor)

    def test_gerador_omite_copia_e_mantem_questoes_unicas(self):
        resposta = self.client.get(
            reverse("prova_gerada"),
            {
                "componentes": [self.a.pk, self.b.pk],
                f"quantidade_{self.a.pk}": 1,
                f"quantidade_{self.b.pk}": 2,
            },
        )
        self.assertEqual(resposta.status_code, 200)
        escolhidas = resposta.context["questoes_selecionadas"]
        self.assertEqual({q.pk for q in escolhidas}, {self.q1.pk, self.q3.pk})
        self.assertTrue(questoes_sao_unicas(escolhidas))

    def test_residencia_seleciona_apenas_destino_correto(self):
        resposta = self.client.get(
            reverse("prova_gerada"),
            {
                "componentes": [self.a.pk],
                "tipo_prova": "residencia",
                f"quantidade_{self.a.pk}": 1,
            },
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual([q.pk for q in resposta.context["questoes_selecionadas"]], [self.qr.pk])
        self.assertEqual(resposta.context["titulo_topo"], "PROVA DE RESIDÊNCIA MÉDICA - UNIPÊ")

    def test_token_assinado_com_questoes_repetidas_e_rejeitado(self):
        token = signing.dumps(
            {
                "usuario": self.gestor.pk,
                "ids": [self.q1.pk, self.q2.pk],
                "tipo_prova": "integrada",
                "exportar_txt": False,
                "codigo": "teste",
            },
            salt="questoes.prova.preflight.v1",
        )
        resposta = self.client.get(reverse("prova_gerada"), {"prova_token": token}, follow=True)
        self.assertContains(resposta, "questões repetidas")

    def test_botoes_dos_cartoes_nao_dependem_da_selecao_de_componentes(self):
        resposta = self.client.get(reverse("pagina_gerar_prova"))
        self.assertContains(
            resposta,
            """const submitButtons = document.querySelectorAll('form[action="{% url 'prova_gerada' %}"] button[type="submit"]');""".replace(
                "{% url 'prova_gerada' %}", reverse("prova_gerada")
            ),
        )
        self.assertContains(resposta, "Gerar cartão avulso")
        self.assertContains(resposta, "Gerar somente o cartão de respostas")
