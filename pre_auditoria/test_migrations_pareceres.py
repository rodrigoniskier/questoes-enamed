from django.core import mail
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class PreservacaoHistoricaTests(TransactionTestCase):
    def test_migrations_preserve_questions_old_decisions_and_do_not_notify(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        before = [
            ("questoes", "0015_importacaoquestao"),
            ("pre_auditoria", "0009_decisaopreauditoria_aprovacao_excepcional_and_more"),
        ]
        try:
            executor.migrate(before)
            apps = executor.loader.project_state(before).apps
            semester = apps.get_model("questoes", "Semestre").objects.create(nome="2026.1")
            period = apps.get_model("questoes", "Periodo").objects.create(
                nome="2º Período", semestre=semester
            )
            component = apps.get_model("questoes", "ComponenteCurricular").objects.create(
                nome="Histórico sintético", periodo=period
            )
            questions = []
            for status in ("PENDENTE", "APROVADA", "REPROVADA"):
                question = apps.get_model("questoes", "Questao").objects.create(
                    componente=component,
                    professor_nome="Docente sintético",
                    professor_email="docente@example.com",
                    enunciado="Enunciado histórico",
                    justificativa="Justificativa histórica",
                    status=status,
                    comentario_validacao="Parecer legado integral.",
                    notified_status=status,
                )
                questions.append((question.pk, status))
            report = apps.get_model("pre_auditoria", "ParecerPreAuditoria").objects.create(
                questao_id=questions[0][0], resultado="INCONCLUSIVA", fingerprint="historico"
            )
            decision = apps.get_model("pre_auditoria", "DecisaoPreAuditoria").objects.create(
                questao_id=questions[0][0],
                parecer=report,
                acao="CORRECAO",
                fingerprint="historico",
                justificativa="Orientações anteriores.",
            )
            executor = MigrationExecutor(connection)
            executor.migrate(latest)
            from questoes.models import Questao

            from .models import DecisaoPreAuditoria

            for pk, status in questions:
                question = Questao.objects.get(pk=pk)
                self.assertEqual(question.status, status)
                self.assertEqual(question.notified_status, status)
                self.assertEqual(question.comentario_validacao, "Parecer legado integral.")
            preserved = DecisaoPreAuditoria.objects.get(pk=decision.pk)
            self.assertEqual(preserved.acao, "CORRECAO")
            self.assertEqual(preserved.justificativa, "Orientações anteriores.")
            self.assertEqual(preserved.notificacao_estado, "LEGADO")
            self.assertEqual(len(mail.outbox), 0)
        finally:
            MigrationExecutor(connection).migrate(latest)
