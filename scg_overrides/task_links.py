"""A document link on every onboarding task (SOF-2140).

The newcomer's tasks are "read the vacation policy", "look at the org chart",
"connect the corporate bot" - each is about a document that lives on the
company portal or Drive. The link is kept in OnboardingTaskLink and shown
wherever the task is: the employee's Onboarding tab, the start-onboarding
e-mail and the task block library; Add Task Blocks copies it with the task.

HR sets it in the task form. Both forms are rendered field by field by
HorillaFormView, so adding a form field is all the UI needs; the views save
the task themselves and know nothing of the field, so the link is picked up
from the posted form on the task's post_save. A save outside that form (a
copy, a script) posts no link field and leaves the link as it is.
"""

from django import forms
from django.db.models.signals import post_save
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

FIELD = "scg_link"


def link_of(task):
    """The task's document link, or ""."""
    if task is None or not task.pk:
        return ""
    from scg_overrides.models import OnboardingTaskLink

    found = OnboardingTaskLink.objects.filter(task=task).values_list("url", flat=True).first()
    return found or ""


def set_link(task, url):
    """Keep, change or (with an empty url) drop the task's link."""
    from scg_overrides.models import OnboardingTaskLink

    url = link_field().clean(url) if url and url.strip() else ""
    if url:
        OnboardingTaskLink.objects.update_or_create(task=task, defaults={"url": url})
    else:
        OnboardingTaskLink.objects.filter(task=task).delete()


def link_field(initial=""):
    return forms.URLField(
        required=False,
        max_length=500,
        assume_scheme="https",
        initial=initial,
        label=_("Document link"),
        help_text=_("The policy or guide this task is about, e.g. a page of the company portal."),
        widget=forms.URLInput(attrs={"class": "oh-input w-100", "placeholder": "https://"}),
    )


def add_link_field(form):
    """Add the link to a task form, filled from the task it edits."""
    from onboarding.models import OnboardingTask

    task = form.instance if isinstance(form.instance, OnboardingTask) else None
    form.fields[FIELD] = link_field(link_of(task))


def save_posted_link(sender, instance, **kwargs):
    """Store the link a task form posted alongside the task being saved."""
    from horilla.horilla_middlewares import _thread_locals

    request = getattr(_thread_locals, "request", None)
    if request is None or request.method != "POST" or FIELD not in request.POST:
        return
    set_link(instance, request.POST[FIELD])


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
    from onboarding.cbv.onboarding_candidates import CandidateProfileTasks
    from onboarding.forms import OnboardingTaskForm, OnboardingViewTaskForm
    from onboarding.models import CandidateTask, OnboardingTask

    for form_class in (OnboardingViewTaskForm, OnboardingTaskForm):
        original_init = form_class.__init__

        def __init__(self, *args, _original=original_init, **kwargs):
            _original(self, *args, **kwargs)
            add_link_field(self)

        form_class.__init__ = __init__
        form_class.cols = {**getattr(form_class, "cols", {}), FIELD: 12}

    post_save.connect(save_posted_link, sender=OnboardingTask, dispatch_uid="scg-task-link")

    # The employee profile's Onboarding tab inherits these columns
    CandidateTask.scg_task_col = task_col
    CandidateProfileTasks.columns = [
        (label, "scg_task_col" if accessor == "onboarding_task_id__task_title" else accessor)
        for label, accessor, *rest in CandidateProfileTasks.columns
    ]
