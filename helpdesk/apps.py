from django.apps import AppConfig
from django.conf import settings
from django.utils.translation import gettext_lazy as _


class HelpdeskConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "helpdesk"
    # Drives the app-level section header in the Roles/Permissions UI
    # (base/views.py's _permission_app_label() reads AppConfig.verbose_name)
    # -- without this, Django defaults it to "Helpdesk" from the app label.
    verbose_name = _("Ticket")

    def ready(self):
        from django.urls import include, path

        from horilla.urls import urlpatterns

        settings.APPS.append("helpdesk")
        urlpatterns.append(
            path("helpdesk/", include("helpdesk.urls")),
        )
        super().ready()
