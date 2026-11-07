"""Employees, leaves and balances pushed by the SCG corporate bot (SOF-2138, SOF-2189).

The bot is where employees request leave and managers approve it; Horilla
only shows the result. Horilla's own leave API cannot mirror that: a request
it creates is always "requested", approving it re-checks and spends Horilla's
balance, an approved request can no longer be edited and only the employee can
cancel one. So the bot calls this endpoint instead, and leaves are written the
way the original seed wrote them - approved directly, through the model's
save(), which keeps the attendance work records and leave clashes in step.

A leave is matched by the "[bot <kind> #<id>]" marker at the start of its
description (the seed's convention, carried by every leave loaded so far), so
pushing the same record twice changes nothing. A record that is not approved
in the bot is only ever applied to a leave already here - a pending or denied
request never shows up in Horilla.

Balances are the bot's ledger: "Annual Leave" holds the days the employee can
still plan, its carry-forward the part left from last year. Horilla accrues
nothing itself (no leave type has reset on, and no scheduler runs).

Employees are entered by HR in the bot, which is their source of truth
(SOF-2189). One is found by the Horilla id the bot keeps, else by e-mail - so
someone already hired through Horilla's recruitment is linked, not duplicated -
and created when neither matches. The bot always owns the name, e-mail, phone,
birthday, hire date, job position and whether the employee is still employed;
an empty value in the bot never erases one Horilla has. Department and
reporting manager are Horilla's org chart, which HR refines (team leads, a
department head kept under Headquarter): the bot sets them only on creation and
when the employee moves to another department in the bot ("reassign"). A
dismissal archives the employee even where Horilla's own archive action would
refuse (a manager of others): the person has left either way.

    PUT /scg/bot-sync/   X-Bot-Token: <SCG_BOT_SYNC_TOKEN>
    {"leaves": [{"kind": "vacation", "bot_id": 1, "email": "...",
                 "reason": "planned_vacation", "start": "2026-11-10",
                 "end": "2026-11-12", "day_part": "full_day",
                 "status": "allowed", "note": "..."}],
     "balances": [{"email": "...", "available": 12.5, "carryforward": 3}],
     "employees": [{"bot_id": 7, "horilla_id": 64, "email": "...",
                    "first_name": "...", "last_name": "...", "phone": "+380...",
                    "birthday": "1990-05-10", "hire_date": "2026-11-09",
                    "position": "QA Engineer",
                    "department": "IT Development & Infrastructure",
                    "manager_email": "...", "active": true, "reassign": false}]}

Employees are applied first, so leaves in the same batch already find them.

"task_statuses" carries a status clicked on a task card in Google Chat
(SOF-2190, scg_overrides.bot_tasks): [{"key": "onboarding:12", "status":
"done", "by": "<e-mail>"}].
"""

import hmac
import json
import os
from datetime import date

from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from base.models import Company, Department, JobPosition
from employee.models import Employee, EmployeeWorkInformation
from leave.models import AvailableLeave, LeaveRequest, LeaveType
from scg_overrides.bot_tasks import apply_task_status

# Bot leave reason -> Horilla leave type (the types the seed created)
LEAVE_TYPES = {
    "planned_vacation": "Annual Leave",
    "personal": "Day Off",
    "other_reason": "Personal Leave",
    "birthday": "Birthday Day Off",
    "sick_leave": "Sick Leave",
}
BIRTHDAY_TYPE = "Birthday Day Off"
BALANCE_TYPE = "Annual Leave"

STATUSES = {
    "pending": "requested",
    "allowed": "approved",
    "denied": "rejected",
    "cancelled": "cancelled",
}
HALF_DAYS = {"am": "first_half", "pm": "second_half"}


def marker(kind, bot_id):
    return f"[bot {kind} #{bot_id}]"


def authorized(request):
    """The request carries the bot's token (SCG_BOT_SYNC_TOKEN)."""
    token = os.environ.get("SCG_BOT_SYNC_TOKEN", "")
    given = request.headers.get("X-Bot-Token", "")
    return bool(token) and hmac.compare_digest(given.encode(), token.encode())


