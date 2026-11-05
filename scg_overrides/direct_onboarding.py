"""Onboarding for an employee hired without the recruitment pipeline (SOF-2176).

Horilla's onboarding belongs to candidates: stages hang off a recruitment,
tasks are CandidateTask rows, and the employee profile's Onboarding tab finds
them through the candidate the employee was converted from. A person hired
"in the chat" is entered as an employee straight away, and nothing in the UI
can tie that employee to a candidate afterwards - conversion and the
onboarding portal both refuse an e-mail that already has a login.

So "Start onboarding" on the employee profile does the tying itself: it puts
the employee into one standing, closed recruitment as an already-converted
candidate, sets them on its first onboarding stage and gives them every task
of that recruitment (filled once from the task blocks with "Add Task Blocks").
The newcomer then sees and ticks their tasks in their own profile, managers
get the usual in-app notification, and the newcomer gets one e-mail with the
tasks and, if they have never signed in, the invitation to set a password.

The candidate stays in the Applied stage, never Hired, and the recruitment is
closed and unpublished: direct hires do not show up as recruitment pipeline,
vacancies or hires. Pressing the button again adds only what is missing.
"""

import logging

from django.contrib import messages
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from base.backends import ConfiguredEmailBackend
from employee.methods.methods import send_employee_invitation
from employee.models import Employee
from horilla.decorators import login_required, permission_required
from horilla.http.response import HorillaRedirect
from notifications.signals import notify
from onboarding.models import CandidateStage, CandidateTask, OnboardingTask
from recruitment.models import Candidate, Recruitment

logger = logging.getLogger(__name__)

RECRUITMENT_TITLE = "Онбординг без рекрутингу"


def standing_recruitment(company):
    """The closed recruitment every direct hire is onboarded in, created on first use."""
    recruitment = (
        Recruitment._base_manager.filter(title=RECRUITMENT_TITLE).order_by("id").first()
    )
    if recruitment is None:
        recruitment = Recruitment(
            title=RECRUITMENT_TITLE,
            description=_(
                "Onboarding for employees hired without the recruitment pipeline. "
                "Fill its tasks with Add Task Blocks; do not add candidates by hand."
            ),
            closed=True,
            is_published=False,
            is_event_based=True,
            vacancy=0,
            company_id=company,
        )
        recruitment.save()
    return recruitment


@login_required
@permission_required("onboarding.add_candidatestage")
@require_POST
def start_onboarding(request, emp_id):
    employee = get_object_or_404(Employee.objects.entire(), id=emp_id)
    work_info = getattr(employee, "employee_work_info", None)
    position = work_info.job_position_id if work_info else None
    if position is None:
        messages.error(request, _("Set the employee's job position first."))
        return HorillaRedirect(request)

    recruitment = standing_recruitment(work_info.company_id)
    tasks = list(
        OnboardingTask.objects.filter(stage_id__recruitment_id=recruitment).order_by(
            "stage_id__sequence", "id"
        )
    )
    if not tasks:
        messages.error(
            request,
            _("%(recruitment)s has no onboarding tasks yet - add them with Add Task Blocks.")
            % {"recruitment": RECRUITMENT_TITLE},
        )
        return HorillaRedirect(request)

    linked = Candidate.objects.filter(converted_employee_id=employee).first()
    if linked and linked.recruitment_id_id != recruitment.id:
        messages.info(
            request,
            _("%(employee)s is already onboarding in %(recruitment)s.")
            % {"employee": employee.get_full_name(), "recruitment": linked.recruitment_id},
        )
        return HorillaRedirect(request)

    with transaction.atomic():
        candidate = linked or new_candidate(employee, recruitment, position)
        # The first stage with work in it: every recruitment also gets an empty
        # "Initial" stage, and the pipeline lists a stage's tasks only next to
        # the candidates standing in it
        first = tasks[0].stage_id
        CandidateStage.objects.get_or_create(
            candidate_id=candidate, defaults={"onboarding_stage_id": first}
        )
        added = []
        for task in tasks:
            _row, created = CandidateTask.objects.get_or_create(
                candidate_id=candidate,
                onboarding_task_id=task,
                defaults={"stage_id": task.stage_id},
            )
            task.candidates.add(candidate)
            if created:
                added.append(task)

    if not added:
        messages.info(request, _("%(employee)s already has every onboarding task.") % {"employee": employee.get_full_name()})
        return HorillaRedirect(request)

    notify_managers(request, added)
    mailed = mail_newcomer(request, employee, tasks)
    messages.success(
        request,
        _("Onboarding started for %(employee)s: %(count)s tasks.")
        % {"employee": employee.get_full_name(), "count": len(added)},
    )
    if not mailed:
        messages.warning(request, _("The e-mail to the employee was not sent - check the mail server."))
    return HorillaRedirect(request)


