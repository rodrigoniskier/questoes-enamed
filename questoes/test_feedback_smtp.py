"""Transporte SMTP real contra receptor local; nenhum destinatário externo."""

import queue
import re
import socketserver
import threading
import uuid
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse

from pre_auditoria.models import DecisaoPreAuditoria
from pre_auditoria.services import executar_pre_auditoria, fingerprint_questao

from .models import Alternativa, ComponenteCurricular, Periodo, Questao, Semestre


class ReceptorSMTP(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(5)

        def reply(text):
            self.wfile.write(text.encode("ascii") + b"\r\n")
            self.wfile.flush()

        reply("220 local.test ESMTP")
        while line := self.rfile.readline():
            command = line.decode("ascii", errors="replace").strip().upper()
            if command.startswith("EHLO"):
                reply("250-local.test\r\n250 SIZE 1000000")
            elif command.startswith(("HELO", "MAIL", "RCPT", "RSET")):
                reply("250 OK")
            elif command == "DATA":
                if self.server.reject_message:
                    reply("451 Temporary local test failure")
                    continue
                reply("354 End data with <CRLF>.<CRLF>")
                data = bytearray()
                while row := self.rfile.readline():
                    if row == b".\r\n":
                        break
                    data.extend(row[1:] if row.startswith(b"..") else row)
                self.server.messages.put(bytes(data))
                reply("250 Message accepted")
            elif command == "QUIT":
                reply("221 Bye")
                return
            else:
                reply("502 Unsupported command")


class TextoVisivel(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ignored = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.ignored += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.ignored -= 1

    def handle_data(self, data):
        if not self.ignored:
            self.parts.append(data)


class FeedbackSMTPTests(TestCase):
    def setUp(self):
        semester = Semestre.objects.create(nome="2026.2")
        period = Periodo.objects.create(nome="2º Período", semestre=semester)
        self.component = ComponenteCurricular.objects.create(nome="Clínica sintética", periodo=period)
        self.question = Questao.objects.create(
            componente=self.component,
            professor_nome="Docente sintético",
            professor_email="docente@example.com",
            texto_base="Contexto sintético.",
            enunciado="Qual a alternativa adequada?",
            justificativa="Justificativa sintética.",
        )
        for index in range(4):
            Alternativa.objects.create(questao=self.question, texto=f"Opção {index}", eh_correta=index == 0)
        self.user = get_user_model().objects.create_superuser("auditor-smtp", "auditor@example.com", "teste")
        self.client.force_login(self.user)
        executar_pre_auditoria(self.question, revisor=self.user, semantica=False)
        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), ReceptorSMTP)
        self.server.daemon_threads = True
        self.server.messages = queue.Queue()
        self.server.reject_message = False
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        config = override_settings(
            EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
            EMAIL_HOST="127.0.0.1",
            EMAIL_PORT=self.server.server_address[1],
            EMAIL_USE_TLS=False,
            EMAIL_USE_SSL=False,
            EMAIL_HOST_USER="",
            EMAIL_HOST_PASSWORD="",
            EMAIL_TIMEOUT=5,
            DEFAULT_FROM_EMAIL="naped@example.com",
        )
        config.enable()
        self.addCleanup(config.disable)
        self.note = (
            "Reprovada: revisar a correspondência entre gabarito e justificativa.\n\n"
            + "Orientação. " * 500
            + '<script>alert("teste")</script>'
        )
        self.data = {
            "classificacao": "REPROVADA",
            "parecer": self.note,
            "confirmar": "on",
            "request_id": str(uuid.uuid4()),
            "fingerprint": fingerprint_questao(self.question),
        }

    def submit(self, admin=False):
        name = "admin:questoes_questao_parecer" if admin else "pre_auditoria:decidir"
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(reverse(name, args=[self.question.pk]), self.data)

    def assert_new_wording(self, text):
        self.assertIsNone(re.search(r"\breprovad[ao]s?\b", text, re.IGNORECASE))
        self.assertIn("Precisa de Revisões", text)

    def test_post_commit_reaches_smtp_with_full_multipart_feedback_and_no_duplicate(self):
        self.assertEqual(self.submit().status_code, 302)
        self.assertEqual(self.submit().status_code, 302)
        message = BytesParser(policy=policy.default).parsebytes(self.server.messages.get_nowait())
        self.assertTrue(self.server.messages.empty())
        self.assertEqual(message["To"], self.question.professor_email)
        self.assertEqual(message["From"], "naped@example.com")
        self.assert_new_wording(str(message["Subject"]))
        plain = message.get_body(preferencelist=("plain",)).get_content()
        html = message.get_body(preferencelist=("html",)).get_content()
        for part in (plain, html):
            self.assert_new_wording(part)
            self.assertIn("Orientação. " * 500, part)
        self.assertIn("/app/pre-auditoria/correcao/", plain)
        self.assertNotIn("<script>", html)
        decision = DecisaoPreAuditoria.objects.get()
        self.assertEqual(decision.notificacao_estado, "ENVIADO")
        self.assertIsNotNone(decision.email_enviado_em)
        # A apresentação não reescreve o documento histórico original.
        self.assertEqual(decision.justificativa, self.note)

    def test_admin_uses_same_automatic_smtp_feedback(self):
        self.assertEqual(self.submit(admin=True).status_code, 302)
        message = BytesParser(policy=policy.default).parsebytes(self.server.messages.get_nowait())
        self.assert_new_wording(message.get_body(preferencelist=("plain",)).get_content())
        self.assertEqual(DecisaoPreAuditoria.objects.get().notificacao_estado, "ENVIADO")

    def test_smtp_failure_is_visible_and_explicit_retry_sends_once(self):
        self.server.reject_message = True
        self.assertEqual(self.submit().status_code, 302)
        self.assertEqual(DecisaoPreAuditoria.objects.get().notificacao_estado, "FALHOU")
        self.assertTrue(self.server.messages.empty())
        self.server.reject_message = False
        self.submit()
        self.submit()
        self.assertEqual(self.server.messages.qsize(), 1)
        self.assertEqual(DecisaoPreAuditoria.objects.count(), 1)
        self.assertEqual(DecisaoPreAuditoria.objects.get().notificacao_estado, "ENVIADO")

    def test_all_user_facing_status_and_history_pages_use_new_wording(self):
        self.submit()
        decision = DecisaoPreAuditoria.objects.get()
        pages = [
            reverse("admin:questoes_questao_changelist"),
            reverse("admin:questoes_questao_change", args=[self.question.pk]),
            reverse("admin:questoes_questao_parecer", args=[self.question.pk]),
            reverse("admin:pre_auditoria_decisaopreauditoria_change", args=[decision.pk]),
            reverse("pre_auditoria:lista") + "?status=REPROVADA",
            reverse("pre_auditoria:detalhe", args=[self.question.pk]),
            reverse("pre_auditoria:decidir", args=[self.question.pk]),
            reverse("submeter_visualizar_questoes") + f"?componente={self.component.pk}",
        ]
        for url in pages:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                parser = TextoVisivel()
                parser.feed(response.content.decode())
                self.assert_new_wording(" ".join(parser.parts))
        response = self.client.get(reverse("api_get_historico"), {"componente_id": self.component.pk})
        self.assertEqual(response.json()["questoes"][0]["status_descricao"], "Precisa de Revisões")
        self.assertEqual(response.json()["questoes"][0]["status"], "REPROVADA")


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
    EMAIL_HOST="smtp.example.com",
    EMAIL_HOST_USER="private@example.com",
    EMAIL_HOST_PASSWORD="private-password",
    DEFAULT_FROM_EMAIL="private@example.com",
    EMAIL_USE_TLS=True,
    EMAIL_USE_SSL=False,
)
class DiagnosticoFeedbackTests(TestCase):
    def test_checks_config_without_opening_connection_or_printing_credentials(self):
        output = StringIO()
        with patch("questoes.management.commands.verificar_email_feedback.get_connection") as connection:
            call_command("verificar_email_feedback", stdout=output)
        connection.assert_not_called()
        self.assertIn("configurado", output.getvalue())
        self.assertNotIn("private", output.getvalue())

    def test_connect_authenticates_without_sending(self):
        output = StringIO()
        with patch("questoes.management.commands.verificar_email_feedback.get_connection") as connection:
            call_command("verificar_email_feedback", conectar=True, stdout=output)
        connection.return_value.__enter__.assert_called_once()
        connection.return_value.send_messages.assert_not_called()
        self.assertIn("autenticação SMTP: OK", output.getvalue())

    @override_settings(EMAIL_HOST_PASSWORD="")
    def test_missing_credentials_are_reported(self):
        with self.assertRaisesMessage(CommandError, "EMAIL_HOST_PASSWORD"):
            call_command("verificar_email_feedback")

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend")
    def test_non_delivery_backend_is_not_reported_as_working(self):
        with self.assertRaisesMessage(CommandError, "não realiza envio SMTP"):
            call_command("verificar_email_feedback")

    def test_connection_error_is_not_reported_as_success_or_exposed(self):
        output = StringIO()
        with patch(
            "questoes.management.commands.verificar_email_feedback.get_connection",
            side_effect=RuntimeError("private-password"),
        ):
            with self.assertRaises(CommandError) as error:
                call_command("verificar_email_feedback", conectar=True, stdout=output)
        self.assertNotIn("private-password", str(error.exception))
        self.assertNotIn("autenticação SMTP: OK", output.getvalue())
