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
            LeaveType.objects.create(
                name=name, total_days=20, payment="paid", reset=False
            )

    def put(self, payload, token=TOKEN):
        response = self.client.put(
            "/scg/bot-sync/",
            json.dumps(payload),
            content_type="application/json",
            HTTP_X_BOT_TOKEN=token,
        )
        return response

    def leave(self, **overrides):
        row = {
            "kind": "vacation",
            "bot_id": 2300,
            "email": "olha@test.horilla",
            "reason": "planned_vacation",
            "start": "2026-11-10",
            "end": "2026-11-12",
            "day_part": "full_day",
            "status": "allowed",
            "note": "море",
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
        self.assertEqual(
            (leave.start_date, leave.end_date), (date(2026, 11, 10), date(2026, 11, 12))
        )
        self.assertEqual(leave.description, "[bot vacation #2300] | море")

    def test_updates_dates_and_cancels(self):
        self.leave()
        self.assertEqual(self.leave(end="2026-11-13")["result"], "updated")
        self.assertEqual(self.stored().end_date, date(2026, 11, 13))

        self.assertEqual(
            self.leave(end="2026-11-13", status="cancelled")["result"], "updated"
        )
        self.assertEqual(self.stored().status, "cancelled")

    def test_cancels_a_record_deleted_in_the_bot(self):
        self.leave()
        result = self.put(
            {"leaves": [{"kind": "vacation", "bot_id": 2300, "status": "cancelled"}]}
        ).json()
        self.assertEqual(result["leaves"][0]["result"], "updated")
        self.assertEqual(self.stored().status, "cancelled")
        self.assertEqual(self.stored().end_date, date(2026, 11, 12))

    def test_never_creates_what_the_bot_has_not_approved(self):
        self.assertEqual(self.leave(status="denied")["result"], "skipped")
        self.assertEqual(self.leave(status="pending")["result"], "skipped")
        self.assertFalse(
            LeaveRequest.objects.filter(
                description__startswith="[bot vacation #2300]"
            ).exists()
        )

    def test_half_day_only_on_a_single_day(self):
        self.leave(bot_id=1, start="2026-11-10", end="2026-11-10", day_part="pm")
        self.assertEqual(
            self.stored("[bot vacation #1]").start_date_breakdown, "second_half"
        )
        self.leave(bot_id=2, day_part="am")
        self.assertEqual(
            self.stored("[bot vacation #2]").start_date_breakdown, "full_day"
        )

    def test_maps_reasons_and_sick_leaves(self):
        self.leave(bot_id=3, reason="personal")
        self.assertEqual(self.stored("[bot vacation #3]").leave_type_id.name, "Day Off")
        self.leave(kind="sick_leave", bot_id=4, reason="sick_leave")
        self.assertEqual(
            self.stored("[bot sick_leave #4]").leave_type_id.name, "Sick Leave"
        )

    def test_creates_the_birthday_type_on_first_use(self):
        self.assertEqual(
            self.leave(
                bot_id=5, reason="birthday", start="2026-11-10", end="2026-11-10"
            )["result"],
            "created",
        )
        birthday = LeaveType.objects.get(name="Birthday Day Off")
        self.assertFalse(birthday.reset)
        self.assertEqual(birthday.total_days, 1)

    def test_reports_an_unknown_employee(self):
        self.assertEqual(
            self.leave(email="ghost@test.horilla")["result"], "unknown_employee"
        )

    def test_one_bad_row_does_not_stop_the_batch(self):
        rows = [
            {
                "kind": "vacation",
                "bot_id": 6,
                "email": "olha@test.horilla",
                "reason": "nonsense",
                "start": "2026-11-10",
                "end": "2026-11-10",
                "status": "allowed",
            },
            {
                "kind": "vacation",
                "bot_id": 7,
                "email": "olha@test.horilla",
                "reason": "personal",
                "start": "2026-11-11",
                "end": "2026-11-11",
                "status": "allowed",
            },
        ]
        results = self.put({"leaves": rows}).json()["leaves"]
        self.assertEqual([r["result"] for r in results], ["error", "created"])

    def test_sets_the_annual_balance(self):
        result = self.put(
            {
                "balances": [
                    {"email": "olha@test.horilla", "available": 12.5, "carryforward": 3}
                ]
            }
        ).json()
        self.assertEqual(result["balances"][0]["result"], "updated")
        balance = AvailableLeave.objects.get(
            employee_id=self.employee, leave_type_id__name="Annual Leave"
        )
        self.assertEqual(
            (
                balance.available_days,
                balance.carryforward_days,
                balance.total_leave_days,
            ),
            (12.5, 3, 15.5),
        )

        again = self.put(
            {
                "balances": [
                    {"email": "olha@test.horilla", "available": 12.5, "carryforward": 3}
                ]
            }
        ).json()
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
            company=cls.company,
            email="head@test.horilla",
            first_name="Yuriy",
            department=cls.it,
            shift=cls.shift,
        )
        cls.ceo = make_employee(
            company=cls.company, email="ceo@test.horilla", department=cls.hq
        )

    def put(self, *rows, leaves=()):
        response = self.client.put(
            "/scg/bot-sync/",
            json.dumps({"employees": list(rows), "leaves": list(leaves)}),
            content_type="application/json",
            HTTP_X_BOT_TOKEN=TOKEN,
        )
        return response.json()

    def row(self, **overrides):
        row = {
            "bot_id": 7,
            "horilla_id": None,
            "email": "Tetiana@Test.Horilla",
            "first_name": "Tetiana",
            "last_name": "Piatkivska",
            "phone": "+380501112233",
            "birthday": "1995-03-04",
            "hire_date": "2026-11-09",
            "position": "Software Support & QA Specialist",
            "department": "IT Development & Infrastructure",
            "manager_email": "head@test.horilla",
            "active": True,
            "reassign": False,
        }
        row.update(overrides)
        return row

    def work(self, employee_id):
        return Employee.objects.entire().get(pk=employee_id).employee_work_info

    def test_creates_the_employee_where_the_bot_put_them(self):
        result = self.put(self.row())["employees"][0]
        self.assertEqual(result["result"], "created")

        employee = Employee.objects.entire().get(pk=result["id"])
        self.assertEqual(
            (employee.get_full_name(), employee.phone, str(employee.dob)),
            ("Tetiana Piatkivska", "+380501112233", "1995-03-04"),
        )
        self.assertIsNotNone(employee.employee_user_id)
        work = employee.employee_work_info
        self.assertEqual(
            (work.department_id, work.reporting_manager_id, str(work.date_joining)),
            (self.it, self.head, "2026-11-09"),
        )
        self.assertEqual(
            (work.job_position_id.job_position, work.job_position_id.department_id),
            ("Software Support & QA Specialist", self.it),
        )
        # Shift and company come from the manager's record
        self.assertEqual((work.shift_id, work.company_id), (self.shift, self.company))

        again = self.put(self.row(horilla_id=result["id"]))["employees"][0]
        self.assertEqual(
            again, {"bot_id": 7, "result": "unchanged", "id": result["id"]}
        )

    def test_moves_the_employee_under_a_new_manager_at_any_time(self):
        """SOF-2120: the bot owns the reporting line, not only on creation."""
        employee_id = self.put(self.row())["employees"][0]["id"]
        result = self.put(
            self.row(horilla_id=employee_id, manager_email="ceo@test.horilla")
        )["employees"][0]
        self.assertEqual(result["result"], "updated")
        self.assertEqual(self.work(employee_id).reporting_manager_id, self.ceo)
        # The department stays where HR keeps it unless the bot reassigns
        self.assertEqual(self.work(employee_id).department_id, self.it)

        again = self.put(
            self.row(horilla_id=employee_id, manager_email="nobody@test.horilla")
        )["employees"][0]
        self.assertEqual(again["result"], "unchanged")
        self.assertEqual(self.work(employee_id).reporting_manager_id, self.ceo)

    def test_lists_the_org_chart_for_the_bot(self):
        employee_id = self.put(self.row())["employees"][0]["id"]
        self.assertEqual(self.client.get("/scg/bot-employees/").status_code, 403)
        response = self.client.get("/scg/bot-employees/", HTTP_X_BOT_TOKEN=TOKEN)
        rows = {r["id"]: r for r in response.json()["employees"]}
        self.assertEqual(rows[employee_id]["manager_id"], self.head.pk)
        self.assertEqual(rows[employee_id]["manager_email"], "head@test.horilla")
        self.assertEqual(
            rows[employee_id]["department"], "IT Development & Infrastructure"
        )
        self.assertEqual(
            rows[employee_id]["position"], "Software Support & QA Specialist"
        )
        self.assertTrue(rows[employee_id]["active"])
        self.assertIsNone(rows[self.ceo.pk]["manager_id"])

    def test_links_someone_hired_through_recruitment(self):
        hired = make_employee(
            company=self.company, email="tetiana@test.horilla", department=self.hq
        )
        result = self.put(self.row())["employees"][0]
        self.assertEqual((result["result"], result["id"]), ("linked", hired.pk))
        self.assertEqual(
            Employee.objects.entire()
            .filter(email__iexact="tetiana@test.horilla")
            .count(),
            1,
        )
        # Linking keeps the department HR gave them
        self.assertEqual(self.work(hired.pk).department_id, self.hq)

    def test_follows_an_email_change_by_the_kept_id(self):
        employee_id = self.put(self.row())["employees"][0]["id"]
        result = self.put(
            self.row(horilla_id=employee_id, email="tetiana.p@test.horilla")
        )["employees"][0]
        self.assertEqual(result["result"], "updated")
        employee = Employee.objects.entire().get(pk=employee_id)
        self.assertEqual(
            (
                employee.email,
                employee.employee_user_id.email,
                employee.employee_user_id.username,
            ),
            (
                "tetiana.p@test.horilla",
                "tetiana.p@test.horilla",
                "tetiana.p@test.horilla",
            ),
        )

    def test_a_change_of_letter_case_keeps_the_email_and_the_login(self):
        employee_id = self.put(self.row())["employees"][0]["id"]
        result = self.put(
            self.row(horilla_id=employee_id, email="TETIANA@test.horilla")
        )["employees"][0]
        self.assertEqual(result["result"], "unchanged")
        employee = Employee.objects.entire().get(pk=employee_id)
        self.assertEqual(
            (employee.email, employee.employee_user_id.username),
            ("Tetiana@Test.Horilla", "Tetiana@Test.Horilla"),
        )

    def test_keeps_the_department_unless_the_bot_reassigns(self):
        """The manager follows the bot at once (SOF-2120); the department
        only with `reassign` (SOF-2189)."""
        employee_id = self.put(self.row())["employees"][0]["id"]
        moved = self.row(
            horilla_id=employee_id,
            department="Headquarter",
            manager_email="ceo@test.horilla",
        )

        self.put(moved)
        self.assertEqual(
            (
                self.work(employee_id).department_id,
                self.work(employee_id).reporting_manager_id,
            ),
            (self.it, self.ceo),
        )

        self.put({**moved, "reassign": True})
        work = self.work(employee_id)
        self.assertEqual(
            (work.department_id, work.reporting_manager_id), (self.hq, self.ceo)
        )
        self.assertEqual(work.job_position_id.department_id, self.hq)

    def test_an_empty_value_in_the_bot_erases_nothing(self):
        employee_id = self.put(self.row())["employees"][0]["id"]
        self.put(
            self.row(
                horilla_id=employee_id,
                phone=None,
                birthday=None,
                hire_date=None,
                position=None,
            )
        )
        employee = Employee.objects.entire().get(pk=employee_id)
        self.assertEqual(
            (
                employee.phone,
                str(employee.dob),
                str(employee.employee_work_info.date_joining),
            ),
            ("+380501112233", "1995-03-04", "2026-11-09"),
        )
        self.assertIsNotNone(employee.employee_work_info.job_position_id)

    def test_gender_fills_an_empty_one_and_never_replaces_horillas(self):
        """SOF-2196: the bot's gender is for a new employee only."""
        employee_id = self.put(self.row(gender="female"))["employees"][0]["id"]
        employee = Employee.objects.entire().get(pk=employee_id)
        self.assertEqual(employee.gender, "female")

        # HR corrects it in Horilla: the bot's value does not come back
        Employee.objects.entire().filter(pk=employee_id).update(gender="other")
        self.put(self.row(horilla_id=employee_id, gender="female"))
        self.assertEqual(Employee.objects.entire().get(pk=employee_id).gender, "other")

        # No gender from the bot erases nothing; an empty one in Horilla is filled
        self.put(self.row(horilla_id=employee_id, gender=None))
        self.assertEqual(Employee.objects.entire().get(pk=employee_id).gender, "other")
        Employee.objects.entire().filter(pk=employee_id).update(gender=None)
        self.put(self.row(horilla_id=employee_id, gender="female"))
        self.assertEqual(Employee.objects.entire().get(pk=employee_id).gender, "female")

    def test_a_new_employee_without_gender_stays_without(self):
        employee_id = self.put(self.row())["employees"][0]["id"]
        self.assertIsNone(Employee.objects.entire().get(pk=employee_id).gender)

    def test_reuses_the_department_position_in_any_letter_case(self):
        from base.models import JobPosition

        existing = make_job_position(title="QA Engineer", department=self.it)
        employee_id = self.put(self.row(position="qa  engineer"))["employees"][0]["id"]
        self.assertEqual(self.work(employee_id).job_position_id, existing)
        self.assertEqual(
            JobPosition.objects.filter(job_position__iexact="qa engineer").count(), 1
        )

    def test_a_dismissal_archives_even_a_manager(self):
        self.put(
            self.row()
        )  # the head now manages someone, which blocks Horilla's own archive
        self.assertTrue(
            Employee.objects.entire().get(pk=self.head.pk).get_archive_condition()
        )
        result = self.put(
            self.row(
                email="head@test.horilla",
                horilla_id=self.head.pk,
                manager_email="ceo@test.horilla",
                active=False,
            )
        )["employees"][0]
        self.assertEqual(result["result"], "updated")
        head = Employee.objects.entire().get(pk=self.head.pk)
        self.assertFalse(head.is_active)
        self.assertFalse(head.employee_user_id.is_active)

        self.put(
            self.row(
                email="head@test.horilla",
                horilla_id=self.head.pk,
                manager_email="ceo@test.horilla",
                active=True,
            )
        )
        self.assertTrue(Employee.objects.entire().get(pk=self.head.pk).is_active)

    def test_leaves_out_who_horilla_should_not_have(self):
        dismissed = self.put(self.row(active=False))["employees"][0]
        outsider = self.put(
            self.row(bot_id=8, email="admin@outsource.test", department=None)
        )["employees"][0]
        self.assertEqual(
            (dismissed["result"], outsider["result"]), ("skipped", "skipped")
        )
        self.assertFalse(
            Employee.objects.entire()
            .filter(email__iexact="tetiana@test.horilla")
            .exists()
        )

    def test_a_leave_in_the_same_batch_finds_the_new_employee(self):
        LeaveType.objects.create(
            name="Annual Leave", total_days=20, payment="paid", reset=False
        )
        leave = {
            "kind": "vacation",
            "bot_id": 1,
            "email": "tetiana@test.horilla",
            "reason": "planned_vacation",
            "start": "2026-12-01",
            "end": "2026-12-02",
            "status": "allowed",
        }
        result = self.put(self.row(), leaves=[leave])
        self.assertEqual(
            (result["employees"][0]["result"], result["leaves"][0]["result"]),
            ("created", "created"),
        )


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
        cls.hr = make_employee(
            company=cls.company, email="hr@test.horilla", user=cls.hr_user
        )
        cls.newcomer = make_employee(
            company=cls.company, email="new@test.horilla", first_name="Olha"
        )
        cls.position = make_job_position(title="Crewing Manager")
        EmployeeWorkInformation.objects.filter(employee_id=cls.newcomer).update(
            job_position_id=cls.position
        )

        cls.recruitment = standing_recruitment(cls.company)
        stage = OnboardingStage.objects.create(
            stage_title="IT", recruitment_id=cls.recruitment, sequence=1
        )
        cls.tasks = []
        for title in ("Почта", "Бот"):
            task = OnboardingTask.objects.create(task_title=title, stage_id=stage)
            task.employee_id.set([cls.hr])
            cls.tasks.append(task)

    def setUp(self):
        from scg_overrides import direct_onboarding

        self.real_mail = direct_onboarding.mail_newcomer
        self.client.force_login(self.hr_user)
        patcher = mock.patch(
            "scg_overrides.direct_onboarding.mail_newcomer", return_value=True
        )
        self.mail = patcher.start()
        self.addCleanup(patcher.stop)

    def start(self, employee=None):
        return self.client.post(
            f"/scg/start-onboarding/{(employee or self.newcomer).pk}/"
        )

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
        self.assertEqual(
            CandidateStage.objects.get(candidate_id=candidate).onboarding_stage_id,
            self.tasks[0].stage_id,
        )
        self.assertEqual(
            set(
                CandidateTask.objects.filter(candidate_id=candidate).values_list(
                    "onboarding_task_id", flat=True
                )
            ),
            {t.pk for t in self.tasks},
        )
        self.assertIn(candidate, self.tasks[0].candidates.all())
        self.mail.assert_called_once()

    def test_the_standing_recruitment_stays_out_of_recruitment(self):
        self.assertTrue(self.recruitment.closed)
        self.assertFalse(self.recruitment.is_published)

    def test_the_onboarding_pipeline_shows_it_once_someone_is_in_it(self):
        from onboarding.models import OnboardingStage

        OnboardingStage.objects.create(
            stage_title="Empty", recruitment_id=self.recruitment, sequence=2
        )
        url = "/onboarding/cbv-pipeline-tab/"
        self.assertNotContains(
            self.client.get(url, HTTP_HX_REQUEST="true"), "Онбординг без рекрутингу"
        )
        self.start()
        # Only the first of its stages holds the newcomer; the others are empty
        self.assertContains(
            self.client.get(url, HTTP_HX_REQUEST="true"), "Онбординг без рекрутингу"
        )

    def test_task_blocks_fill_it_but_never_offer_it_as_a_block(self):
        from scg_overrides.onboarding_blocks import TaskBlockCopyForm

        form = TaskBlockCopyForm()
        self.assertIn(self.recruitment, form.fields["target"].queryset)
        self.assertNotIn(self.recruitment, form.fields["sources"].queryset)

    def test_pressing_again_duplicates_nothing(self):
        from onboarding.models import CandidateTask

        self.start()
        self.start()
        self.assertEqual(
            CandidateTask.objects.filter(candidate_id=self.candidate()).count(), 2
        )
        self.mail.assert_called_once()

    def test_a_task_added_later_is_given_on_the_next_press(self):
        from onboarding.models import CandidateTask, OnboardingTask

        self.start()
        OnboardingTask.objects.create(
            task_title="Пропуск", stage_id=self.tasks[0].stage_id
        )
        self.start()
        self.assertEqual(
            CandidateTask.objects.filter(candidate_id=self.candidate()).count(), 3
        )

    def test_needs_a_job_position(self):
        from recruitment.models import Candidate

        other = make_employee(company=self.company, email="nopos@test.horilla")
        self.start(other)
        self.assertFalse(Candidate.objects.filter(converted_employee_id=other).exists())

    def test_needs_tasks_in_the_standing_recruitment(self):
        from onboarding.models import OnboardingTask
        from recruitment.models import Candidate

        OnboardingTask.objects.filter(
            stage_id__recruitment_id=self.recruitment
        ).delete()
        self.start()
        self.assertFalse(
            Candidate.objects.filter(converted_employee_id=self.newcomer).exists()
        )

    def test_only_post(self):
        self.assertEqual(
            self.client.get(f"/scg/start-onboarding/{self.newcomer.pk}/").status_code,
            405,
        )

    def test_needs_the_permission(self):
        from horilla.testkit import make_user
        from recruitment.models import Candidate

        plain = make_user("onb_plain")
        make_employee(company=self.company, email="plain@test.horilla", user=plain)
        self.client.force_login(plain)
        self.start()
        self.assertFalse(
            Candidate.objects.filter(converted_employee_id=self.newcomer).exists()
        )

    def test_the_profile_offers_the_action(self):
        # The profile body is an htmx partial; a plain GET is refused with 405
        response = self.client.get(
            f"/employee/profile/employee/employee-view/{self.newcomer.pk}/",
            HTTP_HX_REQUEST="true",
        )
        self.assertContains(response, f"/scg/start-onboarding/{self.newcomer.pk}/")

    def test_the_mail_lists_the_tasks_and_the_profile(self):
        from django.test import RequestFactory

        from scg_overrides import direct_onboarding

        sent = []
        backend = mock.Mock(
            configuration=True,
            dynamic_from_email_with_display_name="SCG HR <hr@test.horilla>",
        )
        request = RequestFactory().post("/", HTTP_HOST="hrm.test")
        with mock.patch.object(
            direct_onboarding, "ConfiguredEmailBackend", return_value=backend
        ), mock.patch.object(
            direct_onboarding, "send_employee_invitation", return_value=True
        ) as invite, mock.patch(
            "django.core.mail.EmailMultiAlternatives.send",
            lambda msg: sent.append(msg) or 1,
        ):
            self.assertTrue(self.real_mail(request, self.newcomer, self.tasks))

        invite.assert_called_once()  # never signed in
        self.assertEqual(sent[0].to, ["new@test.horilla"])
        self.assertIn("Почта", sent[0].body)
        self.assertIn(
            f"http://hrm.test/employee/employee-profile/{self.newcomer.pk}/",
            sent[0].body,
        )
        self.assertIn("встановити пароль", sent[0].body)


