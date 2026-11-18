"""Cadastro inicial da Residência Médica no semestre letivo ativo.

Os componentes representam uma prova-base de 100 questões objetivas: cinco
áreas com meta de 20 cada, com quatro alternativas por questão. A migração
preserva os bancos já cadastrados, sem renomear nem alterar questões existentes.
"""

from django.db import migrations


AREAS_RESIDENCIA = (
    ("Clínica Médica", 20),
    ("Cirurgia Geral", 20),
    ("Ginecologia e Obstetrícia", 20),
    ("Pediatria", 20),
    ("Medicina de Família e Comunidade / Saúde Coletiva", 20),
)


def cadastrar_residencia(apps, schema_editor):
    alias = schema_editor.connection.alias
    Semestre = apps.get_model("questoes", "Semestre")
    Periodo = apps.get_model("questoes", "Periodo")
    Componente = apps.get_model("questoes", "ComponenteCurricular")

    semestre = Semestre.objects.using(alias).filter(ativo=True).order_by("-nome", "-pk").first()
    if semestre is None:
        return

    existentes = [
        periodo
        for periodo in Periodo.objects.using(alias).filter(semestre_id=semestre.pk)
        if periodo.nome.strip().casefold() == "residência médica"
    ]
    if len(existentes) > 1:
        raise RuntimeError("Há mais de um período de Residência Médica neste semestre.")
    if existentes:
        periodo = existentes[0]
        if not periodo.ativo:
            periodo.ativo = True
            periodo.save(update_fields=["ativo"], using=alias)
    else:
        periodo = Periodo.objects.using(alias).create(
            semestre_id=semestre.pk, nome="Residência Médica", ativo=True
        )

    # Se a coordenação já definiu componentes próprios, mantém exatamente esses.
    if Componente.objects.using(alias).filter(periodo_id=periodo.pk).exists():
        return
    for nome, quantidade in AREAS_RESIDENCIA:
        Componente.objects.using(alias).create(
            periodo_id=periodo.pk,
            nome=nome,
            numero_questoes_prova=quantidade,
        )


class Migration(migrations.Migration):
    dependencies = [("questoes", "0022_prova_gerada")]

    operations = [
        migrations.RunPython(cadastrar_residencia, migrations.RunPython.noop),
    ]
