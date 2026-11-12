from contextlib import closing
from io import StringIO
from pathlib import Path
from sqlite3 import connect
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from ops.verify_integrity import compare
from questoes.catalog import grade_compartilhada
from questoes.models import ComponenteCurricular, Periodo, Questao, Semestre


class EighthPeriodMigrationTests(TransactionTestCase):
    def test_grade_updates_existing_banks_in_place_and_passes_deploy_verification(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        before = [("questoes", "0020_questaoexcluida_alter_questao_options_and_more")]
        try:
            executor.migrate(before)
            call_command("configurar_semestre_2026_2", stdout=StringIO())
            semestre = Semestre.objects.get(nome="2026.2")
            periodo = semestre.periodos.get(nome="8º Período")
            legal = periodo.componentes.get(nome__startswith="Medicina Legal")
            legal.nome = "Medicina Legal e Deontologia (turmas A e B)"
            legal.save(update_fields=["nome"])
            for componente in periodo.componentes.filter(nome__endswith="(turmas C e D)"):
                componente.numero_questoes_prova += 1
                componente.save(update_fields=["numero_questoes_prova"])
            questao = Questao.objects.create(componente=legal, enunciado="Questão preservada")
            ids = set(periodo.componentes.values_list("pk", flat=True))
            antigo = Semestre.objects.create(nome="2026.1", ativo=False)
            antigo_cd = Periodo.objects.create(nome="8º Período (turmas C e D)", semestre=antigo)
            antigo_neuro = antigo_cd.componentes.create(nome="Clínica Integrada: Neurologia")
            cd = Periodo.objects.create(nome="8º Período (turmas C e D)", semestre=semestre)
            neuro = cd.componentes.create(nome="Clínica Integrada: Neurologia", numero_questoes_prova=8)

            with TemporaryDirectory() as directory:
                backup = Path(directory) / "before.sqlite3"
                current = Path(directory) / "after.sqlite3"
                with closing(connect(backup)) as db:
                    connection.connection.backup(db)
                MigrationExecutor(connection).migrate(latest)
                with closing(connect(current)) as db:
                    connection.connection.backup(db)
                evidence = compare(backup, current)
                self.assertTrue(evidence["questoes_questao"]["existing_preserved"])
                self.assertEqual(evidence["questoes_componentecurricular"]["grade_components_updated"], 7)
                with closing(connect(current)) as db, db:
                    db.execute(
                        "UPDATE questoes_componentecurricular SET numero_questoes_prova = 8 WHERE id = ?",
                        (antigo_neuro.pk,),
                    )
                with self.assertRaisesRegex(AssertionError, "questoes_componentecurricular"):
                    compare(backup, current)

            legal.refresh_from_db()
            self.assertEqual(legal.nome, "Medicina Legal e Deontologia (turmas A, B, C e D)")
            self.assertEqual(legal.numero_questoes_prova, 6)
            self.assertEqual(Questao.objects.get(pk=questao.pk).componente_id, legal.pk)
            self.assertEqual(set(periodo.componentes.values_list("pk", flat=True)), ids)
            self.assertEqual(cd.componentes.get(nome="Medicina Legal e Deontologia").numero_questoes_prova, 5)
            neuro.refresh_from_db()
            self.assertEqual(neuro.numero_questoes_prova, 7)
            self.assertEqual(
                dict(periodo.componentes.values_list("nome", "numero_questoes_prova")),
                {
                    item["nome"]: item["meta"]
                    for group in grade_compartilhada()[1]
                    if group["nome"] == "8º Período"
                    for item in group["componentes"].values()
                },
            )
            call_command("configurar_semestre_2026_2", stdout=StringIO())
            self.assertEqual(set(periodo.componentes.values_list("pk", flat=True)), ids)
            self.assertEqual(
                ComponenteCurricular.objects.get(
                    periodo=cd, nome="Medicina Legal e Deontologia"
                ).consolidado_em_id,
                legal.pk,
            )
        finally:
            MigrationExecutor(connection).migrate(latest)
