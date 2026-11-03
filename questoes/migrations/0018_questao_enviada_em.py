from django.db import migrations, models
from django.db.models import OuterRef, Subquery


def recuperar_datas_documentadas(apps, schema_editor):
    """Recupera somente datas dos recibos vinculados, sem estimar datas ausentes."""
    alias = schema_editor.connection.alias
    questions = apps.get_model("questoes", "Questao").objects.using(alias)
    receipts = apps.get_model("questoes", "SubmissionReceipt").objects.using(alias)
    imports = apps.get_model("questoes", "ImportacaoQuestao").objects.using(alias)
    questions.filter(pk__in=receipts.values("questao_id"), enviada_em__isnull=True).update(
        enviada_em=Subquery(receipts.filter(questao_id=OuterRef("pk")).values("created_at")[:1])
    )
    questions.filter(pk__in=imports.values("copia_id"), enviada_em__isnull=True).update(
        enviada_em=Subquery(imports.filter(copia_id=OuterRef("pk")).values("criada_em")[:1])
    )


class Migration(migrations.Migration):
    dependencies = [("questoes", "0017_alter_questao_comentario_validacao_and_more")]

    operations = [
        # Adiciona sem auto_now_add para não atribuir a data do deploy às questões antigas.
        migrations.AddField(
            model_name="questao",
            name="enviada_em",
            field=models.DateTimeField("Data de envio", null=True, blank=True, db_index=True, editable=False),
        ),
        migrations.RunPython(recuperar_datas_documentadas, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="questao",
            name="enviada_em",
            field=models.DateTimeField(
                "Data de envio", auto_now_add=True, null=True, blank=True, db_index=True
            ),
        ),
    ]
