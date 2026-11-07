"""URLs for the SCG-specific additions."""

from django.urls import path

from scg_overrides import (
    bot_employees,
    bot_sync,
    bot_tasks,
    direct_onboarding,
    onboarding_blocks,
)

urlpatterns = [
    path("bot-sync/", bot_sync.bot_sync, name="scg-bot-sync"),
    path("bot-tasks/", bot_tasks.bot_tasks, name="scg-bot-tasks"),
    path("bot-employees/", bot_employees.bot_employees, name="scg-bot-employees"),
    path(
        "start-onboarding/<int:emp_id>/",
        direct_onboarding.start_onboarding,
        name="scg-start-onboarding",
    ),
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
