from django.db import migrations, models


class Migration(migrations.Migration):
    """The corporate bot as a notification transport (SOF-2127): settings and outbox."""

    dependencies = [
        ("scg_overrides", "0002_task_details"),
    ]

    operations = [
        migrations.CreateModel(
            name="BotTransport",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "bot_url",
                    models.URLField(blank=True, max_length=300, verbose_name="Bot URL"),
                ),
                (
                    "token",
                    models.CharField(
                        blank=True, max_length=200, verbose_name="Shared token"
                    ),
                ),
                (
                    "site_url",
                    models.URLField(
                        blank=True, max_length=300, verbose_name="Horilla URL for links"
                    ),
                ),
                (
                    "exclude_patterns",
                    models.TextField(
                        blank=True,
                        help_text="Regular expressions matched against the text and the link, case-insensitive.",
                        verbose_name="Do not send (one pattern per line)",
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name="BotNotification",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("recipient_email", models.CharField(max_length=254)),
                ("text", models.CharField(max_length=500)),
                ("url", models.CharField(blank=True, max_length=500)),
                ("icon", models.CharField(blank=True, max_length=50)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("last_attempt_at", models.DateTimeField(blank=True, null=True)),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                ("result", models.CharField(blank=True, max_length=20)),
                ("last_error", models.CharField(blank=True, max_length=255)),
            ],
            options={"ordering": ["-id"]},
        ),
        migrations.AddIndex(
            model_name="botnotification",
            index=models.Index(
                fields=["sent_at", "attempts"], name="scg_botnotif_pending_idx"
            ),
        ),
    ]