def new_candidate(employee, recruitment, position):
    recruitment.open_positions.add(position)
    candidate = Candidate(
        name=employee.get_full_name(),
        email=employee.email,
        mobile=employee.phone,
        recruitment_id=recruitment,
        job_position_id=position,
        stage_id=recruitment.get_or_create_applied_stage(),
        converted_employee_id=employee,
        converted=True,
        start_onboard=True,
        joining_date=getattr(employee.employee_work_info, "date_joining", None),
    )
    candidate.save()
    return candidate


def notify_managers(request, tasks):
    users = {
        manager.employee_user_id
        for task in tasks
        for manager in task.employee_id.all()
        if manager.employee_user_id
    }
    if users:
        notify.send(
            request.user.employee_get,
            recipient=list(users),
            verb="You are chosen as an onboarding task manager",
            icon="people-circle",
            redirect=reverse("onboarding-view"),
        )


def mail_newcomer(request, employee, tasks):
    """One e-mail: the tasks and where to tick them; the password invitation if never signed in."""
    backend = ConfiguredEmailBackend()
    if not backend.configuration:
        return False
    user = employee.employee_user_id
    invited = bool(user and user.last_login is None)
    if invited:
        invited = send_employee_invitation(employee, request.get_host(), request.is_secure())

    scheme = "https" if request.is_secure() else "http"
    context = {
        "employee": employee,
        "tasks": tasks,
        "profile_url": f"{scheme}://{request.get_host()}{reverse('employee-profile', args=[employee.pk])}",
        "invited": invited,
    }
    message = EmailMultiAlternatives(
        subject=render_to_string("scg_overrides/mail/start_onboarding_subject.txt", context).strip(),
        body=render_to_string("scg_overrides/mail/start_onboarding.txt", context),
        from_email=backend.dynamic_from_email_with_display_name,
        to=[employee.get_mail()],
        connection=backend,
    )
    message.attach_alternative(
        render_to_string("scg_overrides/mail/start_onboarding.html", context), "text/html"
    )
    try:
        message.send()
        return True
    except Exception:
        logger.error("Start onboarding: mail to %s failed", employee, exc_info=True)
        return False


def accessibility(request, instance=None, user_perms=None, *args, **kwargs):
    """The profile action shows to whoever may start onboarding, for active employees."""
    return request.user.has_perm("onboarding.add_candidatestage") and getattr(
        instance, "is_active", False
    )


def install_profile_action():
    """Add "Start onboarding" to the employee profile's actions menu."""
    from django.conf import settings

    from employee.cbv.employee_profile import EmployeeProfileView

    # The view builds its action list per request in dispatch() and hands it
    # to the template in get_context_data(); prepending there leaves both the
    # product code and the class-level list untouched.
    get_context_data = EmployeeProfileView.get_context_data

    def get_context_data_with_onboarding(self, **kwargs):
        context = get_context_data(self, **kwargs)
        actions = context.get("actions")
        pk = self.kwargs.get("pk")
        if isinstance(actions, list) and pk:
            url = reverse("scg-start-onboarding", args=[pk])
            context["actions"] = [
                {
                    "title": _("Start Onboarding"),
                    "src": f"/{settings.STATIC_URL}images/ui/invite-user.svg",
                    "accessibility": "scg_overrides.direct_onboarding.accessibility",
                    "attrs": f'''
                        href="#"
                        hx-post="{url}"
                        hx-target="#relatedModel"
                        hx-confirm="{_('Start onboarding and send the employee their tasks?')}"
                    ''',
                }
            ] + actions
        return context

    EmployeeProfileView.get_context_data = get_context_data_with_onboarding
