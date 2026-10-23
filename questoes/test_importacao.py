import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.db import IntegrityError, close_old_connections
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from .importacao import importar_questoes
from .models import (
    Alternativa,
    ComponenteCurricular,
    ImportacaoQuestao,
    Periodo,
    Questao,
    Semestre,
)


class DadosImportacao:
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.media = override_settings(MEDIA_ROOT=self.tmp.name)
        self.media.enable()
        self.addCleanup(self.media.disable)
        self.usuario = get_user_model().objects.create_superuser("admin", "admin@example.test", "teste")
        self.semestre = Semestre.objects.create(nome="2026.2", ativo=True)
        self.periodo = Periodo.objects.create(nome="1º Período", semestre=self.semestre)
        self.doador = ComponenteCurricular.objects.create(nome="Doador", periodo=self.periodo)
        self.receptor = ComponenteCurricular.objects.create(nome="Receptor", periodo=self.periodo)
        self.outro_semestre = Semestre.objects.create(nome="2025.2", ativo=True)
        self.periodo_outro = Periodo.objects.create(nome="Outro Período", semestre=self.outro_semestre)
        self.outro = ComponenteCurricular.objects.create(nome="Fora do semestre", periodo=self.periodo_outro)
        self.questao = self.nova_questao()
        self.url = reverse("admin:questoes_importar")
        self.client.force_login(self.usuario)

    def imagem_png(self):
        from io import BytesIO

        from PIL import Image

        buffer = BytesIO()
        Image.new("RGB", (4, 4), "blue").save(buffer, format="PNG")
        return buffer.getvalue()

    def nova_questao(self, **kwargs):
        questao = Questao.objects.create(
            componente=kwargs.pop("componente", self.doador),
            professor_nome=kwargs.pop("professor_nome", "Professor original"),
            professor_email=kwargs.pop("professor_email", "professor@example.test"),
            enunciado=kwargs.pop("enunciado", "Qual alternativa?"),
            texto_base="<p>Texto formatado</p>",
            justificativa="Justificativa pedagógica",
            status="APROVADA",
            **kwargs,
        )
        Alternativa.objects.bulk_create(
            [
                Alternativa(questao=questao, texto="Primeira", eh_correta=False),
                Alternativa(questao=questao, texto="Segunda", eh_correta=True),
            ]
        )
        return questao

    def importar(self, ids=None, **kwargs):
        dados = dict(
            usuario=self.usuario,
            semestre_id=self.semestre.pk,
            doador_id=self.doador.pk,
            receptor_id=self.receptor.pk,
            questao_ids=ids if ids is not None else [self.questao.pk],
        )
        dados.update(kwargs)
        return importar_questoes(**dados)

    def contexto(self, **kwargs):
        return dict(semestre=self.semestre.pk, doador=self.doador.pk, **kwargs)

    def pagina(self):
        return self.client.get(self.url, self.contexto())

    def postar(self, anterior, acao="confirmar", marcadas=None, **kwargs):
        dados = self.contexto(receptor=self.receptor.pk)
        dados.update(
            estado=anterior.context["estado"],
            acao=acao,
            marcadas=marcadas if marcadas is not None else [self.questao.pk],
        )
        dados.update(kwargs)
        return self.client.post(self.url, dados)


