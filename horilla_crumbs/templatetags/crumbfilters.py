"""Template filters for rendering breadcrumbs.

Breadcrumb entries are stored in the session as raw URL path segments (e.g.
``auto-payslip-settings-view``) so the stored data stays language-agnostic and
JSON-serializable. Translation therefore has to happen at render time, which is
what ``crumb_label`` does.
"""

import importlib
import re

from django import template
from django.conf import settings
from django.utils.translation import gettext as _

register = template.Library()

# A route slug is all lowercase letters/digits joined by single hyphens. Anything
# else (company names, object reprs such as an employee's full name, or titles
# already mapped by BREADCRUMB_URL_NAMES) is handled by the fallback branch.
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# Lazily-built map: final URL path segment -> translated sidebar label. Reused
# across requests; values are lazy gettext objects re-evaluated per active locale.
_SLUG_MAP = None


def _build_slug_map():
    mapping = {}
    for app in getattr(settings, "SIDEBARS", []):
        try:
            sidebar = importlib.import_module(app + ".sidebar")
        except Exception:
            continue
        for submenu in getattr(sidebar, "SUBMENUS", None) or []:
            redirect = submenu.get("redirect")
            label = submenu.get("menu")
            if not redirect or not label:
                continue
            try:
                segment = str(redirect).split("?")[0].rstrip("/").rsplit("/", 1)[-1]
            except Exception:
                continue
            if segment and segment not in mapping:
                mapping[segment] = label
    return mapping


@register.filter
def crumb_label(name):
    """Localize a breadcrumb segment.

    Known sidebar destinations map to their translated menu label; other route
    slugs are prettified (``auto-payslip-settings-view`` -> ``Auto Payslip
    Settings View``) and passed through gettext so single-word module segments
    (payroll, recruitment, ...) still translate. Every other value (section
    titles, BREADCRUMB_URL_NAMES titles, object names) goes through gettext,
    which returns it unchanged when it has no catalog entry.
    """
    if not isinstance(name, str):
        return name
    if not _SLUG_RE.match(name):
        return _(name)
    global _SLUG_MAP
    if _SLUG_MAP is None:
        _SLUG_MAP = _build_slug_map()
    if name in _SLUG_MAP:
        return _SLUG_MAP[name]
    return _(name.replace("-", " ").title())