class TaskLinkTest(TestCase):
    """SOF-2140: an onboarding task points to the document it is about."""

    POLICY = "https://docs.google.com/document/d/vacation-policy"

    @classmethod
    def setUpTestData(cls):
        from horilla.testkit import make_user
        from onboarding.models import OnboardingStage, OnboardingTask
        from recruitment.models import Recruitment

        cls.company = make_company("Task Link Co")
        cls.hr_user = make_user("link_hr", is_superuser=True)
        cls.hr = make_employee(
            company=cls.company, email="hr@link.horilla", user=cls.hr_user
        )
        cls.library = Recruitment.objects.create(
            title="Шаблон: Перші кроки",
            closed=True,
            is_published=False,
            vacancy=0,
            company_id=cls.company,
        )
        cls.stage = OnboardingStage.objects.create(
            stage_title="Перші кроки", recruitment_id=cls.library, sequence=1
        )
        cls.task = OnboardingTask.objects.create(
            task_title="Політика відпусток", stage_id=cls.stage
        )
        cls.task.employee_id.set([cls.hr])

    def setUp(self):
        self.client.force_login(self.hr_user)

    def update(self, link, title="Політика відпусток"):
        return self.client.post(
            f"/onboarding/task-update/{self.task.pk}/",
            {
                "task_title": title,
                "stage_id": self.stage.pk,
                "employee_id": [self.hr.pk],
                "scg_link": link,
            },
            HTTP_HX_REQUEST="true",
        )

    def test_the_task_form_sets_changes_and_drops_the_link(self):
        from scg_overrides.task_details import link_of

        self.update(self.POLICY)
        self.assertEqual(link_of(self.task), self.POLICY)

        form = self.client.get(
            f"/onboarding/task-update/{self.task.pk}/", HTTP_HX_REQUEST="true"
        )
        self.assertContains(form, self.POLICY)

        self.update("docs.google.com/document/d/new")
        self.assertEqual(link_of(self.task), "https://docs.google.com/document/d/new")

        self.update("")
        self.assertEqual(link_of(self.task), "")

    def test_a_bad_link_is_refused(self):
        from scg_overrides.task_details import link_of

        self.update("not a link")
        self.assertEqual(link_of(self.task), "")

    def test_a_new_task_gets_its_link(self):
        from onboarding.models import OnboardingTask
        from scg_overrides.task_details import link_of

        self.client.post(
            f"/onboarding/task-creation/{self.stage.pk}/",
            {
                "task_title": "Оргструктура",
                "stage_id": self.stage.pk,
                "managers": [self.hr.pk],
                "scg_link": "https://sites.google.com/view/org",
            },
            HTTP_HX_REQUEST="true",
        )
        task = OnboardingTask.objects.get(task_title="Оргструктура")
        self.assertEqual(link_of(task), "https://sites.google.com/view/org")

    def test_a_save_outside_the_form_keeps_the_link(self):
        from scg_overrides.task_details import link_of, set_link

        set_link(self.task, self.POLICY)
        self.task.task_title = "Політика відпусток і лікарняних"
        self.task.save()
        self.assertEqual(link_of(self.task), self.POLICY)

    def test_task_blocks_copy_the_link(self):
        from onboarding.models import OnboardingTask
        from recruitment.models import Recruitment
        from scg_overrides.onboarding_blocks import _copy_blocks
        from scg_overrides.task_details import link_of, set_link

        set_link(self.task, self.POLICY)
        target = Recruitment.objects.create(
            title="Онбординг", vacancy=0, company_id=self.company
        )
        _copy_blocks(target, [self.library])
        copy = OnboardingTask.objects.get(
            stage_id__recruitment_id=target, task_title="Політика відпусток"
        )
        self.assertEqual(link_of(copy), self.POLICY)

    def test_the_onboarding_tab_and_the_mail_open_the_document(self):
        from onboarding.models import CandidateTask
        from scg_overrides.task_details import set_link

        set_link(self.task, self.POLICY)
        candidate_task = CandidateTask(onboarding_task_id=self.task)
        self.assertIn(f'href="{self.POLICY}"', candidate_task.scg_task_col())

        from django.template.loader import render_to_string

        html = render_to_string(
            "scg_overrides/mail/start_onboarding.html",
            {"employee": self.hr, "tasks": [self.task], "profile_url": "x"},
        )
        text = render_to_string(
            "scg_overrides/mail/start_onboarding.txt",
            {"employee": self.hr, "tasks": [self.task], "profile_url": "x"},
        )
        self.assertIn(f'href="{self.POLICY}"', html)
        self.assertIn(f"Політика відпусток — {self.POLICY}", text)