@csrf_exempt
@require_http_methods(["PUT"])
def bot_sync(request):
    if not authorized(request):
        return JsonResponse({"error": "forbidden"}, status=403)

    try:
        payload = json.loads(request.body)
    except ValueError:
        return JsonResponse({"error": "invalid json"}, status=400)

    synced = [apply_employee(row) for row in payload.get("employees", [])]
    employees = {
        e.email.strip().lower(): e
        for e in Employee._base_manager.exclude(email__isnull=True)
        if e.email
    }
    return JsonResponse(
        {
            "employees": synced,
            "leaves": [
                apply_leave(row, employees) for row in payload.get("leaves", [])
            ],
            "balances": [
                apply_balance(row, employees) for row in payload.get("balances", [])
            ],
            "task_statuses": [
                apply_task_status(row) for row in payload.get("task_statuses", [])
            ],
        }
    )


def apply_leave(row, employees):
    result = {"kind": row.get("kind"), "bot_id": row.get("bot_id")}
    try:
        with transaction.atomic():
            result.update(write_leave(row, employees))
    except Exception as exc:  # one bad row must not lose the rest of the batch
        result.update(result="error", error=repr(exc)[:300])
    return result


def write_leave(row, employees):
    tag = marker(row["kind"], row["bot_id"])
    leave = (
        LeaveRequest._base_manager.filter(description__startswith=tag)
        .order_by("id")
        .first()
    )
    status = STATUSES[row["status"]]
    if leave is None and status != "approved":
        return {"result": "skipped"}

    employee = employees.get((row.get("email") or "").strip().lower())
    if leave is None and employee is None:
        return {"result": "unknown_employee"}

    if "start" in row:
        start = date.fromisoformat(row["start"])
        end = date.fromisoformat(row["end"])
        breakdown = (
            HALF_DAYS.get(row.get("day_part"), "full_day")
            if start == end
            else "full_day"
        )
        note = (row.get("note") or "").strip()
        values = {
            "employee_id": employee or leave.employee_id,
            "leave_type_id": leave_type(LEAVE_TYPES[row["reason"]]),
            "start_date": start,
            "end_date": end,
            "start_date_breakdown": breakdown,
            "end_date_breakdown": breakdown,
            "status": status,
            "description": f"{tag} | {note}" if note else tag,
        }
    else:
        # The record is gone from the bot: only its status is sent
        values = {"status": status}

    if leave is None:
        leave = LeaveRequest(requested_date=start, **values)
        leave.save()
        return {"result": "created", "id": leave.id}

    changed = {k: v for k, v in values.items() if getattr(leave, k) != v}
    if not changed:
        return {"result": "unchanged", "id": leave.id}
    for key, value in changed.items():
        setattr(leave, key, value)
    leave.save()
    return {"result": "updated", "id": leave.id}


def leave_type(name):
    found = LeaveType._base_manager.filter(name=name).first()
    if found:
        return found
    if name != BIRTHDAY_TYPE:
        raise LeaveType.DoesNotExist(f"leave type {name!r} is not set up")

    # The birthday day-off (SOF-2114) arrived after the seed: the first one
    # creates its type, configured like "Day Off" - paid, no approval in
    # Horilla, no reset (Horilla accrues nothing), one day a year
    birthday = LeaveType._base_manager.get(name="Day Off")
    birthday.pk = None
    birthday.id = None
    birthday.name = BIRTHDAY_TYPE
    birthday.count = 1
    birthday.total_days = 1
    birthday.reset = False
    birthday.save()
    return birthday


def apply_balance(row, employees):
    result = {"email": row.get("email")}
    employee = employees.get((row.get("email") or "").strip().lower())
    if employee is None:
        return {**result, "result": "unknown_employee"}
    try:
        with transaction.atomic():
            balance = AvailableLeave._base_manager.filter(
                employee_id=employee, leave_type_id__name=BALANCE_TYPE
            ).first() or AvailableLeave(
                employee_id=employee, leave_type_id=leave_type(BALANCE_TYPE)
            )
            available = round(float(row["available"]), 2)
            carryforward = round(max(float(row["carryforward"]), 0), 2)
            if (
                balance.pk
                and balance.available_days == available
                and balance.carryforward_days == carryforward
            ):
                return {**result, "result": "unchanged"}
            balance.available_days = available
            balance.carryforward_days = carryforward
            balance.save()
        return {**result, "result": "updated"}
    except Exception as exc:
        return {**result, "result": "error", "error": repr(exc)[:300]}


# Copied from the manager's (or a colleague's) work record to a new employee
WORK_DEFAULTS = (
    "company_id",
    "shift_id",
    "work_type_id",
    "employee_type_id",
    "location",
)


def apply_employee(row):
    result = {"bot_id": row.get("bot_id")}
    try:
        with transaction.atomic():
            result.update(write_employee(row))
    except Exception as exc:  # one bad row must not lose the rest of the batch
        result.update(result="error", error=repr(exc)[:300])
    return result


