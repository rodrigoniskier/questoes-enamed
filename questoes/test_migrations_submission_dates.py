import uuid
from datetime import datetime, timezone

from django.core import mail
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class SubmissionDateMigrationTests(TransactionTestCase):
    def test_only_documented_dates_are_recovered_and_existing_records_are_preserved(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        before = [("questoes", "0017_alter_questao_comentario_validacao_and_more")]
        try:
            executor.migrate(before)
            apps = executor.loader.project_state(before).apps
            semester = apps.get_model("questoes", "Semestre").objects.create(nome="2026.2")
            period = apps.get_model("questoes", "Periodo").objects.create(nome="2º", semestre=semester)
            component = apps.get_model("questoes", "ComponenteCurricular").objects.create(
                nome="Componente sintético", periodo=period
            )
            Question = apps.get_model("questoes", "Questao")
            Receipt = apps.get_model("questoes", "SubmissionReceipt")
            Import = apps.get_model("questoes", "ImportacaoQuestao")
            user = apps.get_model("auth", "User").objects.create(username="importador")
            questions = [
                Question.objects.create(
                    componente=component,
                    enunciado=f"Questão histórica {index}",
                    justificativa="Texto preservado",
                    status="APROVADA",
                    comentario_validacao="Parecer preservado",
                    notified_status="APROVADA",
                )
                for index in range(3)
            ]
            saved = list(Question.objects.values())
            submitted = datetime(2026, 10, 10, 14, 20, tzinfo=timezone.utc)
            imported = datetime(2026, 10, 20, 10, 30, tzinfo=timezone.utc)
            receipt = Receipt.objects.create(token=uuid.uuid4(), questao=questions[0])
            Receipt.objects.filter(pk=receipt.pk).update(created_at=submitted)
            Receipt.objects.create(token=uuid.uuid4(), questao=None)
            record = Import.objects.create(
                origem=questions[0],
                origem_identificador=questions[0].pk,
                componente_origem=component.nome,
                copia=questions[1],
                receptor=component,
                semestre=semester,
                responsavel=user,
            )
            Import.objects.filter(pk=record.pk).update(criada_em=imported)

            MigrationExecutor(connection).migrate(latest)
            from .models import Questao

            self.assertEqual(Questao.objects.get(pk=questions[0].pk).enviada_em, submitted)
            self.assertEqual(Questao.objects.get(pk=questions[1].pk).enviada_em, imported)
            self.assertIsNone(Questao.objects.get(pk=questions[2].pk).enviada_em)
            for original in saved:
                self.assertEqual(Questao.objects.values(*original).get(pk=original["id"]), original)
            self.assertEqual(len(mail.outbox), 0)
        finally:
            MigrationExecutor(connection).migrate(latest)
