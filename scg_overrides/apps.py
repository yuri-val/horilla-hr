from django.apps import AppConfig


class ScgOverridesConfig(AppConfig):
    """Applies the declarations in horilla/settings/local_settings.py at startup.

    Registered last (``INSTALLED_APPS += ["scg_overrides"]``) so every app it
    reaches into has already been loaded.
    """

    name = "scg_overrides"
    verbose_name = "SCG Overrides"

    def ready(self):
        from . import (  # noqa: F401 - the receiver registers on import
            bot_notify,
            overrides,
        )

        overrides.apply()
        from horilla.scheduling import register_job

        register_job(bot_notify.retry_pending, "interval", minutes=5)
