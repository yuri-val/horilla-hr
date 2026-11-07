"""The org chart for the corporate bot (SOF-2120).

HR filled Horilla's reporting lines (who reports to whom) while the bot only
knew department heads. The bot imports them once from here, then becomes the
source of truth and pushes every later change back through bot-sync.

    GET /scg/bot-employees/   X-Bot-Token: <SCG_BOT_SYNC_TOKEN>
    {"employees": [{"id": 12, "email": "...", "first_name": "...", "last_name": "...",
                    "active": true, "department": "Ship Management",
                    "position": "Technical Superintendent",
                    "manager_id": 3, "manager_email": "..."}]}
"""

from django.http import JsonResponse
from django.views.decorators.http import require_GET

from employee.models import Employee
from scg_overrides.bot_sync import authorized


@require_GET
def bot_employees(request):
    if not authorized(request):
        return JsonResponse({"error": "forbidden"}, status=403)

    employees = (
        Employee._base_manager.select_related(
            "employee_work_info__department_id",
            "employee_work_info__job_position_id",
            "employee_work_info__reporting_manager_id",
        )
        .exclude(email__isnull=True)
        .order_by("pk")
    )
    return JsonResponse({"employees": [row(e) for e in employees]})


def row(employee):
    work = getattr(employee, "employee_work_info", None)
    manager = work.reporting_manager_id if work else None
    return {
        "id": employee.pk,
        "email": employee.email,
        "first_name": employee.employee_first_name,
        "last_name": employee.employee_last_name,
        "active": employee.is_active,
        "department": work.department_id.department
        if work and work.department_id
        else None,
        "position": work.job_position_id.job_position
        if work and work.job_position_id
        else None,
        "manager_id": manager.pk if manager else None,
        "manager_email": manager.email if manager else None,
    }
