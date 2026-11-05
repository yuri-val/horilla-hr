"""Leaves and balances pushed by the SCG corporate bot (SOF-2138).

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

    PUT /scg/bot-sync/   X-Bot-Token: <SCG_BOT_SYNC_TOKEN>
    {"leaves": [{"kind": "vacation", "bot_id": 1, "email": "...",
                 "reason": "planned_vacation", "start": "2026-11-10",
                 "end": "2026-11-12", "day_part": "full_day",
                 "status": "allowed", "note": "..."}],
     "balances": [{"email": "...", "available": 12.5, "carryforward": 3}]}
"""

import hmac
import json
import os
from datetime import date

from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from employee.models import Employee
from leave.models import AvailableLeave, LeaveRequest, LeaveType

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


@csrf_exempt
@require_http_methods(["PUT"])
def bot_sync(request):
    token = os.environ.get("SCG_BOT_SYNC_TOKEN", "")
    given = request.headers.get("X-Bot-Token", "")
    if not token or not hmac.compare_digest(given.encode(), token.encode()):
        return JsonResponse({"error": "forbidden"}, status=403)

    try:
        payload = json.loads(request.body)
    except ValueError:
        return JsonResponse({"error": "invalid json"}, status=400)

    employees = {
        e.email.strip().lower(): e
        for e in Employee._base_manager.exclude(email__isnull=True)
        if e.email
    }
    return JsonResponse(
        {
            "leaves": [apply_leave(row, employees) for row in payload.get("leaves", [])],
            "balances": [
                apply_balance(row, employees) for row in payload.get("balances", [])
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
            HALF_DAYS.get(row.get("day_part"), "full_day") if start == end else "full_day"
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
            if (balance.pk and balance.available_days == available
                    and balance.carryforward_days == carryforward):
                return {**result, "result": "unchanged"}
            balance.available_days = available
            balance.carryforward_days = carryforward
            balance.save()
        return {**result, "result": "updated"}
    except Exception as exc:
        return {**result, "result": "error", "error": repr(exc)[:300]}
