"""Offboarding that runs by itself (SOF-2175).

Stock Horilla leaves two gaps in the exit pipeline:

* Adding an employee to an offboarding creates no tasks. A task reaches a
  leaver only when HR clicks its cell for that person, or ticks them in "Task
  To" while creating the task - so the checklist is exactly as reliable as
  HR's memory, which is what the pipeline was meant to replace. Here a leaver
  gets every task of the process when added, and a task added later goes to
  everyone still in it. The rows are created in bulk: EmployeeTask.save()
  notifies the leaver once per task ("task assigned"), and the leaver is not
  the one who does them - the people who do get the tasks in Google Chat
  through the corporate bot (scg_overrides.bot_tasks).

* The "archived" stage does not always archive. Both stage-change views set
  is_active=False and call Employee.save(), which inside a request quietly
  re-activates anyone who still manages something - reports, onboarding or
  recruitment stages. 18 of the 59 active employees in November 2026. The
  leaver stays active with a working login and the pipeline says "archived".
  Here the stage itself archives, past save() - as a dismissal pushed by the
  bot does (bot_sync) - and HR is told what the leaver still manages so it can
  be handed over. The views run after the stage is saved and find nothing
  left to archive.
"""

from django.contrib import messages
from django.db.models.signals import post_save
from django.utils.translation import gettext as _


def current_user():
    from horilla.horilla_middlewares import _thread_locals

    request = getattr(_thread_locals, "request", None)
    user = getattr(request, "user", None)
    return request, (user if user is not None and user.is_authenticated else None)


def assign(leavers, tasks):
    """Give each leaver each task; ones they already have are skipped."""
    from offboarding.models import EmployeeTask

    _request, user = current_user()
    rows = [
        EmployeeTask(
            employee_id=leaver, task_id=task, created_by=user, modified_by=user
        )
        for leaver in leavers
        for task in tasks
    ]
    EmployeeTask.objects.bulk_create(rows, ignore_conflicts=True)


def archive(employee):
    """Archive the leaver and close their login, whatever they still manage."""
    from employee.models import Employee

    if not Employee._base_manager.filter(pk=employee.pk, is_active=True).exists():
        return
    Employee._base_manager.filter(pk=employee.pk).update(is_active=False)
    employee.is_active = False
    employee.sync_login_access()

    request, _user = current_user()
    condition = employee.get_archive_condition()
    if request is not None and condition:
        messages.warning(
            request,
            _(
                "%(employee)s is archived but still listed as: %(roles)s. Hand these over."
            )
            % {
                "employee": employee.get_full_name(),
                "roles": ", ".join(
                    str(item["verbose_name"]) for item in condition["related_models"]
                ),
            },
        )


def on_leaver_saved(sender, instance, created, **kwargs):
    from offboarding.models import OffboardingTask

    if kwargs.get("raw") or instance.stage_id is None:
        return
    if created:
        assign(
            [instance],
            OffboardingTask.objects.filter(
                stage_id__offboarding_id=instance.stage_id.offboarding_id
            ),
        )
    if instance.stage_id.type == "archived":
        archive(instance.employee_id)


def on_task_saved(sender, instance, created, **kwargs):
    from offboarding.models import OffboardingEmployee

    if kwargs.get("raw") or not created or instance.stage_id is None:
        return
    assign(
        OffboardingEmployee.objects.filter(
            stage_id__offboarding_id=instance.stage_id.offboarding_id
        ).exclude(stage_id__type="archived"),
        [instance],
    )


def install():
    from offboarding.models import OffboardingEmployee, OffboardingTask

    post_save.connect(
        on_leaver_saved,
        sender=OffboardingEmployee,
        dispatch_uid="scg-offboarding-leaver",
    )
    post_save.connect(
        on_task_saved, sender=OffboardingTask, dispatch_uid="scg-offboarding-task"
    )