class ServicoImportacaoTests(DadosImportacao, TestCase):
    def test_questao_alternativas_gabarito_pendente_e_auditoria(self):
        resultado = self.importar()
        self.assertEqual((resultado.importadas, resultado.ignoradas), (1, 0))
        copia = Questao.objects.get(componente=self.receptor)
        self.assertNotEqual(copia.pk, self.questao.pk)
        for campo in ("texto_base", "enunciado", "justificativa"):
            self.assertEqual(getattr(copia, campo), getattr(self.questao, campo))
        self.assertEqual(copia.status, "PENDENTE")
        self.assertEqual(
            list(copia.alternativas.order_by("pk").values_list("texto", "eh_correta")),
            [("Primeira", False), ("Segunda", True)],
        )
        self.assertFalse(
            set(copia.alternativas.values_list("pk", flat=True))
            & set(self.questao.alternativas.values_list("pk", flat=True))
        )
        registro = ImportacaoQuestao.objects.get(copia=copia)
        self.assertEqual(registro.origem, self.questao)
        self.assertEqual(registro.responsavel, self.usuario)
        self.assertEqual(registro.semestre, self.semestre)
        self.assertIsNotNone(registro.criada_em)
        self.questao.refresh_from_db()
        self.assertEqual(self.questao.status, "APROVADA")

    def test_multiplas_e_enunciados_iguais_nao_sao_duplicatas(self):
        outra = self.nova_questao()
        self.assertEqual(self.importar([self.questao.pk, outra.pk]).importadas, 2)

    def test_reenvio_ignora_e_nao_recria_copia_excluida(self):
        self.importar()
        self.assertEqual(self.importar().ignoradas, 1)
        Questao.objects.filter(componente=self.receptor).delete()
        self.assertEqual(self.importar().ignoradas, 1)
        self.assertIsNone(ImportacaoQuestao.objects.get().copia)

    def test_edicao_e_exclusao_independentes(self):
        self.importar()
        copia = Questao.objects.get(componente=self.receptor)
        copia.enunciado = "Editada"
        copia.save()
        copia.alternativas.update(texto="Editada")
        self.questao.refresh_from_db()
        self.assertEqual(self.questao.enunciado, "Qual alternativa?")
        self.assertEqual(self.questao.alternativas.order_by("pk").first().texto, "Primeira")
        self.questao.delete()
        copia.refresh_from_db()
        self.assertEqual(copia.alternativas.count(), 2)
        self.assertIsNone(ImportacaoQuestao.objects.get().origem)
        copia.delete()
        self.assertEqual(ImportacaoQuestao.objects.count(), 1)

    def test_imagem_tem_arquivo_independente_e_sobrevive_exclusao_origem(self):
        self.questao.imagem.save("original.png", ContentFile(self.imagem_png()), save=True)
        self.importar()
        copia = Questao.objects.get(componente=self.receptor)
        self.assertNotEqual(copia.imagem.name, self.questao.imagem.name)
        self.assertEqual(copia.imagem.read(), self.questao.imagem.read())
        self.questao.imagem.delete()
        self.questao.delete()
        self.assertTrue(copia.imagem.storage.exists(copia.imagem.name))

    def test_excluir_arquivo_copia_preserva_original(self):
        self.questao.imagem.save("original.png", ContentFile(self.imagem_png()), save=True)
        self.importar()
        copia = Questao.objects.get(componente=self.receptor)
        copia.imagem.delete()
        copia.delete()
        self.assertTrue(self.questao.imagem.storage.exists(self.questao.imagem.name))

    def test_inativo(self):
        self.semestre.ativo = False
        self.semestre.save()
        with self.assertRaises(ValidationError):
            self.importar()

    def test_semestres_diferentes(self):
        outro_semestre = Semestre.objects.create(nome="2027.1", ativo=True)
        self.periodo_outro.semestre = outro_semestre
        self.periodo_outro.save()
        with self.assertRaises(ValidationError):
            self.importar(receptor_id=self.outro.pk)

    def test_proprio_componente(self):
        with self.assertRaises(ValidationError):
            self.importar(receptor_id=self.doador.pk)

    def test_ids_duplicados_vazios_invalidos_e_limite(self):
        for ids in ([], [self.questao.pk] * 2, ["x"], [True], [0], list(range(1, 502))):
            with self.subTest(ids=str(ids)[:40]), self.assertRaises(ValidationError):
                self.importar(ids)

    def test_questao_de_outro_componente_rejeita_lote_inteiro(self):
        outra = self.nova_questao(componente=self.outro)
        with self.assertRaises(ValidationError):
            self.importar([self.questao.pk, outra.pk])
        self.assertFalse(Questao.objects.filter(componente=self.receptor).exists())

    def test_permissao_no_servico(self):
        self.usuario.is_staff = False
        self.usuario.save()
        with self.assertRaises(PermissionDenied):
            self.importar()

    def test_rollback_banco_e_arquivos_em_falha_no_segundo_item(self):
        outra = self.nova_questao()
        self.questao.imagem.save("original.png", ContentFile(self.imagem_png()), save=True)
        salvar = ImportacaoQuestao.objects.create
        contador = 0

        def falhar(**kwargs):
            nonlocal contador
            contador += 1
            if contador == 2:
                raise IntegrityError("falha simulada")
            return salvar(**kwargs)

        with patch.object(ImportacaoQuestao.objects, "create", side_effect=falhar):
            with self.assertRaises(IntegrityError):
                self.importar([self.questao.pk, outra.pk])
        self.assertFalse(Questao.objects.filter(componente=self.receptor).exists())
        self.assertFalse(ImportacaoQuestao.objects.exists())
        self.assertEqual(
            [p for p in Path(self.tmp.name).rglob("*") if p.is_file()],
            [Path(self.questao.imagem.path)],
        )

    def test_arquivo_ausente_reverte_todo_lote(self):
        outra = self.nova_questao(imagem="inexistente.png")
        with self.assertRaises(OSError):
            self.importar([self.questao.pk, outra.pk])
        self.assertFalse(Questao.objects.filter(componente=self.receptor).exists())

    def test_restricao_unica_no_banco(self):
        self.importar()
        with self.assertRaises(IntegrityError):
            from django.db import transaction

            with transaction.atomic():
                ImportacaoQuestao.objects.create(
                    origem=self.questao,
                    origem_identificador=self.questao.pk,
                    componente_origem="Doador",
                    copia=None,
                    receptor=self.receptor,
                    semestre=self.semestre,
                    responsavel=self.usuario,
                )
        self.assertEqual(ImportacaoQuestao.objects.count(), 1)

    def test_consultas_crescem_linearmente_sem_n_mais_um_de_alternativas(self):
        ids = [self.questao.pk] + [self.nova_questao().pk for _ in range(9)]
        self.usuario.get_all_permissions()
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as consultas:
            self.importar(ids)
        selects_alternativas = [
            q["sql"]
            for q in consultas
            if q["sql"].startswith("SELECT") and 'FROM "questoes_alternativa"' in q["sql"]
        ]
        self.assertEqual(len(selects_alternativas), 1)


