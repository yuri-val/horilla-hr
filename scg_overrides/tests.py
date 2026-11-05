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
