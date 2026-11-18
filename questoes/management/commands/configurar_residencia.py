"""Garante o Período Residência Médica e áreas no semestre ativo."""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from questoes.models import ComponenteCurricular, Periodo, Semestre

AREAS_RESIDENCIA = (
    ("Medicina Social e Preventiva - RM", 10),
    ("Obstetrícia e Ginecologia - RM", 10),
    ("Pediatria - RM", 10),
    ("Clínica Médica - RM", 10),
    ("Cirurgia Geral - RM", 10),
)


class Command(BaseCommand):
    help = "Ativa o período Residência Médica e cria cinco componentes de 10 questões (50 na prova) se não houver componentes."

    def add_arguments(self, parser):
        parser.add_argument("--semestre", help="Nome do semestre de destino; por padrão, o ativo mais recente.")
        parser.add_argument("--dry-run", action="store_true", help="Simula as alterações sem salvar.")

    def handle(self, *args, **options):
        semestre_nome = options["semestre"]
        semestres = Semestre.objects.all()
        if semestre_nome:
            semestres = semestres.filter(nome=semestre_nome)
        else:
            semestres = semestres.filter(ativo=True)
        semestre = semestres.order_by("-nome", "-pk").first()
        if semestre is None:
            raise CommandError("Nenhum semestre ativo encontrado. Ative um semestre no Admin primeiro.")
        if not semestre.ativo:
            raise CommandError("O semestre informado está arquivado. Ative-o no Admin primeiro.")

        with transaction.atomic():
            existentes = [
                periodo
                for periodo in Periodo.objects.filter(semestre=semestre)
                if periodo.nome.strip().casefold() == "residência médica"
            ]
            if len(existentes) > 1:
                raise CommandError("Há mais de um período de Residência Médica no semestre.")
            if existentes:
                periodo = existentes[0]
                if not periodo.ativo:
                    periodo.ativo = True
                    periodo.save(update_fields=["ativo"])
            else:
                periodo = Periodo.objects.create(
                    nome="RESIDÊNCIA MÉDICA", semestre=semestre, ativo=True
                )

            # Não altera bancos de questões nem substitui componentes existentes.
            criadas = 0
            if not ComponenteCurricular.objects.filter(periodo=periodo).exists():
                for nome, quantidade in AREAS_RESIDENCIA:
                    ComponenteCurricular.objects.create(
                        periodo=periodo,
                        nome=nome,
                        numero_questoes_prova=quantidade,
                    )
                    criadas += 1
            total = ComponenteCurricular.objects.filter(
                periodo=periodo, consolidado_em__isnull=True
            ).count()
            if options["dry_run"]:
                transaction.set_rollback(True)

        prefixo = "SIMULAÇÃO" if options["dry_run"] else "CONCLUÍDO"
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefixo}: Residência Médica ativa em {semestre.nome}; "
                f"{total} componentes disponíveis ({criadas} criados)."
            )
        )
