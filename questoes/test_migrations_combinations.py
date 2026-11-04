from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class CombinationMigrationTests(TransactionTestCase):
    def test_existing_questions_and_statements_are_preserved_with_automatic_mode(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        before = [("questoes", "0018_questao_enviada_em")]
        try:
            executor.migrate(before)
            apps = executor.loader.project_state(before).apps
            semester = apps.get_model("questoes", "Semestre").objects.create(nome="2026.2")
            period = apps.get_model("questoes", "Periodo").objects.create(nome="4º", semestre=semester)
            component = apps.get_model("questoes", "ComponenteCurricular").objects.create(
                nome="Clínica", periodo=period
            )
            Question = apps.get_model("questoes", "Questao")
            Alternative = apps.get_model("questoes", "Alternativa")
            question = Question.objects.create(
                componente=component,
                tipo_questao="MULTIPLA_ESCOLHA",
                enunciado="Comando original",
                justificativa="Justificativa",
                status="APROVADA",
            )
            for i in range(3):
                Alternative.objects.create(questao=question, texto=f"Afirmativa {i + 1}", eh_correta=i < 2)
            saved = Question.objects.values().get(pk=question.pk)
            statements = list(Alternative.objects.values())
            MigrationExecutor(connection).migrate(latest)
            from .models import Alternativa, Questao

            self.assertEqual(Questao.objects.values(*saved).get(pk=question.pk), saved)
            self.assertEqual(Questao.objects.get(pk=question.pk).combinacoes_personalizadas, [])
            self.assertEqual(list(Alternativa.objects.values()), statements)
        finally:
            MigrationExecutor(connection).migrate(latest)
