"""
Client-specific overrides.

Imported last from horilla.settings.__init__ (after base + addons).
Override settings here; do NOT use ``from .base import *`` — that re-exports
base MEDIA_* values and wipes AWS S3 paths set by addons.py.

Examples:

    DEBUG = False
    ALLOWED_HOSTS = ["client.example.com"]
    WHITE_LABELLING = True
    DOC_BASE_URL = "https://www.horilla.com"

    # Extend lists via selective import (same list object as base):
    from .base import INSTALLED_APPS, MIDDLEWARE

    INSTALLED_APPS += ["client_portal"]
    MIDDLEWARE += ["client_portal.middleware.ClientTrackingMiddleware"]
"""

# ---------------------------------------------------------------------------
# Staff Centre Group — hrm.softo.net
#
# Everything below is configuration rather than a patch: no product file is
# edited, so an upstream merge cannot conflict with any of it. Hiding here
# means hiding from *navigation* only — the app stays installed, its URLs keep
# resolving and its data is untouched, so every entry is reversible by editing
# this file alone.
# ---------------------------------------------------------------------------

from django.utils.translation import gettext_lazy as _

from .base import INSTALLED_APPS, SIDEBARS

INSTALLED_APPS += ["scg_overrides"]

# Modules dropped from the sidebar.
#   helpdesk   - never adopted at SCG.
#   attendance - check-in/check-out is switched off for the company
#                (AttendanceGeneralSetting.enable_check_in is False), so every
#                screen in the module could only ever come up empty.
for _hidden_module in ("helpdesk", "attendance"):
    if _hidden_module in SIDEBARS:
        SIDEBARS.remove(_hidden_module)

# Submenus reworked, keyed by app and matched on the English menu label (the
# gettext msgid), which scg_overrides reads with translations disabled so the
# match never depends on the active language. `None` hides the entry; a dict
# re-points it. A label upstream renames stops matching, which makes the entry
# reappear and logs a warning — it never changes silently.
#
# "Policies & Discipline" is re-pointed rather than hidden: HR wants the
# policies but not the disciplinary tabs, and the standalone policies page has
# the same content without them. Trimming the tabs instead would mean copying
# that template and freezing our copy against upstream.
SCG_SUBMENU_OVERRIDES = {
    "employee": {
        "Requests": None,
        "Work Schedules": None,
        "Policies & Discipline": {"menu": _("Policies"), "redirect": "view-policies"},
    },
    # HR does not plan vacation-restriction periods, so the page could only
    # ever show its empty state.
    "leave": {"Restricted Leave Periods": None},
    # Upstream replaced the per-module report submenus with a Standard Reports
    # catalog plus Explorer, so the old "Reports > Attendance" entry no longer
    # exists to hide. Attendance stays out of navigation via SIDEBARS above;
    # what the catalog lists inside itself is a separate question.
}

# Model methods re-pointed at another method of the same model.
#
# HR asked to drop the "(badge id)" that trails every employee name. The list
# column, its default set, the card title and the card heading all resolve
# through Employee.employee_name_with_badge_id, so aliasing that one method
# covers every one of them without editing a view or freezing a template.
# Employee.__str__ deliberately keeps the badge: dropdowns, exports and the
# admin need it to tell namesakes apart.
SCG_MODEL_METHOD_ALIASES = {
    "employee.Employee": {
        "employee_name_with_badge_id": "get_full_name",
    },
}
