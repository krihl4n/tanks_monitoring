"""Tests for calculations module — calc_liters, daily waste gain, holidays, estimate."""

import sys
import os
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from calculations import (
    calc_liters, calc_daily_waste_gain,
    _polish_holidays, _is_non_working_day, _next_working_day, _prev_working_day,
    _count_non_working_streak, estimate_pumpout_date,
)
from config import settings


class TestCalcLiters(unittest.TestCase):

    def setUp(self):
        self._patcher = patch.dict(settings, {"tank_capacity_l": 10000})
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    def test_zero(self):
        self.assertEqual(calc_liters(0), 0)

    def test_hundred(self):
        self.assertEqual(calc_liters(100), 10000)

    def test_fifty(self):
        self.assertEqual(calc_liters(50), 5000)


class TestCalcDailyWasteGain(unittest.TestCase):

    def setUp(self):
        self._patcher = patch.dict(settings, {"tank_capacity_l": 10000})
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    def _make_rows(self, day_data):
        """Build CSV-like row list from per-day data.

        day_data: [("2026-04-30", [pct1, pct2, ...]), ...]
        """
        rows = []
        for day_str, pcts in day_data:
            for i, pct in enumerate(pcts):
                ts = f"{day_str}T{8+i:02d}:00:00"
                rows.append({"timestamp": ts, "waste_pct": str(pct)})
        return rows

    @patch("calculations.load_pumpouts", return_value=[])
    def test_basic_gain(self, _):
        """Day going from 50% to 55% = 500 l gain."""
        rows = self._make_rows([("2026-04-30", [50, 52, 55])])
        result = calc_daily_waste_gain(rows)
        self.assertIn("2026-04-30", result)
        self.assertAlmostEqual(result["2026-04-30"], 500, places=0)

    @patch("calculations.load_pumpouts", return_value=[])
    def test_no_gain_ignored(self, _):
        """Day with no gain (55% -> 55%) -> no entry."""
        rows = self._make_rows([("2026-04-30", [55, 55, 55])])
        result = calc_daily_waste_gain(rows)
        self.assertNotIn("2026-04-30", result)

    @patch("calculations.load_pumpouts", return_value=[])
    def test_decrease_ignored(self, _):
        """Day with decrease (55% -> 50%) -> no entry."""
        rows = self._make_rows([("2026-04-30", [55, 52, 50])])
        result = calc_daily_waste_gain(rows)
        self.assertNotIn("2026-04-30", result)

    @patch("calculations.load_pumpouts")
    def test_pumpout_day_skipped(self, mock_pumpouts):
        """Day with pumpout event -> skipped."""
        mock_pumpouts.return_value = [{"timestamp": "2026-04-30T10:00:00"}]
        rows = self._make_rows([("2026-04-30", [80, 30])])
        result = calc_daily_waste_gain(rows)
        self.assertNotIn("2026-04-30", result)

    @patch("calculations.load_pumpouts", return_value=[])
    def test_noise_cancellation(self, _):
        """+-1 pp oscillation -> net 0, no inflation."""
        rows = self._make_rows([("2026-04-30", [50, 51, 50, 51, 50])])
        result = calc_daily_waste_gain(rows)
        self.assertNotIn("2026-04-30", result)


class TestPolishHolidays(unittest.TestCase):

    def test_fixed_holidays(self):
        holidays = _polish_holidays(2026)
        self.assertIn(date(2026, 1, 1), holidays)   # New Year
        self.assertIn(date(2026, 5, 1), holidays)   # Labour Day
        self.assertIn(date(2026, 12, 25), holidays) # Christmas

    def test_easter_2026(self):
        """Easter 2026 = April 5th."""
        holidays = _polish_holidays(2026)
        self.assertIn(date(2026, 4, 5), holidays)   # Easter Sunday
        self.assertIn(date(2026, 4, 6), holidays)   # Easter Monday

    def test_corpus_christi_2026(self):
        """Corpus Christi 2026 = June 4th (Easter + 60 days)."""
        holidays = _polish_holidays(2026)
        self.assertIn(date(2026, 6, 4), holidays)

    def test_count(self):
        """Poland has 12 public holidays."""
        holidays = _polish_holidays(2026)
        self.assertEqual(len(holidays), 12)


class TestWorkingDays(unittest.TestCase):

    def test_weekday_is_working(self):
        """Monday (non-holiday) -> working day."""
        # May 5, 2026 is Monday
        self.assertFalse(_is_non_working_day(date(2026, 5, 5)))

    def test_sunday_is_non_working(self):
        self.assertTrue(_is_non_working_day(date(2026, 5, 10)))

    def test_holiday_is_non_working(self):
        """May 1st = public holiday."""
        self.assertTrue(_is_non_working_day(date(2026, 5, 1)))

    def test_next_working_day_from_sunday(self):
        # May 10, 2026 = Sunday -> May 11 (Monday)
        self.assertEqual(_next_working_day(date(2026, 5, 10)), date(2026, 5, 11))

    def test_prev_working_day_from_sunday(self):
        # May 10, 2026 = Sunday -> May 9 (Saturday is not a holiday in PL)
        self.assertEqual(_prev_working_day(date(2026, 5, 10)), date(2026, 5, 9))

    def test_christmas_streak(self):
        """Christmas 2026: Dec 25 (Fri), 26 (Sat), 27 (Sun) = 3+ non-working days."""
        streak = _count_non_working_streak(date(2026, 12, 25))
        self.assertGreaterEqual(streak, 3)


