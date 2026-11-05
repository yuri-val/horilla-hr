"""The corporate bot's push endpoint (SOF-2138)."""

import json
import os
from datetime import date
from unittest import mock

from django.test import TestCase

from horilla.testkit import make_company, make_employee
from leave.models import AvailableLeave, LeaveRequest, LeaveType

TOKEN = "test-bot-token"


@mock.patch.dict(os.environ, {"SCG_BOT_SYNC_TOKEN": TOKEN})
class BotSyncTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        company = make_company("Bot Sync Co")
        cls.employee = make_employee(company=company, email="Olha@Test.Horilla")
        for name in ("Annual Leave", "Day Off", "Personal Leave", "Sick Leave"):
            LeaveType.objects.create(name=name, total_days=20, payment="paid", reset=False)

    def put(self, payload, token=TOKEN):
        response = self.client.put(
            "/scg/bot-sync/", json.dumps(payload), content_type="application/json",
            HTTP_X_BOT_TOKEN=token,
        )
        return response

    def leave(self, **overrides):
        row = {
            "kind": "vacation", "bot_id": 2300, "email": "olha@test.horilla",
            "reason": "planned_vacation", "start": "2026-11-10", "end": "2026-11-12",
            "day_part": "full_day", "status": "allowed", "note": "море",
        }
        row.update(overrides)
        return self.put({"leaves": [row]}).json()["leaves"][0]

    def stored(self, tag="[bot vacation #2300]"):
        return LeaveRequest.objects.get(description__startswith=tag)

    def test_rejects_a_wrong_token(self):
        self.assertEqual(self.put({}, token="nope").status_code, 403)

    def test_rejects_when_no_token_is_configured(self):
        with mock.patch.dict(os.environ, {"SCG_BOT_SYNC_TOKEN": ""}):
            self.assertEqual(self.put({}, token="").status_code, 403)

    def test_creates_an_approved_leave_once(self):
        self.assertEqual(self.leave()["result"], "created")
        self.assertEqual(self.leave()["result"], "unchanged")

        leave = self.stored()
        self.assertEqual(leave.status, "approved")
        self.assertEqual(leave.employee_id, self.employee)
        self.assertEqual(leave.leave_type_id.name, "Annual Leave")
        self.assertEqual((leave.start_date, leave.end_date), (date(2026, 11, 10), date(2026, 11, 12)))
        self.assertEqual(leave.description, "[bot vacation #2300] | море")

    def test_updates_dates_and_cancels(self):
        self.leave()
        self.assertEqual(self.leave(end="2026-11-13")["result"], "updated")
        self.assertEqual(self.stored().end_date, date(2026, 11, 13))

        self.assertEqual(self.leave(end="2026-11-13", status="cancelled")["result"], "updated")
        self.assertEqual(self.stored().status, "cancelled")

    def test_cancels_a_record_deleted_in_the_bot(self):
        self.leave()
        result = self.put({"leaves": [{"kind": "vacation", "bot_id": 2300, "status": "cancelled"}]}).json()
        self.assertEqual(result["leaves"][0]["result"], "updated")
        self.assertEqual(self.stored().status, "cancelled")
        self.assertEqual(self.stored().end_date, date(2026, 11, 12))

    def test_never_creates_what_the_bot_has_not_approved(self):
        self.assertEqual(self.leave(status="denied")["result"], "skipped")
        self.assertEqual(self.leave(status="pending")["result"], "skipped")
        self.assertFalse(LeaveRequest.objects.filter(description__startswith="[bot vacation #2300]").exists())

    def test_half_day_only_on_a_single_day(self):
        self.leave(bot_id=1, start="2026-11-10", end="2026-11-10", day_part="pm")
        self.assertEqual(self.stored("[bot vacation #1]").start_date_breakdown, "second_half")
        self.leave(bot_id=2, day_part="am")
        self.assertEqual(self.stored("[bot vacation #2]").start_date_breakdown, "full_day")

    def test_maps_reasons_and_sick_leaves(self):
        self.leave(bot_id=3, reason="personal")
        self.assertEqual(self.stored("[bot vacation #3]").leave_type_id.name, "Day Off")
        self.leave(kind="sick_leave", bot_id=4, reason="sick_leave")
        self.assertEqual(self.stored("[bot sick_leave #4]").leave_type_id.name, "Sick Leave")

    def test_creates_the_birthday_type_on_first_use(self):
        self.assertEqual(self.leave(bot_id=5, reason="birthday", start="2026-11-10", end="2026-11-10")["result"], "created")
        birthday = LeaveType.objects.get(name="Birthday Day Off")
        self.assertFalse(birthday.reset)
        self.assertEqual(birthday.total_days, 1)

    def test_reports_an_unknown_employee(self):
        self.assertEqual(self.leave(email="ghost@test.horilla")["result"], "unknown_employee")

    def test_one_bad_row_does_not_stop_the_batch(self):
        rows = [
            {"kind": "vacation", "bot_id": 6, "email": "olha@test.horilla", "reason": "nonsense",
             "start": "2026-11-10", "end": "2026-11-10", "status": "allowed"},
            {"kind": "vacation", "bot_id": 7, "email": "olha@test.horilla", "reason": "personal",
             "start": "2026-11-11", "end": "2026-11-11", "status": "allowed"},
        ]
        results = self.put({"leaves": rows}).json()["leaves"]
        self.assertEqual([r["result"] for r in results], ["error", "created"])

    def test_sets_the_annual_balance(self):
        result = self.put({"balances": [{"email": "olha@test.horilla", "available": 12.5, "carryforward": 3}]}).json()
        self.assertEqual(result["balances"][0]["result"], "updated")
        balance = AvailableLeave.objects.get(employee_id=self.employee, leave_type_id__name="Annual Leave")
        self.assertEqual((balance.available_days, balance.carryforward_days, balance.total_leave_days), (12.5, 3, 15.5))

        again = self.put({"balances": [{"email": "olha@test.horilla", "available": 12.5, "carryforward": 3}]}).json()
        self.assertEqual(again["balances"][0]["result"], "unchanged")


