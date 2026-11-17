"""Semestre letivo no cartão e reimpressão apenas do cartão de provas recentes."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Alternativa, ComponenteCurricular, Periodo, ProvaGerada, Questao, Semestre


class CartaoRecenteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        semestre = Semestre.objects.create(nome="2026.2")
        periodo = Periodo.objects.create(nome="5º Período", semestre=semestre)
        cls.componente = ComponenteCurricular.objects.create(
            nome="Imunologia", periodo=periodo, numero_questoes_prova=3
        )
        usuarios = get_user_model().objects
        cls.gestor = usuarios.create_user("gestor-recente", is_staff=True)
        cls.outro = usuarios.create_user("outro-gestor", is_staff=True)
        for i in range(3):
            questao = Questao.objects.create(
                componente=cls.componente,
                tipo_questao="RESPOSTA_UNICA",
                professor_nome="Professor",
                professor_email="professor@example.com",
                enunciado=f"Questão {i}",
                justificativa="Teste",
                status="APROVADA",
            )
            for j in range(4):
                Alternativa.objects.create(questao=questao, texto=f"Opção {j}", eh_correta=j == i)

    def gerar(self, **extra):
        return self.client.get(
            reverse("prova_gerada"),
            {"componentes": [self.componente.pk], f"quantidade_{self.componente.pk}": 3, **extra},
        )

    def test_cartao_exibe_semestre_letivo(self):
        self.client.force_login(self.gestor)
        resposta = self.gerar()
        self.assertContains(resposta, "SEMESTRE LETIVO: 2026.2")

    def test_prova_gerada_fica_registrada_sem_duplicar(self):
        self.client.force_login(self.gestor)
        self.gerar()
        self.assertEqual(ProvaGerada.objects.filter(usuario=self.gestor).count(), 1)
        registro = ProvaGerada.objects.get()
        self.assertEqual(registro.semestre_letivo, "2026.2")
        # Reabrir a mesma prova (mesmo código) não cria outro registro.
        reabrir = reverse("prova_gerada") + "?ids=" + ",".join(map(str, registro.questao_ids))
        self.client.get(reabrir)
        from .views import _registrar_prova

        _registrar_prova(
            self.gestor,
            registro.questao_ids,
            registro.tipo_prova,
            registro.codigo,
            registro.titulo_topo,
            {},
            registro.semestre_letivo,
        )
        self.assertEqual(ProvaGerada.objects.filter(usuario=self.gestor).count(), 1)
        pagina = self.client.get(reverse("pagina_gerar_prova"))
        self.assertContains(pagina, registro.codigo)

    def test_reimpressao_somente_cartao_sem_prova(self):
        self.client.force_login(self.gestor)
        original = self.gerar()
        registro = ProvaGerada.objects.get()
        resposta = self.client.get(reverse("cartoes_gerados"), {"provas": [registro.pk]})
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "SEMESTRE LETIVO: 2026.2")
        self.assertContains(resposta, original.context["codigo_prova"])
        self.assertContains(resposta, "Cartão de Respostas")
        self.assertNotContains(resposta, 'class="pagina page-break-after capa-container"')
        self.assertNotContains(resposta, "Questão 0")
        self.assertEqual(resposta["Cache-Control"], "no-store")

    def test_varios_cartoes_em_uma_impressao(self):
        self.client.force_login(self.gestor)
        self.gerar(periodo_prova="5º Período", turma_prova="A")
        self.gerar(periodo_prova="5º Período", turma_prova="B")
        pks = list(ProvaGerada.objects.values_list("pk", flat=True))
        self.assertEqual(len(pks), 2)
        resposta = self.client.get(reverse("cartoes_gerados"), {"provas": pks})
        self.assertContains(resposta, '<section class="pagina cartao-respostas">', count=2)

    def test_isolamento_entre_usuarios(self):
        self.client.force_login(self.gestor)
        self.gerar()
        registro = ProvaGerada.objects.get()
        self.client.force_login(self.outro)
        self.assertNotContains(self.client.get(reverse("pagina_gerar_prova")), registro.codigo)
        resposta = self.client.get(reverse("cartoes_gerados"), {"provas": [registro.pk]}, follow=True)
        self.assertContains(resposta, "não está mais disponível")

    def test_anonimo_e_nao_staff_nao_acessam(self):
        self.client.force_login(self.gestor)
        self.gerar()
        registro = ProvaGerada.objects.get()
        self.client.logout()
        resposta = self.client.get(reverse("cartoes_gerados"), {"provas": [registro.pk]})
        self.assertEqual(resposta.status_code, 302)
        self.assertIn("login", resposta["Location"])

    def test_entradas_invalidas_sao_recusadas(self):
        self.client.force_login(self.gestor)
        for params in ({}, {"provas": ["abc"]}, {"provas": ["1"] * 21}, {"provas": ["999999"]}):
            with self.subTest(params=params):
                resposta = self.client.get(reverse("cartoes_gerados"), params)
                self.assertRedirects(resposta, reverse("pagina_gerar_prova"))

    def test_recusa_quando_conteudo_mudou(self):
        self.client.force_login(self.gestor)
        self.gerar()
        registro = ProvaGerada.objects.get()
        Questao.objects.filter(pk=registro.questao_ids[0]).update(enunciado="Alterada depois")
        resposta = self.client.get(reverse("cartoes_gerados"), {"provas": [registro.pk]}, follow=True)
        self.assertContains(resposta, "mudou desde que a prova foi gerada")
        self.assertNotContains(resposta, "Cartão de Respostas")

    def test_recusa_quando_questao_deixou_de_ser_aprovada(self):
        self.client.force_login(self.gestor)
        self.gerar()
        registro = ProvaGerada.objects.get()
        Questao.objects.filter(pk=registro.questao_ids[0]).update(status="PENDENTE")
        resposta = self.client.get(reverse("cartoes_gerados"), {"provas": [registro.pk]}, follow=True)
        self.assertContains(resposta, "alteradas ou removidas")


class CartaoAvulsoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.gestor = get_user_model().objects.create_user("gestor-avulso", is_staff=True)

    def pedir(self, **extra):
        dados = {
            "semestre_letivo": "2026.1",
            "periodo_prova": "8º Período",
            "turma_prova": "B",
            "quantidade": 50,
            "alternativas": 4,
            **extra,
        }
        return self.client.get(reverse("cartao_avulso"), dados)

    def test_gera_cartao_com_identificacao_e_quantidade(self):
        self.client.force_login(self.gestor)
        resposta = self.pedir()
        self.assertContains(resposta, "SEMESTRE LETIVO: 2026.1")
        self.assertContains(resposta, "8º Período · Turma B")
        self.assertContains(resposta, "AVULSO")
        self.assertContains(resposta, 'class="cartao-numero">050</span>')
        self.assertNotContains(resposta, 'class="cartao-numero">051</span>')
        self.assertNotContains(resposta, 'class="cartao-bolha">E</span>')
        self.assertNotContains(resposta, 'class="pagina page-break-after capa-container"')

    def test_cinco_alternativas_e_varias_folhas(self):
        self.client.force_login(self.gestor)
        resposta = self.pedir(quantidade=121, alternativas=5)
        self.assertContains(resposta, '<section class="pagina cartao-respostas">', count=2)
        self.assertContains(resposta, 'class="cartao-bolha">E</span>')

    def test_entradas_invalidas_sao_recusadas(self):
        self.client.force_login(self.gestor)
        for extra in (
            {"quantidade": 0},
            {"quantidade": 301},
            {"alternativas": 6},
            {"turma_prova": ""},
            {"semestre_letivo": "a\nb"},
            {"quantidade": "x"},
        ):
            with self.subTest(extra=extra):
                self.assertRedirects(self.pedir(**extra), reverse("pagina_gerar_prova"))

    def test_exige_login(self):
        resposta = self.pedir()
        self.assertEqual(resposta.status_code, 302)
        self.assertIn("login", resposta["Location"])
