"""The corporate bot's push endpoint (SOF-2138)."""

import json
import os
from datetime import date
from unittest import mock

from django.test import TestCase

from employee.models import Employee
from horilla.testkit import make_company, make_employee
from horilla.testkit.factories import make_job_position
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



@mock.patch.dict(os.environ, {"SCG_BOT_SYNC_TOKEN": TOKEN})
class EmployeeSyncTest(TestCase):
    """SOF-2189: the bot creates, links, updates and archives employees."""

    @classmethod
    def setUpTestData(cls):
        from base.models import Department, EmployeeShift

        cls.company = make_company("Employee Sync Co")
        cls.it = Department.objects.create(department="IT Development & Infrastructure")
        cls.hq = Department.objects.create(department="Headquarter")
        cls.shift = EmployeeShift.objects.create(employee_shift="Day Shift")
        cls.head = make_employee(
            company=cls.company, email="head@test.horilla", first_name="Yuriy",
            department=cls.it, shift=cls.shift,
        )
        cls.ceo = make_employee(company=cls.company, email="ceo@test.horilla", department=cls.hq)

    def put(self, *rows, leaves=()):
        response = self.client.put(
            "/scg/bot-sync/", json.dumps({"employees": list(rows), "leaves": list(leaves)}),
            content_type="application/json", HTTP_X_BOT_TOKEN=TOKEN,
        )
        return response.json()

    def row(self, **overrides):
        row = {
            "bot_id": 7, "horilla_id": None, "email": "Tetiana@Test.Horilla",
            "first_name": "Tetiana", "last_name": "Piatkivska", "phone": "+380501112233",
            "birthday": "1995-03-04", "hire_date": "2026-11-09",
            "position": "Software Support & QA Specialist",
            "department": "IT Development & Infrastructure",
            "manager_email": "head@test.horilla", "active": True, "reassign": False,
        }
        row.update(overrides)
        return row

    def work(self, employee_id):
        return Employee.objects.entire().get(pk=employee_id).employee_work_info

    def test_creates_the_employee_where_the_bot_put_them(self):
        result = self.put(self.row())["employees"][0]
        self.assertEqual(result["result"], "created")

        employee = Employee.objects.entire().get(pk=result["id"])
        self.assertEqual((employee.get_full_name(), employee.phone, str(employee.dob)),
                         ("Tetiana Piatkivska", "+380501112233", "1995-03-04"))
        self.assertIsNotNone(employee.employee_user_id)
        work = employee.employee_work_info
        self.assertEqual((work.department_id, work.reporting_manager_id, str(work.date_joining)),
                         (self.it, self.head, "2026-11-09"))
        self.assertEqual((work.job_position_id.job_position, work.job_position_id.department_id),
                         ("Software Support & QA Specialist", self.it))
        # Shift and company come from the manager's record
        self.assertEqual((work.shift_id, work.company_id), (self.shift, self.company))

        again = self.put(self.row(horilla_id=result["id"]))["employees"][0]
        self.assertEqual(again, {"bot_id": 7, "result": "unchanged", "id": result["id"]})

    def test_links_someone_hired_through_recruitment(self):
        hired = make_employee(company=self.company, email="tetiana@test.horilla", department=self.hq)
        result = self.put(self.row())["employees"][0]
        self.assertEqual((result["result"], result["id"]), ("linked", hired.pk))
        self.assertEqual(Employee.objects.entire().filter(email__iexact="tetiana@test.horilla").count(), 1)
        # Linking keeps the department HR gave them
        self.assertEqual(self.work(hired.pk).department_id, self.hq)

    def test_follows_an_email_change_by_the_kept_id(self):
        employee_id = self.put(self.row())["employees"][0]["id"]
        result = self.put(self.row(horilla_id=employee_id, email="tetiana.p@test.horilla"))["employees"][0]
        self.assertEqual(result["result"], "updated")
        employee = Employee.objects.entire().get(pk=employee_id)
        self.assertEqual((employee.email, employee.employee_user_id.email, employee.employee_user_id.username),
                         ("tetiana.p@test.horilla", "tetiana.p@test.horilla", "tetiana.p@test.horilla"))

    def test_keeps_the_org_chart_unless_the_department_changes(self):
        employee_id = self.put(self.row())["employees"][0]["id"]
        moved = self.row(horilla_id=employee_id, department="Headquarter", manager_email="ceo@test.horilla")

        self.put(moved)
        self.assertEqual((self.work(employee_id).department_id, self.work(employee_id).reporting_manager_id),
                         (self.it, self.head))

        self.put({**moved, "reassign": True})
        work = self.work(employee_id)
        self.assertEqual((work.department_id, work.reporting_manager_id), (self.hq, self.ceo))
        self.assertEqual(work.job_position_id.department_id, self.hq)

    def test_an_empty_value_in_the_bot_erases_nothing(self):
        employee_id = self.put(self.row())["employees"][0]["id"]
        self.put(self.row(horilla_id=employee_id, phone=None, birthday=None, hire_date=None, position=None))
        employee = Employee.objects.entire().get(pk=employee_id)
        self.assertEqual((employee.phone, str(employee.dob), str(employee.employee_work_info.date_joining)),
                         ("+380501112233", "1995-03-04", "2026-11-09"))
        self.assertIsNotNone(employee.employee_work_info.job_position_id)

    def test_reuses_the_department_position_in_any_letter_case(self):
        from base.models import JobPosition

        existing = make_job_position(title="QA Engineer", department=self.it)
        employee_id = self.put(self.row(position="qa  engineer"))["employees"][0]["id"]
        self.assertEqual(self.work(employee_id).job_position_id, existing)
        self.assertEqual(JobPosition.objects.filter(job_position__iexact="qa engineer").count(), 1)

    def test_a_dismissal_archives_even_a_manager(self):
        self.put(self.row())  # the head now manages someone, which blocks Horilla's own archive
        self.assertTrue(Employee.objects.entire().get(pk=self.head.pk).get_archive_condition())
        result = self.put(self.row(email="head@test.horilla", horilla_id=self.head.pk,
                                   manager_email="ceo@test.horilla", active=False))["employees"][0]
        self.assertEqual(result["result"], "updated")
        head = Employee.objects.entire().get(pk=self.head.pk)
        self.assertFalse(head.is_active)
        self.assertFalse(head.employee_user_id.is_active)

        self.put(self.row(email="head@test.horilla", horilla_id=self.head.pk,
                          manager_email="ceo@test.horilla", active=True))
        self.assertTrue(Employee.objects.entire().get(pk=self.head.pk).is_active)

    def test_leaves_out_who_horilla_should_not_have(self):
        dismissed = self.put(self.row(active=False))["employees"][0]
        outsider = self.put(self.row(bot_id=8, email="admin@outsource.test", department=None))["employees"][0]
        self.assertEqual((dismissed["result"], outsider["result"]), ("skipped", "skipped"))
        self.assertFalse(Employee.objects.entire().filter(email__iexact="tetiana@test.horilla").exists())

    def test_a_leave_in_the_same_batch_finds_the_new_employee(self):
        LeaveType.objects.create(name="Annual Leave", total_days=20, payment="paid", reset=False)
        leave = {"kind": "vacation", "bot_id": 1, "email": "tetiana@test.horilla", "reason": "planned_vacation",
                 "start": "2026-12-01", "end": "2026-12-02", "status": "allowed"}
        result = self.put(self.row(), leaves=[leave])
        self.assertEqual((result["employees"][0]["result"], result["leaves"][0]["result"]), ("created", "created"))

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
