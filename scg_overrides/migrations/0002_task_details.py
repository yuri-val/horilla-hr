import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """The onboarding task link grows into task details, and offboarding gets the same (SOF-2190)."""

    dependencies = [
        ("scg_overrides", "0001_initial"),
        ("offboarding", "0006_alter_offboardingtask_is_required"),
    ]

    operations = [
        migrations.RenameModel("OnboardingTaskLink", "OnboardingTaskDetails"),
        migrations.AlterField(
            model_name="onboardingtaskdetails",
            name="task",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="scg_details",
                to="onboarding.onboardingtask",
            ),
        ),
        migrations.AlterField(
            model_name="onboardingtaskdetails",
            name="url",
            field=models.URLField(blank=True, max_length=500, verbose_name="Document link"),
        ),
        migrations.AddField(
            model_name="onboardingtaskdetails",
            name="description",
            field=models.TextField(blank=True, verbose_name="Description"),
        ),
        migrations.AddField(
            model_name="onboardingtaskdetails",
            name="for_subject",
            field=models.BooleanField(
                default=False,
                help_text="The newcomer or the leaver does it, not the task managers.",
                verbose_name="Done by the employee",
            ),
        ),
        migrations.CreateModel(
            name="OffboardingTaskDetails",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("url", models.URLField(blank=True, max_length=500, verbose_name="Document link")),
                ("description", models.TextField(blank=True, verbose_name="Description")),
                (
                    "for_subject",
                    models.BooleanField(
                        default=False,
                        help_text="The newcomer or the leaver does it, not the task managers.",
                        verbose_name="Done by the employee",
                    ),
                ),
                (
                    "task",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="scg_details",
                        to="offboarding.offboardingtask",
                    ),
                ),
            ],
            options={"abstract": False},
        ),
    ]