class StartOnboardingTest(TestCase):
    """SOF-2176: an employee hired without recruitment gets onboarding from the profile."""

    @classmethod
    def setUpTestData(cls):
        from employee.models import EmployeeWorkInformation
        from horilla.testkit import make_user
        from horilla.testkit.factories import make_job_position
        from onboarding.models import OnboardingStage, OnboardingTask
        from scg_overrides.direct_onboarding import standing_recruitment

        cls.company = make_company("Onboarding Co")
        cls.hr_user = make_user("onb_hr", is_superuser=True)
        cls.hr = make_employee(company=cls.company, email="hr@test.horilla", user=cls.hr_user)
        cls.newcomer = make_employee(company=cls.company, email="new@test.horilla", first_name="Olha")
        cls.position = make_job_position(title="Crewing Manager")
        EmployeeWorkInformation.objects.filter(employee_id=cls.newcomer).update(job_position_id=cls.position)

        cls.recruitment = standing_recruitment(cls.company)
        stage = OnboardingStage.objects.create(stage_title="IT", recruitment_id=cls.recruitment, sequence=1)
        cls.tasks = []
        for title in ("Почта", "Бот"):
            task = OnboardingTask.objects.create(task_title=title, stage_id=stage)
            task.employee_id.set([cls.hr])
            cls.tasks.append(task)

    def setUp(self):
        from scg_overrides import direct_onboarding

        self.real_mail = direct_onboarding.mail_newcomer
        self.client.force_login(self.hr_user)
        patcher = mock.patch("scg_overrides.direct_onboarding.mail_newcomer", return_value=True)
        self.mail = patcher.start()
        self.addCleanup(patcher.stop)

    def start(self, employee=None):
        return self.client.post(f"/scg/start-onboarding/{(employee or self.newcomer).pk}/")

    def candidate(self):
        from recruitment.models import Candidate

        return Candidate.objects.get(converted_employee_id=self.newcomer)

    def test_links_the_employee_and_gives_every_task(self):
        from onboarding.models import CandidateStage, CandidateTask

        self.start()
        candidate = self.candidate()
        self.assertEqual(candidate.recruitment_id, self.recruitment)
        self.assertEqual(candidate.job_position_id, self.position)
        self.assertFalse(candidate.hired)
        # Not the automatic empty "Initial" stage: the first one with tasks
        self.assertEqual(CandidateStage.objects.get(candidate_id=candidate).onboarding_stage_id, self.tasks[0].stage_id)
        self.assertEqual(
            set(CandidateTask.objects.filter(candidate_id=candidate).values_list("onboarding_task_id", flat=True)),
            {t.pk for t in self.tasks},
        )
        self.assertIn(candidate, self.tasks[0].candidates.all())
        self.mail.assert_called_once()

    def test_the_standing_recruitment_stays_out_of_recruitment(self):
        self.assertTrue(self.recruitment.closed)
        self.assertFalse(self.recruitment.is_published)

    def test_the_onboarding_pipeline_shows_it_once_someone_is_in_it(self):
        from onboarding.models import OnboardingStage

        OnboardingStage.objects.create(stage_title="Empty", recruitment_id=self.recruitment, sequence=2)
        url = "/onboarding/cbv-pipeline-tab/"
        self.assertNotContains(self.client.get(url, HTTP_HX_REQUEST="true"), "Онбординг без рекрутингу")
        self.start()
        # Only the first of its stages holds the newcomer; the others are empty
        self.assertContains(self.client.get(url, HTTP_HX_REQUEST="true"), "Онбординг без рекрутингу")

    def test_task_blocks_fill_it_but_never_offer_it_as_a_block(self):
        from scg_overrides.onboarding_blocks import TaskBlockCopyForm

        form = TaskBlockCopyForm()
        self.assertIn(self.recruitment, form.fields["target"].queryset)
        self.assertNotIn(self.recruitment, form.fields["sources"].queryset)

    def test_pressing_again_duplicates_nothing(self):
        from onboarding.models import CandidateTask

        self.start()
        self.start()
        self.assertEqual(CandidateTask.objects.filter(candidate_id=self.candidate()).count(), 2)
        self.mail.assert_called_once()

    def test_a_task_added_later_is_given_on_the_next_press(self):
        from onboarding.models import CandidateTask, OnboardingTask

        self.start()
        OnboardingTask.objects.create(task_title="Пропуск", stage_id=self.tasks[0].stage_id)
        self.start()
        self.assertEqual(CandidateTask.objects.filter(candidate_id=self.candidate()).count(), 3)

    def test_needs_a_job_position(self):
        from recruitment.models import Candidate

        other = make_employee(company=self.company, email="nopos@test.horilla")
        self.start(other)
        self.assertFalse(Candidate.objects.filter(converted_employee_id=other).exists())

    def test_needs_tasks_in_the_standing_recruitment(self):
        from onboarding.models import OnboardingTask
        from recruitment.models import Candidate

        OnboardingTask.objects.filter(stage_id__recruitment_id=self.recruitment).delete()
        self.start()
        self.assertFalse(Candidate.objects.filter(converted_employee_id=self.newcomer).exists())

    def test_only_post(self):
        self.assertEqual(self.client.get(f"/scg/start-onboarding/{self.newcomer.pk}/").status_code, 405)

    def test_needs_the_permission(self):
        from horilla.testkit import make_user
        from recruitment.models import Candidate

        plain = make_user("onb_plain")
        make_employee(company=self.company, email="plain@test.horilla", user=plain)
        self.client.force_login(plain)
        self.start()
        self.assertFalse(Candidate.objects.filter(converted_employee_id=self.newcomer).exists())

    def test_the_profile_offers_the_action(self):
        # The profile body is an htmx partial; a plain GET is refused with 405
        response = self.client.get(
            f"/employee/profile/employee/employee-view/{self.newcomer.pk}/", HTTP_HX_REQUEST="true"
        )
        self.assertContains(response, f"/scg/start-onboarding/{self.newcomer.pk}/")

    def test_the_mail_lists_the_tasks_and_the_profile(self):
        from django.test import RequestFactory

        from scg_overrides import direct_onboarding

        sent = []
        backend = mock.Mock(configuration=True, dynamic_from_email_with_display_name="SCG HR <hr@test.horilla>")
        request = RequestFactory().post("/", HTTP_HOST="hrm.test")
        with mock.patch.object(direct_onboarding, "ConfiguredEmailBackend", return_value=backend), \
                mock.patch.object(direct_onboarding, "send_employee_invitation", return_value=True) as invite, \
                mock.patch("django.core.mail.EmailMultiAlternatives.send", lambda msg: sent.append(msg) or 1):
            self.assertTrue(self.real_mail(request, self.newcomer, self.tasks))

        invite.assert_called_once()  # never signed in
        self.assertEqual(sent[0].to, ["new@test.horilla"])
        self.assertIn("Почта", sent[0].body)
        self.assertIn(f"http://hrm.test/employee/employee-profile/{self.newcomer.pk}/", sent[0].body)
        self.assertIn("встановити пароль", sent[0].body)