def write_employee(row):
    employee = find_employee(row)
    active = bool(row.get("active", True))
    department = Department._base_manager.filter(
        department=row.get("department")
    ).first()
    created = employee is None
    if created:
        # Nobody to link to: a dismissed or out-of-scope employee stays out
        if not active or department is None:
            return {"result": "skipped"}
        employee = Employee(gender=None)
    linked = not created and row.get("horilla_id") != employee.pk

    values = {
        "employee_first_name": row["first_name"],
        "employee_last_name": row.get("last_name") or None,
    }
    # An e-mail that differs only in letter case is the same address: leave
    # it, and the login named after it, as they are
    email = row["email"].strip()
    if (employee.email or "").lower() != email.lower():
        values["email"] = email
    if row.get("phone"):
        values["phone"] = row["phone"]
    if row.get("birthday"):
        values["dob"] = date.fromisoformat(row["birthday"])
    old_email = employee.email
    changed = assign(employee, values)
    if created or changed:
        employee.save()
    if not created and "email" in changed:
        rename_login(employee.employee_user_id, old_email, employee.email)

    work = EmployeeWorkInformation._base_manager.get_or_create(employee_id=employee)[0]
    work_values = {}
    if row.get("hire_date"):
        work_values["date_joining"] = date.fromisoformat(row["hire_date"])
    # The direct manager is the bot's to set at any time (SOF-2120); the
    # department only when the employee is created or moved there
    manager = (
        Employee._base_manager.filter(
            email__iexact=(row.get("manager_email") or "").strip()
        )
        .exclude(pk=employee.pk)
        .first()
        if row.get("manager_email")
        else None
    )
    if manager or (created and department):
        work_values["reporting_manager_id"] = manager
    if department and (created or row.get("reassign")):
        work_values["department_id"] = department
        if created:
            template = work_template(department, manager)
            for field in WORK_DEFAULTS:
                work_values[field] = getattr(template, field) if template else None
            work_values["company_id"] = (
                work_values["company_id"]
                or Company._base_manager.order_by("id").first()
            )
    position_department = work_values.get("department_id", work.department_id)
    if row.get("position") and position_department:
        work_values["job_position_id"] = job_position(
            row["position"],
            position_department,
            work_values.get("company_id", work.company_id),
        )
    work_changed = assign(work, work_values)
    if work_changed:
        work.save()

    # Employee.save() inside a request re-activates an archived employee who
    # still manages something, so the status is written past it
    status_changed = (
        Employee._base_manager.filter(pk=employee.pk)
        .values_list("is_active", flat=True)
        .get()
        != active
    )
    if status_changed:
        Employee._base_manager.filter(pk=employee.pk).update(is_active=active)
        employee.is_active = active
        employee.sync_login_access()

    if created:
        outcome = "created"
    elif linked:
        outcome = "linked"
    else:
        outcome = (
            "updated" if changed or work_changed or status_changed else "unchanged"
        )
    return {"result": outcome, "id": employee.pk}


def find_employee(row):
    if row.get("horilla_id"):
        found = Employee._base_manager.filter(pk=row["horilla_id"]).first()
        if found:
            return found
    email = (row.get("email") or "").strip()
    return Employee._base_manager.filter(email__iexact=email).first() if email else None


def assign(instance, values):
    """Set what differs; the names of the fields that changed."""
    changed = [
        field for field, value in values.items() if getattr(instance, field) != value
    ]
    for field in changed:
        setattr(instance, field, values[field])
    return changed


def rename_login(user, old_email, new_email):
    """The login follows the e-mail it was created from, when that name is free."""
    if user is None:
        return
    user.email = new_email
    if (
        user.username == old_email
        and not get_user_model().objects.filter(username=new_email).exists()
    ):
        user.username = new_email
    user.save()


def work_template(department, manager):
    """Whose shift, work type and company a new employee starts with."""
    records = EmployeeWorkInformation._base_manager.filter(employee_id__is_active=True)
    if manager:
        found = records.filter(employee_id=manager).first()
        if found:
            return found
    return records.filter(department_id=department).first() or records.first()


def job_position(name, department, company):
    """The department's job position of this name, created when it has none."""
    name = " ".join(name.split())
    found = JobPosition._base_manager.filter(
        department_id=department, job_position__iexact=name
    ).first()
    if found:
        return found
    position = JobPosition(job_position=name, department_id=department)
    position.save()
    if company:
        position.company_id.add(company)
    return position
