from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("questoes", "0011_submissionreceipt")]

    operations = [
        migrations.AlterField(
            model_name="submissionreceipt",
            name="questao",
            field=models.OneToOneField(
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="submission_receipt",
                to="questoes.questao",
            ),
        ),
        migrations.AddField(
            model_name="questao",
            name="notified_status",
            field=models.CharField(blank=True, default="", editable=False, max_length=20),
        ),
    ]
