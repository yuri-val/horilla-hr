"""Give a handful of EmployeeWorkInformation rows real change history.

Fixture-loaded work info rows are a single static snapshot with zero
history: `loaddata` saves with raw=True, which simple_history's post_save
handler explicitly skips. So the profile History tab's "Work Information"
section renders empty for every employee regardless of the fixtures'
content. This replays a short, believable raise/promotion story -- real
.save() calls, not raw fixture rows or a bare .update() -- on a
deterministic subset of employees, ending back at each row's original
values so the live record is unchanged; only its history is populated.
"""

from __future__ import annotations

import logging
from datetime import date

from django.apps import apps
from django.db import transaction

logger = logging.getLogger(__name__)

HISTORY_EMPLOYEE_COUNT = 40
# Every 2nd selected employee's story also includes a promotion (a
# department-scoped job_position change), not just a raise.
PROMOTION_SHARE = 2


@transaction.atomic
def backfill_employee_work_info_history(today: date | None = None) -> dict[str, int]:
    """
    Replay 3 real .save() versions (a lower starting basic_salary, a
    mid-point raise, and for half the selected employees a job_position
    change in between) on a deterministic subset of EmployeeWorkInformation
    rows. Safe to call repeatedly: skips any row that already has a visible
    history diff, so re-running load_demo_data without --flush doesn't
    keep growing each employee's history forever.
    """
    result = {"employees_seeded": 0, "promotions": 0}
    if not apps.is_installed("employee"):
        return result

    from base.models import JobPosition
    from employee.models import EmployeeWorkInformation
    from horilla_views.history_methods import get_diff

    work_infos = list(
        EmployeeWorkInformation._base_manager.order_by("employee_id")[
            :HISTORY_EMPLOYEE_COUNT
        ]
    )
    for index, work_info in enumerate(work_infos):
        if get_diff(work_info, "history_set"):
            continue

        original_salary = work_info.basic_salary or 0
        original_job_position_id = work_info.job_position_id_id

        starting_salary = max(int(original_salary * 0.7), 1)
        mid_salary = max(int(original_salary * 0.85), starting_salary)

        alternate_job_position_id = None
        if index % PROMOTION_SHARE == 0 and work_info.department_id_id:
            alternate_job_position_id = (
                JobPosition._base_manager.filter(
                    department_id=work_info.department_id_id
                )
                .exclude(pk=original_job_position_id)
                .order_by("id")
                .values_list("id", flat=True)
                .first()
            )

        work_info.basic_salary = starting_salary
        if alternate_job_position_id:
            work_info.job_position_id_id = alternate_job_position_id
        work_info.save()

        work_info.basic_salary = mid_salary
        work_info.save()

        work_info.basic_salary = original_salary
        if alternate_job_position_id:
            work_info.job_position_id_id = original_job_position_id
        work_info.save()

        result["employees_seeded"] += 1
        if alternate_job_position_id:
            result["promotions"] += 1

    logger.info("Employee work info history backfill: %s", result)
    return result
