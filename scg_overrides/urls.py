"""URLs for the SCG-specific additions."""

from django.urls import path

from scg_overrides import onboarding_blocks

urlpatterns = [
    path(
        "onboarding-task-blocks/",
        onboarding_blocks.task_blocks_view,
        name="scg-onboarding-task-blocks",
    ),
    path(
        "onboarding-task-blocks/task/<int:pk>/delete/",
        onboarding_blocks.delete_block_task,
        name="scg-onboarding-task-block-delete",
    ),
]
