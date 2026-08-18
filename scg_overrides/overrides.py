"""
Apply SCG's declarative overrides on top of stock Horilla.

Why this app exists
-------------------
Horilla runs here as a checkout of our fork of upstream, and the whole point of
that arrangement is that pulling upstream stays a non-event. So every
customisation that can be expressed as data lives in
``horilla/settings/local_settings.py`` — the file upstream ships empty for
exactly this purpose, imported last, and therefore impossible to conflict on —
and this module is the thin layer that applies it. Nothing here edits a
product file.

Which hook each override uses, and why that one
-----------------------------------------------
``SCG_SUBMENU_OVERRIDES``
    ``horilla.config.sidebar()`` builds the menu from ``settings.SIDEBARS``
    (module level, so whole modules are hidden from local_settings directly)
    and then from each app's ``sidebar.SUBMENUS``. Individual submenus have no
    settings hook, but every submenu already honours an ``"accessibility"``
    key holding the dotted path of a ``callable(request, submenu, perms) ->
    bool``; 76 submenus across 15 apps use it. Hiding one therefore means
    pointing that documented hook at :func:`deny`, not editing the app.

    Note this is also the only mechanism that works on a superuser. Most of
    those 76 hooks resolve through ``user.has_perm()``, which is unconditionally
    true for superusers, so revoking permissions cannot hide anything from them.

    An entry can also be re-pointed instead of hidden, which is how a page that
    mixes something wanted with something unwanted gets trimmed: sending
    "Policies & Discipline" to the standalone policies page leaves the
    disciplinary tabs unreachable without copying a template and freezing it
    against upstream.

    Matching is by English menu label rather than by URL name because
    ``submenu["redirect"]`` is a ``reverse_lazy`` proxy: forcing it during
    ``AppConfig.ready()`` would pull in the whole URLConf while the app
    registry is still settling. Labels are plain lazy gettext, and recovering
    their msgid needs nothing but ``translation.override(None)``. Replacement
    redirects are given as URL *names* and wrapped in ``reverse_lazy`` here, so
    they stay unresolved until the sidebar is actually rendered.

``SCG_MODEL_METHOD_ALIASES``
    Some of what HR asks to change is neither a menu nor a setting but a string
    a model builds, rendered from several places at once — a list column
    declared as a ``(label, accessor, ...)`` tuple in Python, a card heading in
    a template, a card title in a view dict. Re-pointing the one model method
    they all resolve through fixes every caller together, where patching each
    caller would mean editing a view *and* freezing a copy of a template.

Failure policy
--------------
A broken override must never stop the site from booting, so each step is
guarded and logs rather than raising. The visible symptom of a failed override
is the hidden item coming back — the safe direction to fail in, and loud enough
to notice.
"""

import importlib
import logging

from django.apps import apps
from django.conf import settings
from django.urls import reverse_lazy
from django.utils import translation

logger = logging.getLogger(__name__)


def deny(request, submenu, user_perms, *args, **kwargs):
    """Accessibility hook that hides a submenu from everyone, superusers included."""
    return False


def _msgid(label):
    """Return a lazy translatable label's original text, whatever the active language."""
    with translation.override(None):
        return str(label)


def configure_submenus():
    """Hide (spec is ``None``) or re-point the configured submenus."""
    for app_label, specs in (getattr(settings, "SCG_SUBMENU_OVERRIDES", None) or {}).items():
        if not apps.is_installed(app_label):
            logger.warning("SCG_SUBMENU_OVERRIDES: app %r is not installed", app_label)
            continue
        try:
            sidebar = importlib.import_module(f"{app_label}.sidebar")
        except Exception:
            logger.exception("SCG_SUBMENU_OVERRIDES: cannot import %s.sidebar", app_label)
            continue

        matched = set()
        for submenu in getattr(sidebar, "SUBMENUS", None) or []:
            label = _msgid(submenu.get("menu", ""))
            if label not in specs:
                continue
            matched.add(label)
            spec = specs[label]
            if spec is None:
                submenu["accessibility"] = "scg_overrides.overrides.deny"
                continue
            if "menu" in spec:
                submenu["menu"] = spec["menu"]
            if "redirect" in spec:
                submenu["redirect"] = reverse_lazy(spec["redirect"])

        if set(specs) - matched:
            logger.warning(
                "SCG_SUBMENU_OVERRIDES: no submenu of %r matches %s — upstream probably "
                "renamed it, and the entry is untouched",
                app_label,
                sorted(set(specs) - matched),
            )


def alias_model_methods():
    """Re-point model methods at another method of the same model."""
    for model_path, aliases in (getattr(settings, "SCG_MODEL_METHOD_ALIASES", None) or {}).items():
        try:
            model = apps.get_model(model_path)
        except Exception:
            logger.exception("SCG_MODEL_METHOD_ALIASES: cannot resolve model %s", model_path)
            continue

        for name, target_name in aliases.items():
            target = getattr(model, target_name, None)
            if not callable(target):
                logger.warning(
                    "SCG_MODEL_METHOD_ALIASES: %s has no callable %r to alias %r onto — "
                    "leaving %r as upstream defines it",
                    model_path,
                    target_name,
                    name,
                    name,
                )
                continue
            if not hasattr(model, name):
                logger.warning(
                    "SCG_MODEL_METHOD_ALIASES: %s has no %r — upstream probably renamed "
                    "or dropped it, so nothing was aliased",
                    model_path,
                    name,
                )
                continue
            setattr(model, name, target)


def install_onboarding_task_blocks():
    """SCG addition, not an override: reusable onboarding task blocks.

    The feature itself lives in scg_overrides.onboarding_blocks; this only
    drops its entry point into the pipeline's Actions dropdown.
    """
    from scg_overrides.onboarding_blocks import install_nav_action

    install_nav_action()


def apply():
    """Run every override, letting none of them break startup."""
    for step in (configure_submenus, alias_model_methods, install_onboarding_task_blocks):
        try:
            step()
        except Exception:
            logger.exception("scg_overrides: %s failed", step.__name__)
