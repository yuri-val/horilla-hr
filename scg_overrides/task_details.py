"""Description, document link and performer of a task (SOF-2140, SOF-2190).

An onboarding or offboarding task is "read the vacation policy", "return the
laptop": it needs the document it is about and a line of explanation, and the
newcomer's own tasks are done by the newcomer rather than by the task managers.
Horilla's task models carry a title and managers only, so the rest is kept in
OnboardingTaskDetails / OffboardingTaskDetails. The link shows wherever the
task is - the employee's Onboarding tab, the start-onboarding e-mail, the task
block library - Add Task Blocks copies the details, and the corporate bot
sends all of it to the people who do the task (scg_overrides.bot_tasks).

HR sets them in the task form. The three task forms are rendered field by
field by HorillaFormView, so adding form fields is all the UI needs; the views
save the task themselves and know nothing of the fields, so the details are
picked up from the posted form on the task's post_save. A save outside that
form (a copy, a script) posts no detail fields and leaves the details as they
are.
"""

from django import forms
from django.db.models.signals import post_save
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

LINK = "scg_link"
DESCRIPTION = "scg_description"
FOR_SUBJECT = "scg_for_subject"
EMPTY = {"url": "", "description": "", "for_subject": False}


def details_model(task):
    from offboarding.models import OffboardingTask
    from scg_overrides.models import OffboardingTaskDetails, OnboardingTaskDetails

    return (
        OffboardingTaskDetails
        if isinstance(task, OffboardingTask)
        else OnboardingTaskDetails
    )


def details_of(task):
    """{"url", "description", "for_subject"} of the task, empty when it has none."""
    if task is None or not task.pk:
        return dict(EMPTY)
    found = (
        details_model(task)
        .objects.filter(task=task)
        .values("url", "description", "for_subject")
        .first()
    )
    return found or dict(EMPTY)


def link_of(task):
    """The task's document link, or ""."""
    return details_of(task)["url"]


def set_details(task, **changes):
    """Change the given details; a task left with none loses its row."""
    if "url" in changes:
        url = changes["url"]
        changes["url"] = link_field().clean(url) if url and url.strip() else ""
    if "description" in changes:
        changes["description"] = (changes["description"] or "").strip()
    details = {**details_of(task), **changes}
    model = details_model(task)
    if details == EMPTY:
        model.objects.filter(task=task).delete()
    else:
        model.objects.update_or_create(task=task, defaults=details)


def set_link(task, url):
    set_details(task, url=url)


def link_field(initial=""):
    return forms.URLField(
        required=False,
        max_length=500,
        assume_scheme="https",
        initial=initial,
        label=_("Document link"),
        help_text=_(
            "The policy or guide this task is about, e.g. a page of the company portal."
        ),
        widget=forms.URLInput(
            attrs={"class": "oh-input w-100", "placeholder": "https://"}
        ),
    )


def add_detail_fields(form, task_model):
    """Add the details to a task form, filled from the task it edits."""
    task = form.instance if isinstance(form.instance, task_model) else None
    details = details_of(task)
    form.fields[LINK] = link_field(details["url"])
    form.fields[DESCRIPTION] = forms.CharField(
        required=False,
        initial=details["description"],
        label=_("Description"),
        help_text=_(
            "What to do and how - the people doing the task get it in Google Chat."
        ),
        widget=forms.Textarea(attrs={"class": "oh-input w-100", "rows": 3}),
    )
    form.fields[FOR_SUBJECT] = forms.BooleanField(
        required=False,
        initial=details["for_subject"],
        label=_("Done by the employee"),
        help_text=_("The newcomer or the leaver does it, not the task managers."),
    )


def save_posted_details(sender, instance, **kwargs):
    """Store the details a task form posted alongside the task being saved."""
    from horilla.horilla_middlewares import _thread_locals

    request = getattr(_thread_locals, "request", None)
    if request is None or request.method != "POST" or LINK not in request.POST:
        return
    set_details(
        instance,
        url=request.POST[LINK],
        description=request.POST.get(DESCRIPTION, ""),
        # An unticked checkbox is not posted at all
        for_subject=request.POST.get(FOR_SUBJECT) in ("on", "true", "True", "1"),
    )


def task_col(candidate_task):
    """The task's title in the Onboarding tab, opening its document when it has one."""
    task = candidate_task.onboarding_task_id
    url = link_of(task)
    if not url:
        return task.task_title
    return format_html(
        '<a href="{}" target="_blank" rel="noopener" onclick="event.stopPropagation()">{} &#128279;</a>',
        url,
        task.task_title,
    )


def install():
    from offboarding.forms import TaskForm
    from offboarding.models import OffboardingTask
    from onboarding.cbv.onboarding_candidates import CandidateProfileTasks
    from onboarding.forms import OnboardingTaskForm, OnboardingViewTaskForm
    from onboarding.models import CandidateTask, OnboardingTask

    for form_class, task_model in (
        (OnboardingViewTaskForm, OnboardingTask),
        (OnboardingTaskForm, OnboardingTask),
        (TaskForm, OffboardingTask),
    ):
        original_init = form_class.__init__

        def __init__(self, *args, _original=original_init, _model=task_model, **kwargs):
            _original(self, *args, **kwargs)
            add_detail_fields(self, _model)

        form_class.__init__ = __init__
        form_class.cols = {
            **getattr(form_class, "cols", {}),
            LINK: 12,
            DESCRIPTION: 12,
            FOR_SUBJECT: 12,
        }

    for task_model in (OnboardingTask, OffboardingTask):
        post_save.connect(
            save_posted_details,
            sender=task_model,
            dispatch_uid=f"scg-task-details-{task_model.__name__}",
        )

    # The employee profile's Onboarding tab inherits these columns
    CandidateTask.scg_task_col = task_col
    CandidateProfileTasks.columns = [
        (
            label,
            "scg_task_col"
            if accessor == "onboarding_task_id__task_title"
            else accessor,
        )
        for label, accessor, *rest in CandidateProfileTasks.columns
    ]
