from django.db import migrations


METAS_CLINICAS_CD = {
    "Clínica Integrada: Otorrinolaringologia": 5,
    "Clínica Integrada: Neurologia": 7,
    "Clínica Integrada: Ortopedia e Traumatologia": 7,
    "Clínica Integrada: Reumatologia": 5,
    "Clínica Integrada: Oftalmologia": 5,
}
MEDICINA_LEGAL = "Medicina Legal e Deontologia"


def atualizar_grade(apps, schema_editor):
    componentes = apps.get_model("questoes", "ComponenteCurricular").objects.using(
        schema_editor.connection.alias
    )
    componentes = componentes.filter(periodo__semestre__nome="2026.2", consolidado_em__isnull=True)
    compartilhados = componentes.filter(periodo__nome="8º Período")
    for nome, meta in METAS_CLINICAS_CD.items():
        compartilhados.filter(nome=f"{nome} (turmas C e D)").update(numero_questoes_prova=meta)

    nome_antigo = f"{MEDICINA_LEGAL} (turmas A e B)"
    nome_atual = f"{MEDICINA_LEGAL} (turmas A, B, C e D)"
    if compartilhados.filter(nome=nome_antigo).exists():
        if compartilhados.filter(nome__in=(nome_antigo, nome_atual)).count() != 1:
            raise RuntimeError("Há cadastros duplicados de Medicina Legal no 8º período de 2026.2.")
        compartilhados.filter(nome=nome_antigo).update(nome=nome_atual, numero_questoes_prova=6)
    else:
        compartilhados.filter(nome=nome_atual).update(numero_questoes_prova=6)

    # Instalações ainda não consolidadas usam os blocos ativos de turmas.
    periodos_cd = apps.get_model("questoes", "Periodo").objects.using(schema_editor.connection.alias)
    periodo_cd = periodos_cd.filter(
        semestre__nome="2026.2", nome="8º Período (turmas C e D)", ativo=True
    ).first()
    if periodo_cd is not None:
        for nome, meta in METAS_CLINICAS_CD.items():
            componentes.filter(periodo=periodo_cd, nome=nome).update(numero_questoes_prova=meta)
        componentes.update_or_create(
            periodo=periodo_cd, nome=MEDICINA_LEGAL, defaults={"numero_questoes_prova": 5}
        )


class Migration(migrations.Migration):
    dependencies = [("questoes", "0020_questaoexcluida_alter_questao_options_and_more")]
    operations = [migrations.RunPython(atualizar_grade, migrations.RunPython.noop)]
