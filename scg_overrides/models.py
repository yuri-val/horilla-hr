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
