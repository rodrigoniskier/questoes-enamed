"""Grade compartilhada 2026.2; preserva questões, recibos e cadastros anteriores."""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from questoes.catalog import grade_compartilhada
from questoes.models import ComponenteCurricular, Periodo, Semestre


class Command(BaseCommand):
    help = "Consolida as turmas de 2026.2, mantém a maior meta e arquiva 2026.1."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Simula e desfaz todas as alterações.")

    def handle(self, *args, **options):
        try:
            grade, grupos = grade_compartilhada()
        except (ValueError, KeyError, TypeError) as error:
            raise CommandError(f"Grade inválida: {error}") from error
        nomes_originais = {p["nome"] for p in grade["periodos"]}
        nomes_atuais = {g["nome"] for g in grupos}
        movidas = 0

        with transaction.atomic():
            semestre, _ = Semestre.objects.get_or_create(nome="2026.2", defaults={"ativo": False})
            if semestre.periodos.exclude(nome__in=nomes_originais | nomes_atuais).exists():
                raise CommandError("2026.2 contém períodos fora do edital. Confira antes de consolidar.")
            # Stop on unexpected content; never discard an unrecognized component.
            permitidos = {p["nome"]: {c["nome"] for c in p["componentes"]} for p in grade["periodos"]}
            permitidos.update({g["nome"]: {c["nome"] for c in g["componentes"].values()} for g in grupos})
            for periodo in semestre.periodos.all():
                if periodo.componentes.exclude(nome__in=permitidos[periodo.nome]).exists():
                    raise CommandError(f"Há componentes fora do edital em {periodo.nome}.")

            for grupo in grupos:
                periodo, _ = Periodo.objects.get_or_create(
                    semestre=semestre, nome=grupo["nome"], defaults={"ativo": True}
                )
                for item in grupo["componentes"].values():
                    atuais = list(periodo.componentes.filter(nome=item["nome"]))
                    if len(atuais) > 1:
                        raise CommandError(f"Cadastro compartilhado duplicado: {item['nome']}.")
                    anteriores = list(
                        ComponenteCurricular.objects.filter(
                            periodo__semestre=semestre,
                            periodo__nome__in=item["blocos"],
                            nome=item["base"],
                        ).order_by("pk")
                    )
                    canonico = (
                        atuais[0]
                        if atuais
                        else next((c for c in anteriores if c.consolidado_em_id is None), None)
                    )
                    if canonico is None:
                        canonico, _ = ComponenteCurricular.objects.update_or_create(
                            periodo=periodo,
                            nome=item["nome"],
                            defaults={"numero_questoes_prova": item["meta"]},
                        )
                    if canonico.consolidado_em_id:
                        raise CommandError("O destino compartilhado não pode apontar para outro cadastro.")
                    canonico.periodo = periodo
                    canonico.nome = item["nome"]
                    canonico.numero_questoes_prova = item["meta"]
                    canonico.save(update_fields=["periodo", "nome", "numero_questoes_prova"])
                    for anterior in anteriores:
                        if anterior.pk == canonico.pk:
                            continue
                        if anterior.consolidado_em_id not in (None, canonico.pk):
                            raise CommandError(f"Destino incompatível para {anterior.nome}.")
                        # Preserve question IDs, alternatives, images, receipts and status.
                        movidas += anterior.questoes.update(componente=canonico)
                        anterior.consolidado_em = canonico
                        anterior.save(update_fields=["consolidado_em"])
                periodo.ativo = True
                periodo.save(update_fields=["ativo"])

            # Soft archive previous blocks; retain their IDs as recoverable aliases.
            semestre.periodos.filter(nome__in=nomes_originais).update(ativo=False)
            Semestre.objects.filter(nome="2026.1").update(ativo=False)
            semestre.ativo = True
            semestre.save(update_fields=["ativo"])
            if options["dry_run"]:
                transaction.set_rollback(True)

        prefix = "SIMULAÇÃO (nenhuma alteração salva)" if options["dry_run"] else "CONCLUÍDO"
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}: 2026.1 arquivado; 2026.2 com 5 períodos e 50 componentes compartilhados; "
                f"{movidas} questões vinculadas ao banco compartilhado."
            )
        )
