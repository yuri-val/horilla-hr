"""SCG's own tables."""

from django.db import models
from django.utils.translation import gettext_lazy as _


class OnboardingTaskLink(models.Model):
    """The document an onboarding task points to (SOF-2140).

    Policies live on the company portal and Drive, not in Horilla, so a task
    carries a link instead of a copy. Horilla's OnboardingTask has nowhere to
    keep one; a table of our own leaves the product's migrations alone.
    """

    task = models.OneToOneField(
        "onboarding.OnboardingTask", on_delete=models.CASCADE, related_name="scg_link"
    )
    url = models.URLField(max_length=500, verbose_name=_("Document link"))

    def __str__(self):
        return self.url
