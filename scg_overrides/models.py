"""SCG's own tables."""

from django.db import models
from django.utils.translation import gettext_lazy as _


class TaskDetails(models.Model):
    """What Horilla's task models have no room for (SOF-2140, SOF-2190).

    A task is "read the vacation policy", "return the laptop": it needs the
    document it is about, a line of explanation, and - for the newcomer's own
    tasks - who actually does it. Horilla's OnboardingTask and OffboardingTask
    carry a title and managers only; tables of our own leave the product's
    migrations alone.
    """

    url = models.URLField(max_length=500, blank=True, verbose_name=_("Document link"))
    description = models.TextField(blank=True, verbose_name=_("Description"))
    for_subject = models.BooleanField(
        default=False,
        verbose_name=_("Done by the employee"),
        help_text=_("The newcomer or the leaver does it, not the task managers."),
    )

    class Meta:
        abstract = True

    def __str__(self):
        return self.url or self.description[:50]


class OnboardingTaskDetails(TaskDetails):
    task = models.OneToOneField(
        "onboarding.OnboardingTask",
        on_delete=models.CASCADE,
        related_name="scg_details",
    )


class OffboardingTaskDetails(TaskDetails):
    task = models.OneToOneField(
        "offboarding.OffboardingTask",
        on_delete=models.CASCADE,
        related_name="scg_details",
    )


class BotTransport(models.Model):
    """How Horilla reaches the corporate bot to deliver notifications in Chat (SOF-2127).

    One row; ``current()`` creates it empty. Enabling the transport is the
    ``corporate_bot`` switch in Settings → Integrations, like WhatsApp.
    """

    bot_url = models.URLField(max_length=300, blank=True, verbose_name=_("Bot URL"))
    token = models.CharField(max_length=200, blank=True, verbose_name=_("Shared token"))
    site_url = models.URLField(
        max_length=300, blank=True, verbose_name=_("Horilla URL for links")
    )
    exclude_patterns = models.TextField(
        blank=True,
        verbose_name=_("Do not send (one pattern per line)"),
        help_text=_(
            "Regular expressions matched against the text and the link, case-insensitive."
        ),
    )
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def current(cls):
        return cls.objects.first() or cls.objects.create()

    def configured(self):
        return bool(self.bot_url and self.token)

    def __str__(self):
        return self.bot_url or "corporate bot (not configured)"


class BotNotification(models.Model):
    """Outbox of a notification to deliver through the bot; the bot's answer is kept."""

    recipient_email = models.CharField(max_length=254)
    text = models.CharField(max_length=500)
    url = models.CharField(max_length=500, blank=True)
    icon = models.CharField(max_length=50, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    result = models.CharField(
        max_length=20, blank=True
    )  # delivered / no_dm / unknown_user / error
    last_error = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-id"]
        indexes = [
            models.Index(
                fields=["sent_at", "attempts"], name="scg_botnotif_pending_idx"
            )
        ]

    def __str__(self):
        return f"{self.recipient_email}: {self.text[:40]}"
