"""Reusable onboarding task blocks.

Horilla ties onboarding stages and tasks to one recruitment (OnboardingStage
has a plain FK), so every new vacancy starts with an empty pipeline and the
standard checklists - IT accounts, HR paperwork, equipment, probation - have
to be retyped each time. Upstream master has no template concept either (as
of Aug 2026), so SCG keeps the standard checklists on ordinary recruitments
used as block libraries (one closed recruitment per process works well: it
never shows among live vacancies) and copies them into a real vacancy here.

The copy mirrors what onboarding.views.task_creation does by hand: stages
keep their order and managers, tasks keep their managers and the is_required
flag, and candidates already onboarding in the target vacancy are wired to
every copied task through CandidateTask - the same rows the product creates
when a task is added to a live pipeline.
"""

from django import forms
from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext

from horilla.decorators import login_required, permission_required
from onboarding.models import CandidateStage, CandidateTask, OnboardingStage, OnboardingTask
from recruitment.models import Candidate, Recruitment


class TaskBlockCopyForm(forms.Form):
    """Pick a target vacancy and the block recruitments to copy from."""

    target = forms.ModelChoiceField(
        queryset=Recruitment.objects.none(),
        label=_("Vacancy"),
        widget=forms.Select(attrs={"class": "oh-select w-100"}),
    )
    sources = forms.ModelMultipleChoiceField(
        queryset=Recruitment.objects.none(),
        label=_("Task blocks"),
        widget=forms.CheckboxSelectMultiple,
        help_text=_(
            "Every stage that has tasks is appended, in order, to the "
            "vacancy's onboarding; empty stages are skipped."
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["target"].queryset = Recruitment.objects.filter(
            is_active=True, closed=False
        ).order_by("title")
        # Blocks are recruitments that own at least one stage. Closed ones stay
        # eligible on purpose: a closed recruitment is how a block library is
        # kept out of the live vacancy lists.
        self.fields["sources"].queryset = (
            Recruitment.objects.filter(is_active=True, onboarding_stage__isnull=False)
            .distinct()
            .order_by("title")
        )

    def clean(self):
        cleaned = super().clean()
        target, sources = cleaned.get("target"), cleaned.get("sources")
        if target and sources and target in list(sources):
            raise forms.ValidationError(
                _("A vacancy cannot receive its own stages - unselect it in the blocks.")
            )
        return cleaned


def _copy_blocks(target, sources):
    """Append every stage and task of `sources` to `target`, in order."""
    last = (
        OnboardingStage.objects.filter(recruitment_id=target)
        .exclude(sequence__isnull=True)
        .order_by("-sequence")
        .values_list("sequence", flat=True)
        .first()
    )
    next_sequence = (last or 0) + 1
    onboarding_candidates = Candidate.objects.filter(
        recruitment_id=target, onboarding_stage__isnull=False
    )

    copied_stages = copied_tasks = 0
    for source in sources:
        for stage in OnboardingStage.objects.filter(recruitment_id=source).order_by(
            "sequence", "id"
        ):
            tasks = list(stage.onboarding_task.all().order_by("id"))
            # Every recruitment gets an automatic empty "Initial" stage on
            # creation, so block libraries always carry one. A block is a
            # checklist - a stage with nothing to do has nothing to copy.
            if not tasks:
                continue
            new_stage = OnboardingStage.objects.create(
                stage_title=stage.stage_title,
                recruitment_id=target,
                sequence=next_sequence,
                # The target keeps its own single completion stage; a copied
                # block must never smuggle a second one in.
                is_final_stage=False,
            )
            next_sequence += 1
            new_stage.employee_id.set(stage.employee_id.all())
            copied_stages += 1

            for task in tasks:
                new_task = OnboardingTask.objects.create(
                    task_title=task.task_title,
                    stage_id=new_stage,
                    is_required=task.is_required,
                )
                new_task.employee_id.set(task.employee_id.all())
                copied_tasks += 1
                if onboarding_candidates:
                    new_task.candidates.set(onboarding_candidates)
                    for candidate in onboarding_candidates:
                        CandidateTask.objects.get_or_create(
                            candidate_id=candidate,
                            onboarding_task_id=new_task,
                            defaults={"stage_id": new_stage},
                        )
    return copied_stages, copied_tasks


def _library_contents(form):
    """What each offered block actually holds, for display next to the form.

    The pipeline lists candidates, not tasks: a stage shows its tasks only as
    columns beside a candidate standing in it, and the stage menu has no task
    list at all. So without this there is no way to see what a block contains
    before copying it - or to check afterwards what arrived.
    """
    libraries = []
    for recruitment in form.fields["sources"].queryset:
        stages = []
        for stage in OnboardingStage.objects.filter(recruitment_id=recruitment).order_by(
            "sequence", "id"
        ):
            tasks = [
                {
                    "title": task.task_title,
                    "required": task.is_required,
                    "owners": ", ".join(
                        person.get_full_name() for person in task.employee_id.all()
                    ),
                }
                for task in stage.onboarding_task.all().order_by("id")
            ]
            if tasks:
                stages.append({"title": stage.stage_title, "tasks": tasks})
        if stages:
            libraries.append({"title": recruitment.title, "stages": stages})
    return libraries


@login_required
@permission_required(perm="onboarding.add_onboardingstage")
def task_blocks_view(request):
    """Copy standard task blocks into a vacancy's onboarding."""
    form = TaskBlockCopyForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        target = form.cleaned_data["target"]
        stages, tasks = _copy_blocks(target, form.cleaned_data["sources"])
        messages.success(
            request,
            ngettext(
                "Added %(stages)d stage with %(tasks)d task to %(vacancy)s.",
                "Added %(stages)d stages with %(tasks)d tasks to %(vacancy)s.",
                stages,
            )
            % {"stages": stages, "tasks": tasks, "vacancy": target.title},
        )
        return redirect("cbv-pipeline-onboarding")
    return render(
        request,
        "scg_overrides/task_blocks_form.html",
        {"form": form, "libraries": _library_contents(form)},
    )


def install_nav_action():
    """Put the entry point into the onboarding pipeline's Actions dropdown."""
    from django.urls import reverse_lazy

    from onboarding.cbv.pipeline import PipelineNav

    PipelineNav.actions = list(PipelineNav.actions) + [
        {
            "action": _("Add Task Blocks"),
            "attrs": f'''href="{reverse_lazy('scg-onboarding-task-blocks')}"''',
        }
    ]