class OffboardingTest(TestCase):
    """SOF-2175: a leaver gets the whole checklist, and the archived stage archives."""

    @classmethod
    def setUpTestData(cls):
        from employee.models import EmployeeWorkInformation
        from horilla.testkit import make_user
        from offboarding.models import Offboarding, OffboardingStage, OffboardingTask

        cls.company = make_company("Offboarding Co")
        cls.hr_user = make_user("off_hr", is_superuser=True)
        cls.hr = make_employee(
            company=cls.company, email="hr@off.horilla", user=cls.hr_user
        )
        cls.it = make_employee(company=cls.company, email="it@off.horilla")
        cls.leaver = make_employee(
            company=cls.company, email="leaver@off.horilla", first_name="Olena"
        )
        cls.report = make_employee(company=cls.company, email="report@off.horilla")
        # The leaver manages someone: Employee.save() would refuse to archive them
        EmployeeWorkInformation.objects.filter(employee_id=cls.report).update(
            reporting_manager_id=cls.leaver
        )

        cls.process = Offboarding.objects.create(
            title="Звільнення", description="-", company_id=cls.company
        )
        stages = OffboardingStage.objects.filter(offboarding_id=cls.process)
        cls.notice = stages.get(type="notice_period")
        cls.handover = stages.get(type="handover")
        cls.archived = stages.get(type="archived")
        cls.tasks = []
        for title, manager in (
            ("Повернути техніку", cls.it),
            ("Exit-інтерв'ю", cls.hr),
        ):
            task = OffboardingTask.objects.create(title=title, stage_id=cls.handover)
            task.managers.set([manager])
            cls.tasks.append(task)

    def setUp(self):
        self.client.force_login(self.hr_user)

    def add(self, employee=None, stage=None):
        from offboarding.models import OffboardingEmployee

        return OffboardingEmployee.objects.create(
            employee_id=employee or self.leaver, stage_id=stage or self.notice
        )

    def test_a_leaver_gets_every_task_of_the_process_once(self):
        from offboarding.models import EmployeeTask

        leaver = self.add()
        self.assertEqual(
            set(
                EmployeeTask.objects.filter(employee_id=leaver).values_list(
                    "task_id", flat=True
                )
            ),
            {task.pk for task in self.tasks},
        )
        leaver.save()
        self.assertEqual(EmployeeTask.objects.filter(employee_id=leaver).count(), 2)

    def test_a_task_added_later_goes_to_everyone_still_leaving(self):
        from offboarding.models import EmployeeTask, OffboardingTask

        leaving = self.add()
        gone = self.add(employee=self.report, stage=self.archived)
        task = OffboardingTask.objects.create(
            title="Закрити доступи й пошту", stage_id=self.handover
        )
        self.assertTrue(
            EmployeeTask.objects.filter(employee_id=leaving, task_id=task).exists()
        )
        self.assertFalse(
            EmployeeTask.objects.filter(employee_id=gone, task_id=task).exists()
        )

    def test_the_archived_stage_archives_even_a_manager(self):
        leaver = self.add()
        response = self.client.get(
            "/offboarding/offboarding-change-stage/",
            {"employee_ids": [leaver.pk], "stage_id": self.archived.pk},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        employee = Employee.objects.entire().get(pk=self.leaver.pk)
        self.assertFalse(employee.is_active)
        self.assertFalse(employee.employee_user_id.is_active)

        self.client.get(
            "/offboarding/offboarding-change-stage/",
            {"employee_ids": [leaver.pk], "stage_id": self.handover.pk},
            HTTP_HX_REQUEST="true",
        )
        self.assertTrue(Employee.objects.entire().get(pk=self.leaver.pk).is_active)

    def test_the_task_form_keeps_link_description_and_performer(self):
        from offboarding.models import OffboardingTask
        from scg_overrides.task_details import details_of

        self.client.post(
            f"/offboarding/offboarding-add-task/?stage_id={self.handover.pk}",
            {
                "title": "Передати справи",
                "stage_id": self.handover.pk,
                "managers": [self.hr.pk],
                "scg_link": "https://docs.google.com/document/d/handover",
                "scg_description": "Список проєктів",
                "scg_for_subject": "on",
            },
            HTTP_HX_REQUEST="true",
        )
        task = OffboardingTask.objects.get(title="Передати справи")
        self.assertEqual(
            details_of(task),
            {
                "url": "https://docs.google.com/document/d/handover",
                "description": "Список проєктів",
                "for_subject": True,
            },
        )


@mock.patch.dict(os.environ, {"SCG_BOT_SYNC_TOKEN": TOKEN})
class BotTasksTest(TestCase):
    """SOF-2190: the bot pulls onboarding and offboarding tasks and pushes statuses back."""

    @classmethod
    def setUpTestData(cls):
        from offboarding.models import (
            Offboarding,
            OffboardingEmployee,
            OffboardingStage,
            OffboardingTask,
        )
        from onboarding.models import CandidateTask, OnboardingStage, OnboardingTask
        from recruitment.models import Candidate
        from scg_overrides.direct_onboarding import standing_recruitment
        from scg_overrides.task_details import set_details

        company = make_company("Bot Tasks Co")
        cls.it = make_employee(company=company, email="it@tasks.horilla")
        cls.newcomer = make_employee(
            company=company, email="new@tasks.horilla", first_name="Tetiana"
        )
        cls.leaver = make_employee(
            company=company, email="leaver@tasks.horilla", first_name="Olena"
        )

        recruitment = standing_recruitment(company)
        stage = OnboardingStage.objects.create(
            stage_title="IT", recruitment_id=recruitment, sequence=1
        )
        laptop = OnboardingTask.objects.create(
            task_title="Видати ноутбук", stage_id=stage
        )
        laptop.employee_id.set([cls.it])
        bot = OnboardingTask.objects.create(
            task_title="Підключитися до бота", stage_id=stage
        )
        bot.employee_id.set([cls.it])
        set_details(
            bot,
            url="https://drive.google.com/bot-guide",
            description="Google Chat → /balance",
            for_subject=True,
        )
        position = make_job_position(title="QA Specialist")
        recruitment.open_positions.add(position)
        candidate = Candidate.objects.create(
            name="Tetiana",
            email="new@tasks.horilla",
            recruitment_id=recruitment,
            job_position_id=position,
            converted_employee_id=cls.newcomer,
            converted=True,
        )
        cls.laptop = CandidateTask.objects.create(
            candidate_id=candidate,
            onboarding_task_id=laptop,
            stage_id=stage,
            status="stuck",
        )
        cls.bot = CandidateTask.objects.create(
            candidate_id=candidate, onboarding_task_id=bot, stage_id=stage
        )

        process = Offboarding.objects.create(
            title="Звільнення", description="-", company_id=company
        )
        archived = OffboardingStage.objects.get(offboarding_id=process, type="archived")
        handover = OffboardingStage.objects.get(offboarding_id=process, type="handover")
        access = OffboardingTask.objects.create(
            title="Закрити доступи", stage_id=handover
        )
        access.managers.set([cls.it])
        cls.offboarding = OffboardingEmployee.objects.create(
            employee_id=cls.leaver, stage_id=handover
        )
        cls.archived = archived

    def get(self, token=TOKEN):
        return self.client.get("/scg/bot-tasks/", HTTP_X_BOT_TOKEN=token)

    def tasks(self):
        return {task["key"]: task for task in self.get().json()["tasks"]}

    def test_needs_the_token(self):
        self.assertEqual(self.get(token="wrong").status_code, 403)

    def test_sends_each_task_with_its_details_and_people(self):
        tasks = self.tasks()
        laptop = tasks[f"onboarding:{self.laptop.pk}"]
        self.assertEqual(
            (
                laptop["title"],
                laptop["status"],
                laptop["stuck"],
                laptop["assignees"],
                laptop["subject"],
            ),
            (
                "Видати ноутбук",
                "in_progress",
                True,
                ["it@tasks.horilla"],
                "Tetiana Employee",
            ),
        )
        bot = tasks[f"onboarding:{self.bot.pk}"]
        self.assertEqual(
            (bot["assignees"], bot["url"], bot["description"]),
            (
                ["new@tasks.horilla"],
                "https://drive.google.com/bot-guide",
                "Google Chat → /balance",
            ),
        )

        offboarding = [t for key, t in tasks.items() if key.startswith("offboarding:")]
        self.assertEqual(
            [(t["title"], t["assignees"], t["closed"]) for t in offboarding],
            [("Закрити доступи", ["it@tasks.horilla"], False)],
        )

    def test_a_finished_offboarding_closes_its_tasks(self):
        self.offboarding.stage_id = self.archived
        self.offboarding.save()
        offboarding = [
            t for key, t in self.tasks().items() if key.startswith("offboarding:")
        ]
        self.assertTrue(offboarding[0]["closed"])

    def test_takes_a_status_clicked_in_the_bot(self):
        from offboarding.models import EmployeeTask
        from onboarding.models import CandidateTask

        off_task = EmployeeTask.objects.get(employee_id=self.offboarding)
        rows = [
            {
                "key": f"onboarding:{self.laptop.pk}",
                "status": "done",
                "by": "it@tasks.horilla",
            },
            {
                "key": f"offboarding:{off_task.pk}",
                "status": "in_progress",
                "by": "it@tasks.horilla",
            },
            {
                "key": f"onboarding:{self.bot.pk}",
                "status": "todo",
                "by": "new@tasks.horilla",
            },
            {"key": "onboarding:0", "status": "done"},
            {"key": "nonsense", "status": "done"},
        ]
        response = self.client.put(
            "/scg/bot-sync/",
            json.dumps({"task_statuses": rows}),
            content_type="application/json",
            HTTP_X_BOT_TOKEN=TOKEN,
        )
        self.assertEqual(
            [r["result"] for r in response.json()["task_statuses"]],
            ["updated", "updated", "unchanged", "unknown_task", "error"],
        )
        self.assertEqual(CandidateTask.objects.get(pk=self.laptop.pk).status, "done")
        self.assertEqual(EmployeeTask.objects.get(pk=off_task.pk).status, "in_progress")


class MenuTest(TestCase):
    """SOF-2177: HR's menu holds only what HR uses - superusers included."""

    KEPT = {
        "Employee": {
            "Dashboard",
            "Employees",
            "Organization Chart",
            "Policies",
            "Configuration",
        },
        "Leave": {
            "Dashboard",
            "Leave Requests",
            "Leave Balances",
            "Public Holidays",
            "Configuration",
        },
        "Recruitment": {
            "Dashboard",
            "Recruitment Pipeline",
            "Open Recruitments",
            "Candidates",
            "Configuration",
        },
        "Onboarding": {"Dashboard", "Candidates", "Onboarding Tasks"},
        "Offboarding": {"Dashboard", "Exit Process"},
        "Reports": {"Standard Reports", "Explorer"},
        "Surveys": {"Dashboard", "Surveys"},  # SOF-2178
    }

    def test_the_menu_of_a_superuser(self):
        from django.contrib.sessions.backends.db import SessionStore
        from django.test import RequestFactory
        from django.utils import translation

        from horilla.config import get_MENUS
        from horilla.testkit import make_user

        request = RequestFactory().get("/")
        request.user = make_user("menu_hr", is_superuser=True)
        request.session = SessionStore()
        request.session.create()
        with translation.override(None):
            menus = {
                str(menu["menu"]): {str(item["menu"]) for item in menu["submenu"]}
                for menu in get_MENUS(request)["sidebar"]
            }
        self.assertEqual(menus, self.KEPT)


@mock.patch.dict(os.environ, {"SCG_BOT_SYNC_TOKEN": TOKEN})
class BotNotifyTest(TestCase):
    """SOF-2127: notify.send → outbox → the bot's endpoint, with a filter and retries."""

    @classmethod
    def setUpTestData(cls):
        from base.models import Department, IntegrationApps
        from scg_overrides.models import BotTransport

        cls.company = make_company("Notify Co")
        cls.it = Department.objects.create(department="IT")
        cls.actor = make_employee(
            company=cls.company, email="actor@test.horilla", department=cls.it
        )
        cls.target = make_employee(
            company=cls.company, email="target@test.horilla", department=cls.it
        )
        IntegrationApps.objects.create(app_label="corporate_bot", is_enabled=True)
        transport = BotTransport.current()
        transport.bot_url = "https://bot.test"
        transport.token = "shared"
        transport.site_url = "https://hrm.test"
        transport.exclude_patterns = "attendance\n^Shift"
        transport.save()

    def send(
        self,
        verb="Leave request updated for %(employee)s.",
        recipient=None,
        redirect="/leave/request-view?id=1",
        **kwargs,
    ):
        from notifications.signals import notify

        with mock.patch("threading.Thread") as thread:
            notify.send(
                self.actor,
                recipient=recipient or self.target.employee_user_id,
                verb=verb,
                redirect=redirect,
                icon="people-circle",
                **kwargs,
            )
        return thread

    def outbox(self):
        from scg_overrides.models import BotNotification

        return list(BotNotification.objects.order_by("id"))

    def test_queues_one_row_per_recipient_with_the_rendered_text_and_an_absolute_link(
        self,
    ):
        self.send(verb_params={"employee": "Target Employee"})
        rows = self.outbox()
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            (rows[0].recipient_email, rows[0].text, rows[0].url),
            (
                "target@test.horilla",
                "Leave request updated for Target Employee.",
                "https://hrm.test/leave/request-view?id=1",
            ),
        )

    def test_fills_the_text_from_verb_params_like_horilla_does(self):
        # SOF-2191: 116 of 240 notify.send calls pass their values this way
        self.send(
            verb="You are invited to the survey '%(title)s'",
            verb_params={"title": "Gallup Q12"},
            employee="ignored: Horilla uses only verb_params",
        )
        self.send(verb="Leave request updated for %(employee)s.", verb_params={})
        self.send(verb="Plain text, no values", verb_params={"title": "unused"})
        self.assertEqual(
            [r.text for r in self.outbox()],
            [
                "You are invited to the survey 'Gallup Q12'",
                "Leave request updated for %(employee)s.",
                "Plain text, no values",
            ],
        )

    def test_skips_the_actor_excluded_patterns_and_a_disabled_transport(self):
        self.send(recipient=self.actor.employee_user_id)
        self.send(verb="Shift request approved")
        self.send(verb="Your attendance was validated")
        self.assertEqual(self.outbox(), [])
        # the settings page's test notification goes to the actor on purpose
        from scg_overrides.models import BotNotification

        self.send(recipient=self.actor.employee_user_id, include_actor=True)
        self.assertEqual(
            [r.recipient_email for r in self.outbox()],
            [self.actor.employee_user_id.email],
        )
        BotNotification.objects.all().delete()
        from base.models import IntegrationApps

        IntegrationApps.objects.filter(app_label="corporate_bot").update(
            is_enabled=False
        )
        self.send()
        self.assertEqual(self.outbox(), [])

    def test_delivers_the_batch_to_the_bot_and_keeps_its_answers(self):
        from scg_overrides import bot_notify

        self.send()
        self.send(
            recipient=[self.actor.employee_user_id, self.target.employee_user_id],
            verb="Announcement",
        )
        rows = self.outbox()
        answer = mock.Mock(status_code=200)
        answer.json.return_value = {
            "notifications": [
                {"id": rows[0].pk, "result": "delivered"},
                {"id": rows[1].pk, "result": "no_dm"},
            ]
        }
        with mock.patch(
            "scg_overrides.bot_notify.requests.post", return_value=answer
        ) as post:
            self.assertEqual(bot_notify.deliver_pending(), 2)
        self.assertEqual(
            post.call_args.args[0], "https://bot.test/horilla/notifications"
        )
        self.assertEqual(post.call_args.kwargs["headers"], {"X-Bot-Token": "shared"})
        self.assertEqual(
            [n["email"] for n in post.call_args.kwargs["json"]["notifications"]],
            ["target@test.horilla", "target@test.horilla"],
        )
        rows = self.outbox()
        self.assertEqual(
            [(r.result, r.sent_at is not None, r.attempts) for r in rows],
            [("delivered", True, 1), ("no_dm", True, 1)],
        )
        with mock.patch("scg_overrides.bot_notify.requests.post") as post:
            self.assertEqual(bot_notify.deliver_pending(), 0)
            post.assert_not_called()

    def test_a_failed_push_is_retried_until_the_limit(self):
        from django.utils import timezone

        from scg_overrides import bot_notify
        from scg_overrides.models import BotNotification

        self.send()
        with mock.patch(
            "scg_overrides.bot_notify.requests.post",
            side_effect=ConnectionError("down"),
        ):
            bot_notify.deliver_pending()
            # within the claim of the failed push nothing is retried
            self.assertEqual(bot_notify.deliver_pending(), 0)
            for _ in range(bot_notify.MAX_ATTEMPTS + 1):
                BotNotification.objects.update(
                    last_attempt_at=timezone.now() - bot_notify.CLAIM
                )
                bot_notify.deliver_pending()
        row = self.outbox()[0]
        self.assertEqual(
            (row.attempts, row.sent_at, row.last_error),
            (bot_notify.MAX_ATTEMPTS, None, "down"),
        )

    def test_a_row_being_sent_is_not_sent_again_by_a_parallel_push(self):
        from scg_overrides import bot_notify

        self.send()
        row = self.outbox()[0]
        answer = mock.Mock(status_code=200)
        answer.json.return_value = {
            "notifications": [{"id": row.pk, "result": "delivered"}]
        }
        parallel = []

        def post(*args, **kwargs):
            # the scheduled retry fires while the immediate push waits for the bot
            parallel.append(bot_notify.deliver_pending())
            return answer

        with mock.patch(
            "scg_overrides.bot_notify.requests.post", side_effect=post
        ) as p:
            self.assertEqual(bot_notify.deliver_pending(), 1)
        self.assertEqual((p.call_count, parallel), (1, [0]))
        row.refresh_from_db()
        self.assertEqual((row.result, row.attempts), ("delivered", 1))

    def test_settings_page_saves_and_toggles_the_integration(self):
        from base.models import IntegrationApps
        from horilla.testkit import make_user
        from scg_overrides.models import BotTransport

        admin = make_user("notify_admin", is_superuser=True)
        make_employee(
            company=self.company,
            email="notify_admin@test.horilla",
            user=admin,
            department=self.it,
        )
        self.client.force_login(admin)
        response = self.client.get("/scg/bot-notifications/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "https://bot.test")
        response = self.client.post(
            "/scg/bot-notifications/",
            {
                "bot_url": "https://bot2.test",
                "token": "t2",
                "site_url": "https://hrm.test",
                "exclude_patterns": "",
                "enabled": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(BotTransport.current().bot_url, "https://bot2.test")
        self.assertFalse(
            IntegrationApps.objects.get(app_label="corporate_bot").is_enabled
        )


class GenderTest(TestCase):
    """SOF-2196: employees without a gender can be found and are counted."""

    @classmethod
    def setUpTestData(cls):
        from horilla.testkit import make_user

        cls.company = make_company("Gender Co")
        cls.admin = make_user("gender_admin", is_superuser=True)
        cls.male = make_employee(
            company=cls.company, email="gender_admin@test.horilla", user=cls.admin
        )
        cls.female = make_employee(company=cls.company, email="f@test.horilla")
        cls.unset = make_employee(company=cls.company, email="n@test.horilla")
        cls.blank = make_employee(company=cls.company, email="b@test.horilla")
        Employee.objects.entire().filter(pk=cls.female.pk).update(gender="female")
        Employee.objects.entire().filter(pk=cls.unset.pk).update(gender=None)
        Employee.objects.entire().filter(pk=cls.blank.pk).update(gender="")

    def filtered(self, value):
        from django.http import QueryDict

        from employee.filters import EmployeeFilter

        data = QueryDict(mutable=True)
        data.update(
            {"custom_field": "gender", "custom_lookup": "exact", "custom_value": value}
        )
        found = EmployeeFilter(data, queryset=Employee.objects.entire()).qs
        return set(found.values_list("pk", flat=True))

    def test_the_gender_filter_offers_and_finds_not_specified(self):
        from employee.filters import EmployeeFilter

        gender = next(
            f for f in EmployeeFilter().custom_filter_fields if f["key"] == "gender"
        )
        self.assertEqual(
            [value for value, _label in gender["choices"]],
            ["male", "female", "other", "not_set"],
        )
        self.assertEqual(self.filtered("not_set"), {self.unset.pk, self.blank.pk})
        self.assertEqual(self.filtered("female"), {self.female.pk})
        self.assertEqual(self.filtered("male"), {self.male.pk})

    def test_the_dashboard_counts_who_has_no_gender(self):
        self.client.force_login(self.admin)
        response = self.client.get("/employee/dashboard-employee-gender/").json()
        self.assertEqual(
            response["labels"], ["Male", "Female", "Other", "Not specified"]
        )
        self.assertEqual(response["dataSet"][0]["data"], [1, 1, 0, 2])

        Employee.objects.entire().filter(gender__isnull=True).update(gender="male")
        Employee.objects.entire().filter(gender="").update(gender="female")
        response = self.client.get("/employee/dashboard-employee-gender/").json()
        self.assertEqual(response["labels"], ["Male", "Female", "Other"])
        self.assertEqual(response["dataSet"][0]["data"], [2, 2, 0])
