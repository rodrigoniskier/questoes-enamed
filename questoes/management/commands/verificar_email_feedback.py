"""Diagnóstico do feedback SMTP, sem emitir mensagens ou alterar decisões."""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import get_connection
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email


class Command(BaseCommand):
    help = "Confere o feedback por SMTP. --conectar testa conexão/autenticação sem enviar e-mail."

    def add_arguments(self, parser):
        parser.add_argument("--conectar", action="store_true")

    def handle(self, *args, **options):
        if settings.EMAIL_BACKEND != "django.core.mail.backends.smtp.EmailBackend":
            raise CommandError("O backend ativo não realiza envio SMTP. Confira EMAIL_BACKEND.")
        missing = [
            name
            for name in ("EMAIL_HOST", "EMAIL_HOST_USER", "EMAIL_HOST_PASSWORD", "DEFAULT_FROM_EMAIL")
            if not getattr(settings, name, "")
        ]
        if missing:
            raise CommandError("Configurações ausentes: " + ", ".join(missing))
        try:
            validate_email(settings.DEFAULT_FROM_EMAIL)
        except ValidationError as error:
            raise CommandError("DEFAULT_FROM_EMAIL não contém um endereço válido.") from error
        if settings.EMAIL_USE_TLS and settings.EMAIL_USE_SSL:
            raise CommandError("EMAIL_USE_TLS e EMAIL_USE_SSL não podem estar ativos simultaneamente.")
        self.stdout.write("Feedback automático por SMTP: configurado.")
        self.stdout.write("Remetente e credenciais: presentes (valores não exibidos).")
        if options["conectar"]:
            try:
                with get_connection(fail_silently=False):
                    pass
            except Exception as error:
                raise CommandError(
                    "Falha na conexão/autenticação SMTP: "
                    + type(error).__name__
                    + ". Confira a configuração privada e as permissões do provedor."
                ) from error
            self.stdout.write(self.style.SUCCESS("Conexão e autenticação SMTP: OK."))
        else:
            self.stdout.write("Conexão não testada. Use --conectar para verificar autenticação.")
        self.stdout.write("Nenhuma mensagem foi enviada; nenhum registro acadêmico foi alterado.")