class InterfaceImportacaoTests(DadosImportacao, TestCase):
    def test_acesso_anonimo_e_staff_sem_permissoes(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
        usuario = get_user_model().objects.create_user("staff", password="teste", is_staff=True)
        self.client.force_login(usuario)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, {}).status_code, 403)

    def test_staff_com_permissoes_e_menu_proprio(self):
        usuario = get_user_model().objects.create_user("importador", is_staff=True)
        usuario.user_permissions.set(
            Permission.objects.filter(codename__in=["importar_questoes", "view_questao", "add_questao"])
        )
        self.client.force_login(usuario)
        self.assertContains(self.client.get(reverse("admin:index")), self.url)
        pagina = self.pagina()
        confirmacao = self.postar(pagina)
        self.assertContains(confirmacao, "Confirmar importação")
        self.assertEqual(self.postar(confirmacao, "importar", []).status_code, 200)
        self.assertEqual(ImportacaoQuestao.objects.get().responsavel, usuario)

    def test_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.usuario)
        self.assertEqual(client.post(self.url, self.contexto()).status_code, 403)

    def test_exibe_somente_semestres_ativos_e_receptores_validos(self):
        Semestre.objects.create(nome="2025.1", ativo=False)
        pagina = self.pagina()
        self.assertContains(pagina, "2026.2")
        self.assertNotContains(pagina, "2025.1")
        self.assertNotContains(pagina, "Fora do semestre")
        receptores = pagina.context["contexto_form"].fields["receptor"].queryset
        self.assertEqual(list(receptores), [self.receptor])

    def test_fluxo_confirmacao_e_reenvio(self):
        pagina = self.pagina()
        self.assertContains(pagina, "questoes/importacao.js")
        confirmacao = self.postar(pagina)
        self.assertContains(confirmacao, "Confirmar importação")
        self.assertEqual(Questao.objects.filter(componente=self.receptor).count(), 0)
        resultado = self.postar(confirmacao, "importar", [])
        self.assertContains(resultado, "1 importadas; 0 ignoradas (já importadas); 0 erros")
        reenvio = self.postar(confirmacao, "importar", [])
        self.assertContains(reenvio, "0 importadas; 1 ignoradas")

    def test_sem_confirmacao_e_troca_de_receptor_sao_rejeitados(self):
        pagina = self.pagina()
        self.assertEqual(self.postar(pagina, "importar").status_code, 400)
        confirmacao = self.postar(pagina)
        self.periodo_outro.semestre = self.semestre
        self.periodo_outro.save()
        self.assertEqual(self.postar(confirmacao, "importar", [], receptor=self.outro.pk).status_code, 400)

    def test_selecao_assinada_invalida_ou_de_outra_sessao(self):
        pagina = self.pagina()
        self.assertEqual(self.postar(pagina, estado="adulterado").status_code, 400)
        client = Client()
        client.force_login(self.usuario)
        self.assertEqual(
            client.post(
                self.url,
                dict(
                    self.contexto(receptor=self.receptor.pk),
                    estado=pagina.context["estado"],
                    acao="confirmar",
                    marcadas=[self.questao.pk],
                ),
            ).status_code,
            400,
        )

    def test_estado_expirado(self):
        with patch("django.core.signing.time.time", return_value=0):
            pagina = self.pagina()
        self.assertEqual(self.postar(pagina).status_code, 400)

    def test_ids_manipulados_inativos_e_semestre_trocado(self):
        pagina = self.pagina()
        outra = self.nova_questao(componente=self.outro)
        self.assertEqual(self.postar(pagina, marcadas=[outra.pk]).status_code, 400)
        self.assertEqual(
            self.postar(pagina, receptor=self.doador.pk).context["contexto_form"].is_valid(), False
        )
        outro_semestre = Semestre.objects.create(nome="2027.1", ativo=True)
        self.periodo.semestre = outro_semestre
        self.periodo.save()
        self.assertEqual(self.postar(pagina, semestre=outro_semestre.pk).status_code, 400)
        self.semestre.ativo = False
        self.semestre.save()
        self.assertFalse(self.postar(pagina).context["contexto_form"].is_valid())
        self.assertFalse(ImportacaoQuestao.objects.exists())

    def test_filtros_paginacao_e_selecao_conservada(self):
        outras = [self.nova_questao(enunciado=f"Questão extra {n}") for n in range(30)]
        pagina1 = self.pagina()
        self.assertEqual(len(pagina1.context["pagina_obj"]), 25)
        pagina2 = self.postar(pagina1, "pagina", pagina=2)
        self.assertEqual(pagina2.context["selecionadas"], {self.questao.pk})
        filtro = self.postar(pagina2, "filtrar", [outras[-1].pk], busca="Qual alternativa")
        self.assertEqual(filtro.context["selecionadas"], {self.questao.pk, outras[-1].pk})
        self.assertEqual(filtro.context["pagina_obj"].paginator.count, 1)
        confirmacao = self.postar(filtro, "confirmar", [])
        self.assertEqual(confirmacao.context["selecionadas"], {outras[-1].pk})
        self.postar(confirmacao, "importar", [])
        self.assertEqual(Questao.objects.filter(componente=self.receptor).count(), 1)

    def test_selecionar_todas_do_filtro_e_limpar_outras_paginas(self):
        for n in range(30):
            self.nova_questao(enunciado=f"Extra {n}")
        pagina = self.pagina()
        todas = self.postar(pagina, "selecionar_filtradas", [])
        self.assertEqual(len(todas.context["selecionadas"]), 31)
        limpa = self.postar(todas, "limpar_todas", [self.questao.pk])
        self.assertEqual(limpa.context["selecionadas"], set())
        filtradas = self.postar(limpa, "selecionar_filtradas", [], busca="Extra")
        self.assertEqual(len(filtradas.context["selecionadas"]), 30)

    def test_selecionar_todas_nao_ultrapassa_limite_e_rejeita_repetidas(self):
        Questao.objects.bulk_create(
            [Questao(componente=self.doador, enunciado=f"Extra {n}", justificativa="J") for n in range(500)]
        )
        pagina = self.pagina()
        excesso = self.postar(pagina, "selecionar_filtradas", [])
        self.assertEqual(excesso.context["selecionadas"], set())
        self.assertContains(excesso, "Refine o filtro")
        self.assertEqual(self.postar(pagina, marcadas=[self.questao.pk] * 2).status_code, 400)

    def test_erros_de_storage_sao_explicitos_sem_importacao_parcial(self):
        pagina = self.pagina()
        confirmacao = self.postar(pagina)
        with patch("questoes.importacao_admin.importar_questoes", side_effect=OSError("indisponível")):
            with self.assertLogs("questoes.importacao_admin", level="ERROR"):
                resposta = self.postar(confirmacao, "importar", [])
        self.assertEqual(resposta.status_code, 503)
        self.assertContains(resposta, "Nenhuma questão foi importada", status_code=503)

    def test_importacao_apos_questao_excluida_rejeita_tudo(self):
        pagina = self.pagina()
        confirmacao = self.postar(pagina)
        self.questao.delete()
        self.assertEqual(self.postar(confirmacao, "importar", []).status_code, 400)

    def test_html_nao_executa_enunciado(self):
        self.questao.enunciado = "<script>alert(1)</script>"
        self.questao.save()
        pagina = self.pagina()
        self.assertNotContains(pagina, "<script>alert(1)</script>")
        self.assertContains(pagina, "&lt;script&gt;")

    def test_admin_auditoria_somente_leitura(self):
        self.importar()
        registro = ImportacaoQuestao.objects.get()
        self.assertEqual(self.client.get(reverse("admin:questoes_importacaoquestao_add")).status_code, 403)
        self.assertEqual(
            self.client.post(
                reverse("admin:questoes_importacaoquestao_delete", args=[registro.pk])
            ).status_code,
            403,
        )
        self.assertContains(self.client.get(reverse("admin:questoes_importacaoquestao_changelist")), self.url)


