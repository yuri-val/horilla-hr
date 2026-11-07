"""Onboarding and offboarding tasks for the corporate bot (SOF-2190).

Only HR opens Horilla; IT, the office, accounting and the newcomer never do.
So the tasks Horilla hands out - a CandidateTask per newcomer and onboarding
task, an EmployeeTask per leaver and offboarding task - are delivered by the
corporate bot as Google Chat cards with status buttons. The bot pulls them
from here every few minutes (Horilla never calls out) and pushes a clicked
status back through bot-sync ("task_statuses").

Each task is sent with what the card shows and who gets it: the task
managers, or the newcomer / leaver for a task marked "done by the employee".
Statuses are normalized to the bot's three - todo, in_progress, done - with
"stuck" kept as a flag; "closed" means the employee's process is over (the
offboarding reached its archived stage, the onboarding its final stage), so
an open task stops being re-sent.

    GET /scg/bot-tasks/   X-Bot-Token: <SCG_BOT_SYNC_TOKEN>
    {"tasks": [{"key": "onboarding:12", "kind": "onboarding", "title": "...",
                "description": "...", "url": "...", "status": "todo",
                "stuck": false, "closed": false, "subject": "Olha Koval",
                "process": "...", "assigned_by": "Oksana Saienko",
                "assigned_on": "2026-11-09", "assignees": ["it@..."]}]}
"""

from django.http import JsonResponse
from django.views.decorators.http import require_GET

from scg_overrides.task_details import details_of

ONBOARDING = {
    "todo": "todo",
    "scheduled": "todo",
    "ongoing": "in_progress",
    "stuck": "in_progress",
    "done": "done",
}
OFFBOARDING = {
    "todo": "todo",
    "in_progress": "in_progress",
    "stuck": "in_progress",
    "completed": "done",
}
# The bot's status -> Horilla's, per kind
BACK = {
    "onboarding": {"todo": "todo", "in_progress": "ongoing", "done": "done"},
    "offboarding": {"todo": "todo", "in_progress": "in_progress", "done": "completed"},
}


@require_GET
def bot_tasks(request):
    from scg_overrides.bot_sync import authorized

    if not authorized(request):
        return JsonResponse({"error": "forbidden"}, status=403)
    return JsonResponse({"tasks": [*onboarding_tasks(), *offboarding_tasks()]})


def name_of(user):
    employee = getattr(user, "employee_get", None) if user else None
    return employee.get_full_name() if employee else ""


def row(
    key,
    kind,
    title,
    details,
    status,
    statuses,
    subject,
    process,
    created_by,
    created_at,
    managers,
    closed,
):
    assignees = (
        [subject.email]
        if details["for_subject"] and subject and subject.email
        else [m.email for m in managers if m.email]
    )
    return {
        "key": key,
        "kind": kind,
        "title": title,
        "description": details["description"],
        "url": details["url"],
        "status": statuses.get(status or "todo", "todo"),
        "stuck": status == "stuck",
        "closed": closed,
        "subject": subject.get_full_name() if subject else "",
        "process": process,
        "assigned_by": name_of(created_by),
        "assigned_on": created_at.date().isoformat() if created_at else None,
        "assignees": sorted(set(assignees)),
    }


def onboarding_tasks():
    from onboarding.models import CandidateTask

    rows = (
        CandidateTask._base_manager.filter(is_active=True)
        .select_related(
            "candidate_id__recruitment_id",
            "candidate_id__converted_employee_id",
            "onboarding_task_id",
            "created_by",
        )
        .prefetch_related("onboarding_task_id__employee_id")
    )
    for task in rows:
        candidate = task.candidate_id
        stage = getattr(candidate, "onboarding_stage", None)
        closed = bool(
            stage
            and stage.onboarding_stage_id
            and stage.onboarding_stage_id.is_final_stage
        )
        subject = candidate.converted_employee_id
        yield row(
            f"onboarding:{task.pk}",
            "onboarding",
            task.onboarding_task_id.task_title,
            details_of(task.onboarding_task_id),
            task.status,
            ONBOARDING,
            subject,
            candidate.recruitment_id.title,
            task.created_by,
            task.created_at,
            task.onboarding_task_id.employee_id.all(),
            closed,
        )


def offboarding_tasks():
    from offboarding.models import EmployeeTask

    rows = (
        EmployeeTask._base_manager.filter(is_active=True, employee_id__isnull=False)
        .select_related(
            "employee_id__employee_id",
            "employee_id__stage_id__offboarding_id",
            "task_id",
            "created_by",
        )
        .prefetch_related("task_id__managers")
    )
    for task in rows:
        leaver = task.employee_id
        stage = leaver.stage_id
        yield row(
            f"offboarding:{task.pk}",
            "offboarding",
            task.task_id.title,
            details_of(task.task_id),
            task.status,
            OFFBOARDING,
            leaver.employee_id,
            stage.offboarding_id.title if stage else "",
            task.created_by,
            task.created_at,
            task.task_id.managers.all(),
            bool(stage and stage.type == "archived"),
        )


def apply_task_status(item):
    """Write a status clicked in the bot: {"key", "status", "by"}."""
    from django.contrib.auth import get_user_model

    from employee.models import Employee
    from offboarding.models import EmployeeTask
    from onboarding.models import CandidateTask

    result = {"key": item.get("key")}
    try:
        kind, pk = item["key"].split(":")
        model = {"onboarding": CandidateTask, "offboarding": EmployeeTask}[kind]
        status = BACK[kind][item["status"]]
    except (KeyError, ValueError, AttributeError):
        return {**result, "result": "error", "error": "bad key or status"}
    task = model._base_manager.filter(pk=pk).first()
    if task is None:
        return {**result, "result": "unknown_task"}
    if task.status == status:
        return {**result, "result": "unchanged"}
    by = Employee._base_manager.filter(
        email__iexact=(item.get("by") or "").strip()
    ).first()
    user = by.employee_user_id if by else None
    # Past save(): EmployeeTask.save() tells the leaver "task assigned" on every
    # write, and both need a logged-in request user the bot does not have
    model._base_manager.filter(pk=pk).update(
        status=status, modified_by=user if isinstance(user, get_user_model()) else None
    )
    return {**result, "result": "updated"}
