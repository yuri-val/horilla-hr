"""Employees without a gender: findable and counted (SOF-2196).

The bot creates employees with no gender (scg_overrides.bot_sync), and
Horilla had no way to see them: the Gender filter offers Male/Female/Other
and an empty value skips the row, and the dashboard chart counts the three
values only. This adds "Not specified" to both, without editing employee/:

- the Gender row of the employee filter's "+ Add filter" builder is pointed
  at a declared filter (the documented ``filter_name`` shape of
  HorillaFilterSet._build_custom_filter_fields) that understands the extra
  value;
- the gender chart's route in employee/urls.py is pointed at a view that
  adds the extra slice.
"""

import django_filters
from django.db.models import Q
from django.http import JsonResponse
from django.utils.translation import gettext_lazy as _

from horilla.decorators import login_required

NOT_SPECIFIED = "not_set"
FILTER_NAME = "scg_gender"


def without_gender(queryset):
    return queryset.filter(Q(gender__isnull=True) | Q(gender=""))


def filter_gender(queryset, name, value):
    if value == NOT_SPECIFIED:
        return without_gender(queryset)
    return queryset.filter(gender=value)


@login_required
def dashboard_employee_gender(request):
    """The stock chart, plus a "Not specified" slice when anyone has none."""
    from employee.models import Employee

    employees = Employee.objects.filter(is_active=True)
    labels = [_("Male"), _("Female"), _("Other")]
    data = [len(employees.filter(gender=value)) for value in ("male", "female", "other")]
    missing = len(without_gender(employees))
    if missing:
        labels.append(_("Not specified"))
        data.append(missing)
    return JsonResponse(
        {"dataSet": [{"label": _("Employees"), "data": data}], "labels": labels}
    )


def install():
    from employee import views
    from employee.filters import EmployeeFilter

    EmployeeFilter.base_filters[FILTER_NAME] = django_filters.CharFilter(
        field_name="gender", method=filter_gender
    )

    build = EmployeeFilter._build_custom_filter_fields
    if not getattr(build, "scg_gender", False):

        def build_with_not_specified(self):
            fields = build(self)
            for entry in fields:
                if entry["key"] == "gender":
                    entry["filter_name"] = FILTER_NAME
                    entry["choices"] = entry["choices"] + [
                        [NOT_SPECIFIED, str(_("Not specified"))]
                    ]
            return fields

        build_with_not_specified.scg_gender = True
        EmployeeFilter._build_custom_filter_fields = build_with_not_specified

    # employee/urls.py captures the view when it is imported, which can happen
    # before this runs: re-point the route itself, not only the module attribute
    from employee import urls

    views.dashboard_employee_gender = dashboard_employee_gender
    for pattern in urls.urlpatterns:
        if getattr(pattern, "name", None) == "dashboard-employee-gender":
            pattern.callback = dashboard_employee_gender