class ConcorrenciaImportacaoTests(DadosImportacao, TransactionTestCase):
    def test_duas_operacoes_simultaneas_criam_uma_unica_copia(self):
        barreira = threading.Barrier(2)

        def executar():
            close_old_connections()
            try:
                usuario = get_user_model().objects.get(pk=self.usuario.pk)
                usuario.get_all_permissions()
                barreira.wait(timeout=15)
                return self.importar(usuario=usuario)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            resultados = list(executor.map(lambda _: executar(), range(2)))
        self.assertEqual(sorted((r.importadas, r.ignoradas) for r in resultados), [(0, 1), (1, 0)])
        self.assertEqual(Questao.objects.filter(componente=self.receptor).count(), 1)
        self.assertEqual(ImportacaoQuestao.objects.count(), 1)


class RegrasEnamedImportacaoTests(DadosImportacao, TestCase):
    def test_tipos_autoria_uso_e_proposicao_sao_preservados_sem_herdar_validacao(self):
        from django.core import mail

        for tipo, _ in Questao.TIPO_QUESTAO_CHOICES:
            questao = self.nova_questao(
                tipo_questao=tipo,
                uso_prova="REPOSICAO",
                proposicao_dois="Proposição II",
                comentario_validacao="Aprovação anterior do doador",
                notified_status="APROVADA",
            )
            self.importar([questao.pk])
            copia = ImportacaoQuestao.objects.get(origem=questao).copia
            for campo in (
                "professor_nome",
                "professor_email",
                "tipo_questao",
                "uso_prova",
                "proposicao_dois",
            ):
                self.assertEqual(getattr(copia, campo), getattr(questao, campo))
            self.assertIsNone(copia.comentario_validacao)
            self.assertEqual(copia.notified_status, "")
            self.assertEqual(copia.status, "PENDENTE")
            self.assertEqual(copia.importacao.comentario_origem, "Aprovação anterior do doador")
        self.assertEqual(len(mail.outbox), 0)

    def test_historicos_operacionais_e_recibos_de_submissao_nao_sao_clonados(self):
        import uuid

        from pre_auditoria.models import ParecerPreAuditoria

        from .models import SubmissionReceipt

        parecer = ParecerPreAuditoria.objects.create(
            questao=self.questao, resultado="APROVADA", fingerprint="f" * 64
        )
        recibo = SubmissionReceipt.objects.create(token=uuid.uuid4(), questao=self.questao)
        self.importar()
        copia = ImportacaoQuestao.objects.get(origem=self.questao).copia
        self.assertFalse(copia.pareceres_pre_auditoria.exists())
        self.assertFalse(SubmissionReceipt.objects.filter(questao=copia).exists())
        parecer.refresh_from_db()
        recibo.refresh_from_db()
        self.assertEqual(parecer.questao_id, self.questao.pk)
        self.assertEqual(recibo.questao_id, self.questao.pk)
        copia.delete()
        self.assertTrue(ParecerPreAuditoria.objects.filter(pk=parecer.pk).exists())

    def test_periodos_arquivados_e_aliases_nao_podem_ser_destinos_ou_doadores(self):
        periodo = Periodo.objects.create(nome="Arquivado", semestre=self.semestre, ativo=False)
        alias = ComponenteCurricular.objects.create(
            nome="Alias antigo", periodo=periodo, consolidado_em=self.doador
        )
        for dados in ({"doador_id": alias.pk}, {"receptor_id": alias.pk}):
            with self.assertRaises(ValidationError):
                self.importar(**dados)
        self.periodo.ativo = False
        self.periodo.save()
        with self.assertRaises(ValidationError):
            self.importar()
        self.assertFalse(ImportacaoQuestao.objects.exists())

    def test_banco_compartilhado_preserva_aliases_e_filtra_semestre_de_origem(self):
        periodo = Periodo.objects.create(nome="Turma antiga", semestre=self.semestre, ativo=False)
        alias = ComponenteCurricular.objects.create(
            nome="Alias antigo", periodo=periodo, consolidado_em=self.doador
        )
        compartilhada = self.nova_questao(componente=alias)
        self.assertContains(self.pagina(), compartilhada.enunciado)
        self.assertEqual(self.importar([compartilhada.pk]).importadas, 1)
        compartilhada.refresh_from_db()
        self.assertEqual(compartilhada.componente_id, alias.pk)
        alias_externo = ComponenteCurricular.objects.create(
            nome="Alias outro semestre", periodo=self.periodo_outro, consolidado_em=self.doador
        )
        externa = self.nova_questao(componente=alias_externo)
        pagina = self.pagina()
        componentes = pagina.context["contexto_form"].fields["doador"].queryset
        self.assertEqual(componentes.get(pk=self.doador.pk).total_questoes, 2)
        self.assertEqual(componentes.get(pk=self.receptor.pk).total_questoes, 1)
        with self.assertRaises(ValidationError):
            self.importar([externa.pk])

    def test_django_cleanup_exclui_apenas_arquivo_da_copia(self):
        self.questao.imagem.save("original.png", ContentFile(self.imagem_png()), save=True)
        self.importar()
        copia = Questao.objects.get(componente=self.receptor)
        nome_copia = copia.imagem.name
        with self.captureOnCommitCallbacks(execute=True):
            copia.delete()
        self.assertFalse(self.questao.imagem.storage.exists(nome_copia))
        self.assertTrue(self.questao.imagem.storage.exists(self.questao.imagem.name))

    def test_receptor_ausente_preserva_selecao_para_correcao(self):
        resposta = self.postar(self.pagina(), receptor="")
        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(resposta.context["selecionadas"], {self.questao.pk})
        self.assertIsNotNone(resposta.context["pagina_obj"])

    def test_falha_transitoria_preserva_confirmacao_e_reenvio_idempotente(self):
        confirmacao = self.postar(self.pagina())
        with patch("questoes.importacao_admin.importar_questoes", side_effect=OSError("Falha simulada")):
            with self.assertLogs("questoes.importacao_admin", level="ERROR"):
                falha = self.postar(confirmacao, "importar", [])
        self.assertEqual(falha.status_code, 503)
        self.assertTrue(falha.context["confirmacao"])
        self.assertEqual(falha.context["selecionadas"], {self.questao.pk})
        resposta = self.postar(falha, "importar", [])
        self.assertContains(resposta, "1 importadas; 0 ignoradas")
