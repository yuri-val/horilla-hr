"""Settings page of the corporate-bot notification transport (SOF-2127)."""

from django import forms
from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils.translation import gettext_lazy as _

from base.models import IntegrationApps
from horilla.decorators import login_required, permission_required
from notifications.signals import notify
from scg_overrides import bot_notify
from scg_overrides.models import BotNotification, BotTransport


class BotTransportForm(forms.ModelForm):
    enabled = forms.BooleanField(
        required=False, label=_("Send notifications to the corporate bot")
    )

    class Meta:
        model = BotTransport
        fields = ["bot_url", "token", "site_url", "exclude_patterns"]
        widgets = {
            "bot_url": forms.URLInput(
                attrs={
                    "class": "oh-input w-100",
                    "placeholder": "https://chatapi.scglobal.group",
                }
            ),
            "token": forms.PasswordInput(
                attrs={"class": "oh-input w-100", "autocomplete": "new-password"},
                render_value=True,
            ),
            "site_url": forms.URLInput(
                attrs={
                    "class": "oh-input w-100",
                    "placeholder": "https://hrm.softo.net",
                }
            ),
            "exclude_patterns": forms.Textarea(
                attrs={
                    "class": "oh-input w-100",
                    "rows": 4,
                    "placeholder": "attendance\nshift request",
                }
            ),
        }


@login_required
@permission_required(perm="base.change_integrationapps")
def bot_notifications_view(request):
    transport = BotTransport.current()
    integration = IntegrationApps.objects.filter(app_label=bot_notify.APP_LABEL).first()
    if request.method == "POST" and request.POST.get("action") == "test":
        notify.send(
            request.user.employee_get,
            recipient=request.user,
            verb="Test notification from Horilla: the corporate bot transport works.",
            redirect="/scg/bot-notifications/",
            icon="chatbubble-outline",
            include_actor=True,
        )
        messages.info(request, _("Test notification queued - check your Google Chat."))
        return redirect("scg-bot-notifications")
    form = BotTransportForm(
        request.POST or None,
        instance=transport,
        initial={"enabled": bool(integration and integration.is_enabled)},
    )
    if request.method == "POST" and form.is_valid():
        form.save()
        IntegrationApps.objects.update_or_create(
            app_label=bot_notify.APP_LABEL,
            company=None,
            defaults={"is_enabled": form.cleaned_data["enabled"]},
        )
        messages.success(request, _("Saved."))
        return redirect("scg-bot-notifications")
    return render(
        request,
        "scg_overrides/bot_notifications.html",
        {
            "form": form,
            "enabled": bot_notify.enabled(),
            "recent": BotNotification.objects.all()[:30],
            "pending": BotNotification.objects.filter(
                sent_at__isnull=True, attempts__lt=bot_notify.MAX_ATTEMPTS
            ).count(),
        },
    )
