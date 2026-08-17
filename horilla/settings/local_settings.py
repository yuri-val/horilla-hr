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

from .base import INSTALLED_APPS, SIDEBARS

INSTALLED_APPS += ["scg_overrides"]

# Modules dropped from the sidebar.
#   helpdesk - never adopted at SCG.
for _hidden_module in ("helpdesk",):
    if _hidden_module in SIDEBARS:
        SIDEBARS.remove(_hidden_module)
