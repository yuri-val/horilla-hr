"""Horilla's notifications delivered by the corporate bot in Google Chat (SOF-2127).

Nobody reads Horilla's bell: approvals and tasks already reach people in
Chat through the corporate bot. So every ``notify.send`` (240 call sites, none
of them touched) also lands here: one receiver writes an outbox row per
recipient, a thread pushes the batch to the bot's ``POST /horilla/notifications``
at once, and a scheduled job re-sends what did not get through. The bot finds
the employee by e-mail and writes to their DM; Horilla's own notification stays
as it was.

Switched on in Settings → Integrations as ``corporate_bot``; the bot's URL,
the shared token and the exclude patterns live in ``BotTransport`` (one row),
editable at ``/scg/bot-notifications/``.
"""

import logging
import re
import threading

import requests
from django.db import transaction
from django.dispatch import receiver
from django.utils import timezone

from base.models import IntegrationApps
from notifications.signals import notify
from scg_overrides.models import BotNotification, BotTransport

logger = logging.getLogger(__name__)

APP_LABEL = "corporate_bot"
MAX_ATTEMPTS = 10
BATCH = 50
TIMEOUT = 15


def enabled():
    return (
        IntegrationApps.objects.filter(app_label=APP_LABEL, is_enabled=True).exists()
        and BotTransport.current().configured()
    )


def render_verb(verb, data):
    """``"Leave request updated for %(employee)s."`` with its kwargs, when they fit."""
    text = str(verb)
    try:
        return text % data if "%(" in text and data else text
    except (KeyError, TypeError, ValueError):
        return text


def excluded(transport, text, redirect):
    """Any line of the exclude patterns (regex, case-insensitive) matching the text or the link."""
    for line in (transport.exclude_patterns or "").splitlines():
        pattern = line.strip()
        if not pattern:
            continue
        try:
            if re.search(pattern, text or "", re.I) or re.search(
                pattern, redirect or "", re.I
            ):
                return True
        except re.error:
            logger.warning("bot notify: bad exclude pattern %r", pattern)
    return False


def absolute(redirect, request=None):
    if not redirect:
        return ""
    if redirect.startswith("http"):
        return redirect
    if request is not None:
        try:
            return request.build_absolute_uri(redirect)
        except Exception:  # pragma: no cover - a request without a host
            pass
    base = BotTransport.current().site_url.rstrip("/")
    return f"{base}{redirect}" if base else redirect


@receiver(notify, dispatch_uid="scg_overrides.bot_notify")
def queue_for_bot(sender, recipient, verb, redirect=None, icon=None, **kwargs):
    """One outbox row per recipient; the actor is never notified about their own action."""
    try:
        if not enabled():
            return
        transport = BotTransport.current()
        from horilla.horilla_middlewares import _thread_locals

        request = getattr(_thread_locals, "request", None)
        data = {
            k: v
            for k, v in kwargs.items()
            if not k.startswith("verb_") and k != "signal"
        }
        text = render_verb(verb, data)
        url = absolute(redirect, request)
        if excluded(transport, text, url):
            return
        recipients = (
            recipient
            if isinstance(recipient, (list, tuple)) or hasattr(recipient, "__iter__")
            else [recipient]
        )
        actor_user = getattr(sender, "employee_user_id", None) or (
            sender if hasattr(sender, "email") else None
        )
        rows = []
        for user in recipients:
            email = (getattr(user, "email", "") or "").strip()
            if not email or (
                actor_user is not None
                and getattr(user, "pk", None) == getattr(actor_user, "pk", None)
            ):
                continue
            rows.append(
                BotNotification(
                    recipient_email=email,
                    text=text[:500],
                    url=url[:500],
                    icon=(icon or "")[:50],
                )
            )
        if not rows:
            return
        BotNotification.objects.bulk_create(rows)
        transaction.on_commit(
            lambda: threading.Thread(target=deliver_pending, daemon=True).start()
        )
    except (
        Exception
    ):  # one bad notification must never break the request that raised it
        logger.exception("bot notify: queueing failed")


def deliver_pending(limit=BATCH):
    """Push the unsent rows to the bot in one batch; returns how many were handled."""
    transport = BotTransport.current()
    if not transport.configured():
        return 0
    with transaction.atomic():
        rows = list(
            BotNotification.objects.select_for_update(skip_locked=True)
            .filter(sent_at__isnull=True, attempts__lt=MAX_ATTEMPTS)
            .order_by("id")[:limit]
        )
        if not rows:
            return 0
        now = timezone.now()
        for row in rows:
            row.attempts += 1
            row.last_attempt_at = now
        BotNotification.objects.bulk_update(rows, ["attempts", "last_attempt_at"])
    payload = {
        "notifications": [
            {
                "id": r.pk,
                "email": r.recipient_email,
                "text": r.text,
                "url": r.url,
                "icon": r.icon,
            }
            for r in rows
        ]
    }
    try:
        response = requests.post(
            transport.bot_url.rstrip("/") + "/horilla/notifications",
            json=payload,
            headers={"X-Bot-Token": transport.token},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        results = {r["id"]: r for r in response.json().get("notifications", [])}
    except Exception as exc:
        logger.warning("bot notify: delivery failed: %s", exc)
        BotNotification.objects.filter(pk__in=[r.pk for r in rows]).update(
            last_error=str(exc)[:255]
        )
        return 0
    for row in rows:
        result = results.get(row.pk, {})
        outcome = result.get("result", "error")
        row.result = outcome
        row.last_error = (result.get("error") or "")[:255]
        # "no_dm" and "unknown_user" are final: the bot cannot reach them until the person writes to it
        row.sent_at = (
            timezone.now()
            if outcome in ("delivered", "no_dm", "unknown_user")
            else None
        )
    BotNotification.objects.bulk_update(rows, ["result", "last_error", "sent_at"])
    return len(rows)


def retry_pending():
    """Scheduled every few minutes: whatever the immediate push missed."""
    try:
        if enabled():
            deliver_pending()
    except Exception:  # pragma: no cover
        logger.exception("bot notify: retry failed")
