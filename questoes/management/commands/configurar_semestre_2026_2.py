"""Grade do edital 2026.2; arquiva 2026.1 sem alterar questões históricas."""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from questoes.models import ComponenteCurricular, Periodo, Semestre


class Command(BaseCommand):
    help = "Cadastra o edital 2026.2 e arquiva 2026.1, preservando seus registros."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Simula e desfaz todas as alterações.")

    def handle(self, *args, **options):
        path = Path(__file__).resolve().parents[2] / "data" / "grade_2026_2.json"
        grade = json.loads(path.read_text(encoding="utf-8"))
        periodos = grade["periodos"]
        nomes = {p["nome"] for p in periodos}
        if len(nomes) != 9 or sum(len(p["componentes"]) for p in periodos) != 62:
            raise CommandError("A grade deve conter os nove grupos e 62 componentes do edital.")
        for periodo in periodos:
            componentes = periodo["componentes"]
            if (
                len({c["nome"] for c in componentes}) != len(componentes)
                or any(
                    type(c["numero_questoes_prova"]) is not int or c["numero_questoes_prova"] <= 0
                    for c in componentes
                )
                or sum(c["numero_questoes_prova"] for c in componentes) != 50
            ):
                raise CommandError(
                    f"Grade inválida em {periodo['nome']}: a prova deve totalizar 50 questões."
                )

        with transaction.atomic():
            semestre, _ = Semestre.objects.get_or_create(nome=grade["semestre"], defaults={"ativo": False})
            if semestre.periodos.exclude(nome__in=nomes).exists():
                raise CommandError("2026.2 contém períodos fora do edital. Confira antes de ativar a grade.")
            for item in periodos:
                periodo, _ = Periodo.objects.get_or_create(semestre=semestre, nome=item["nome"])
                nomes_componentes = {c["nome"] for c in item["componentes"]}
                if periodo.componentes.exclude(nome__in=nomes_componentes).exists():
                    raise CommandError(f"Há componentes fora do edital em {periodo.nome}.")
                for componente in item["componentes"]:
                    if periodo.componentes.filter(nome=componente["nome"]).count() > 1:
                        raise CommandError(f"Componente duplicado em {periodo.nome}: {componente['nome']}.")
                    ComponenteCurricular.objects.update_or_create(
                        periodo=periodo,
                        nome=componente["nome"],
                        defaults={"numero_questoes_prova": componente["numero_questoes_prova"]},
                    )
            # Only the requested semester is archived; historical FKs remain intact.
            Semestre.objects.filter(nome="2026.1").update(ativo=False)
            semestre.ativo = True
            semestre.save(update_fields=["ativo"])
            if options["dry_run"]:
                transaction.set_rollback(True)

        prefix = "SIMULAÇÃO (nenhuma alteração salva)" if options["dry_run"] else "CONCLUÍDO"
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}: 2026.1 arquivado; 2026.2 com 9 grupos, 62 componentes e 50 questões por prova."
            )
        )
