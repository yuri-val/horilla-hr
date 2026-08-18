"""URLs for the SCG-specific additions."""

from django.urls import path

from scg_overrides import onboarding_blocks

urlpatterns = [
    path(
        "onboarding-task-blocks/",
        onboarding_blocks.task_blocks_view,
        name="scg-onboarding-task-blocks",
    ),
]