class TestEstimatePumpoutDate(unittest.TestCase):

    @patch("calculations.load_history")
    def test_no_data_returns_none(self, mock_load):
        mock_load.return_value = []
        self.assertIsNone(estimate_pumpout_date())

    @patch("calculations.load_history")
    def test_flat_trend_returns_none(self, mock_load):
        """Flat fill level -> no estimate."""
        now = datetime.now()
        rows = []
        for i in range(7 * 24):
            ts = now - timedelta(hours=7*24-i)
            rows.append({"timestamp": ts.isoformat(), "waste_pct": "50"})
        mock_load.return_value = rows
        self.assertIsNone(estimate_pumpout_date())

    @patch("calculations.load_history")
    def test_decreasing_trend_returns_none(self, mock_load):
        """Decreasing trend -> no estimate."""
        now = datetime.now()
        rows = []
        for i in range(7 * 24):
            ts = now - timedelta(hours=7*24-i)
            pct = 80 - i * 0.1
            rows.append({"timestamp": ts.isoformat(), "waste_pct": str(pct)})
        mock_load.return_value = rows
        self.assertIsNone(estimate_pumpout_date())

    @patch("calculations.load_history")
    def test_rising_trend_returns_estimate(self, mock_load):
        """Rising trend -> returns dict with dates."""
        now = datetime.now()
        rows = []
        for i in range(7 * 24):
            ts = now - timedelta(hours=7*24-i)
            pct = 40 + i * 0.2  # ~0.2 pp/h = ~5 pp/day
            rows.append({"timestamp": ts.isoformat(), "waste_pct": str(pct)})
        mock_load.return_value = rows
        result = estimate_pumpout_date()
        self.assertIsNotNone(result)
        if isinstance(result, dict):
            self.assertIn("full_date", result)
            self.assertIn("service_date", result)
            self.assertIn("order_date", result)

    @patch("calculations.load_history")
    def test_already_full_returns_now(self, mock_load):
        """Fill >= PUMPOUT_ESTIMATE_PCT -> 'teraz'."""
        now = datetime.now()
        rows = []
        for i in range(7 * 24):
            ts = now - timedelta(hours=7*24-i)
            pct = settings["pumpout_estimate_pct"] + i * 0.01
            rows.append({"timestamp": ts.isoformat(), "waste_pct": str(pct)})
        mock_load.return_value = rows
        result = estimate_pumpout_date()
        self.assertEqual(result, "teraz")

    @patch("calculations.load_planned_pumpout")
    @patch("calculations.load_history")
    def test_planned_pumpout_too_late_warns(self, mock_load, mock_planned):
        """Planned pumpout after estimated fill date should produce warning."""
        now = datetime.now()
        rows = []
        for i in range(7 * 24):
            ts = now - timedelta(hours=7*24-i)
            pct = 40 + i * 0.2  # fast fill
            rows.append({"timestamp": ts.isoformat(), "waste_pct": str(pct)})
        mock_load.return_value = rows

        # First call without planned pumpout to get estimated date
        mock_planned.return_value = None
        result = estimate_pumpout_date()
        if result is None or result == "teraz":
            self.skipTest("Estimation did not produce a date result")

        # Parse the estimated full_date
        full_date_str = result["full_date"][:10]
        est_date = datetime.strptime(full_date_str, "%d.%m.%Y").date()

        # Plan pumpout 10 days AFTER estimated fill date
        late_date = est_date + timedelta(days=10)
        mock_planned.return_value = {"date": late_date.strftime("%Y-%m-%d"), "note": ""}
        result2 = estimate_pumpout_date()
        self.assertIsNotNone(result2)
        self.assertIsInstance(result2, dict)
        self.assertIn("warning", result2)
        self.assertIsNotNone(result2["warning"])
        self.assertIn("PO estymowanym zapełnieniu", result2["warning"])

    @patch("calculations.load_planned_pumpout")
    @patch("calculations.load_history")
    def test_planned_pumpout_on_time_ok(self, mock_load, mock_planned):
        """Planned pumpout before estimated fill date should produce planned_ok."""
        now = datetime.now()
        rows = []
        for i in range(7 * 24):
            ts = now - timedelta(hours=7*24-i)
            pct = 40 + i * 0.2
            rows.append({"timestamp": ts.isoformat(), "waste_pct": str(pct)})
        mock_load.return_value = rows

        mock_planned.return_value = None
        result = estimate_pumpout_date()
        if result is None or result == "teraz":
            self.skipTest("Estimation did not produce a date result")

        full_date_str = result["full_date"][:10]
        est_date = datetime.strptime(full_date_str, "%d.%m.%Y").date()

        # Plan pumpout 5 days BEFORE estimated fill date
        early_date = est_date - timedelta(days=5)
        mock_planned.return_value = {"date": early_date.strftime("%Y-%m-%d"), "note": ""}
        result2 = estimate_pumpout_date()
        self.assertIsNotNone(result2)
        self.assertIsInstance(result2, dict)
        self.assertIn("planned_ok", result2)
        self.assertIn("przed estymowanym zapełnieniem", result2["planned_ok"])

    @patch("calculations.load_planned_pumpout")
    @patch("calculations.load_history")
    def test_no_planned_pumpout(self, mock_load, mock_planned):
        """No planned pumpout should not add warning or planned_ok."""
        now = datetime.now()
        rows = []
        for i in range(7 * 24):
            ts = now - timedelta(hours=7*24-i)
            pct = 40 + i * 0.2
            rows.append({"timestamp": ts.isoformat(), "waste_pct": str(pct)})
        mock_load.return_value = rows
        mock_planned.return_value = None
        result = estimate_pumpout_date()
        if result is None or result == "teraz":
            self.skipTest("Estimation did not produce a date result")
        self.assertNotIn("planned_ok", result)


if __name__ == "__main__":
    unittest.main()
