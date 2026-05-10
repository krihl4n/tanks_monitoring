"""Tests for charts module — SVG chart generation."""

import sys
import os
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from charts import (
    generate_rainfall_chart,
    generate_seasonal_rainwater_chart,
    generate_waste_daily_chart,
    generate_waste_weekday_chart,
    generate_rain_efficiency_chart,
    generate_svg_chart,
    _generate_hourly_usage_chart,
    _generate_monthly_usage_chart,
)


def _make_history_rows(days, waste_pcts=None, rain_pcts=None):
    """Helper: generate CSV-like rows with timestamps over N days."""
    rows = []
    base = datetime.now() - timedelta(days=days)
    for i in range(days * 4):  # 4 readings per day
        ts = base + timedelta(hours=i * 6)
        row = {"timestamp": ts.isoformat()}
        if waste_pcts is not None:
            row["waste_pct"] = str(waste_pcts[i % len(waste_pcts)])
        if rain_pcts is not None:
            row["rain_pct"] = str(rain_pcts[i % len(rain_pcts)])
        rows.append(row)
    return rows


def _make_rainfall_rows(days, mm_per_day=2.0):
    """Helper: generate rainfall CSV-like rows."""
    rows = []
    base = datetime.now() - timedelta(days=days)
    for i in range(days):
        ts = base + timedelta(days=i, hours=12)
        rows.append({
            "timestamp": ts.isoformat(),
            "precipitation_mm": str(mm_per_day),
        })
    return rows


class TestGenerateSvgChart(unittest.TestCase):
    """Tests for the generic generate_svg_chart function."""

    def test_empty_rows(self):
        svg = generate_svg_chart([], "waste_pct", "#a00", "#a00", "day")
        self.assertIn("Brak danych", svg)
        self.assertIn("<svg", svg)

    def test_single_point(self):
        rows = [{"timestamp": "2026-04-30T10:00:00", "waste_pct": "50"}]
        svg = generate_svg_chart(rows, "waste_pct", "#a00", "#a00", "day")
        # Single point: t_max == t_min -> t_max = t_min + 1
        self.assertIn("<svg", svg)
        self.assertIn("<path", svg)

    def test_multiple_points(self):
        rows = [
            {"timestamp": "2026-04-29T10:00:00", "waste_pct": "30"},
            {"timestamp": "2026-04-29T16:00:00", "waste_pct": "35"},
            {"timestamp": "2026-04-30T10:00:00", "waste_pct": "40"},
        ]
        svg = generate_svg_chart(rows, "waste_pct", "#a00", "#b00", "day")
        self.assertIn("<svg", svg)
        self.assertIn('stroke="#a00"', svg)
        self.assertIn('fill="#b00"', svg)

    def test_time_unit_hour(self):
        rows = [
            {"timestamp": "2026-04-30T08:00:00", "waste_pct": "30"},
            {"timestamp": "2026-04-30T14:00:00", "waste_pct": "35"},
        ]
        svg = generate_svg_chart(rows, "waste_pct", "#a00", "#a00", "hour")
        self.assertIn("<svg", svg)
        # Hour labels should be HH:MM format
        self.assertIn(":00", svg)

    def test_time_unit_month(self):
        rows = [
            {"timestamp": "2026-01-15T12:00:00", "waste_pct": "30"},
            {"timestamp": "2026-06-15T12:00:00", "waste_pct": "60"},
        ]
        svg = generate_svg_chart(rows, "waste_pct", "#a00", "#a00", "month")
        self.assertIn("<svg", svg)

    def test_missing_value_key(self):
        rows = [
            {"timestamp": "2026-04-30T10:00:00", "other_key": "50"},
        ]
        svg = generate_svg_chart(rows, "waste_pct", "#a00", "#a00", "day")
        self.assertIn("Brak danych", svg)

    def test_invalid_timestamp(self):
        rows = [
            {"timestamp": "not-a-date", "waste_pct": "50"},
        ]
        svg = generate_svg_chart(rows, "waste_pct", "#a00", "#a00", "day")
        self.assertIn("Brak danych", svg)

    def test_rain_pct_key(self):
        rows = [
            {"timestamp": "2026-04-29T10:00:00", "rain_pct": "70"},
            {"timestamp": "2026-04-30T10:00:00", "rain_pct": "75"},
        ]
        svg = generate_svg_chart(rows, "rain_pct", "#48cae4", "#48cae4", "day")
        self.assertIn("<svg", svg)
        self.assertIn('stroke="#48cae4"', svg)


class TestGenerateRainfallChart(unittest.TestCase):

    def test_no_data(self):
        svg = generate_rainfall_chart(
            7,
            load_rainfall=lambda days: [],
            load_history=lambda days: [],
        )
        self.assertIn("Brak danych", svg)

    def test_with_data(self):
        rain = _make_rainfall_rows(7, mm_per_day=3.0)
        history = _make_history_rows(7, rain_pcts=[40, 42, 44, 46])
        svg = generate_rainfall_chart(
            7,
            load_rainfall=lambda days: rain,
            load_history=lambda days: history,
        )
        self.assertIn("<svg", svg)
        self.assertIn("rect", svg)  # bar chart bars

    def test_rain_only_no_tank(self):
        rain = _make_rainfall_rows(7, mm_per_day=5.0)
        svg = generate_rainfall_chart(
            7,
            load_rainfall=lambda days: rain,
            load_history=lambda days: [],
        )
        self.assertIn("<svg", svg)


class TestGenerateSeasonalRainwaterChart(unittest.TestCase):

    def test_no_data(self):
        result = generate_seasonal_rainwater_chart(
            load_history=lambda days: [],
            sensor_noise_cm=2,
            tank_depth_cm=100,
            tank_capacity_l=10000,
        )
        self.assertIn("Brak danych", result)

    def test_with_gain(self):
        """Months with net positive gain should produce bars."""
        rows = []
        # Month 1: 10% -> 30% (gain 20%, above noise)
        base = datetime(2026, 1, 1)
        rows.append({"timestamp": base.isoformat(), "rain_pct": "10"})
        rows.append({"timestamp": (base + timedelta(days=15)).isoformat(), "rain_pct": "30"})
        # Month 2: 30% -> 50%
        base2 = datetime(2026, 2, 1)
        rows.append({"timestamp": base2.isoformat(), "rain_pct": "30"})
        rows.append({"timestamp": (base2 + timedelta(days=15)).isoformat(), "rain_pct": "50"})

        result = generate_seasonal_rainwater_chart(
            load_history=lambda days: rows,
            sensor_noise_cm=2,
            tank_depth_cm=100,
            tank_capacity_l=10000,
        )
        self.assertIn("<svg", result)
        self.assertIn("rect", result)

    def test_no_significant_gain(self):
        """Gain below noise threshold should produce 'Brak danych'."""
        rows = [
            {"timestamp": "2026-01-01T00:00:00", "rain_pct": "50"},
            {"timestamp": "2026-01-31T00:00:00", "rain_pct": "50.5"},
        ]
        result = generate_seasonal_rainwater_chart(
            load_history=lambda days: rows,
            sensor_noise_cm=2,
            tank_depth_cm=100,
            tank_capacity_l=10000,
        )
        self.assertIn("Brak danych", result)


class TestGenerateWasteDailyChart(unittest.TestCase):

    def test_no_data(self):
        svg = generate_waste_daily_chart(
            30,
            load_history=lambda days: [],
            calc_daily_waste_gain=lambda rows: {},
            tank_capacity_l=10000,
        )
        self.assertIn("Brak danych", svg)

    def test_24h_delegates_to_hourly(self):
        """days<=1 should call _generate_hourly_usage_chart internally."""
        rows = [
            {"timestamp": "2026-04-30T08:00:00", "waste_pct": "50"},
            {"timestamp": "2026-04-30T09:00:00", "waste_pct": "51"},
            {"timestamp": "2026-04-30T10:00:00", "waste_pct": "52"},
        ]
        svg = generate_waste_daily_chart(
            1,
            load_history=lambda days: rows,
            calc_daily_waste_gain=lambda rows: {},
            tank_capacity_l=10000,
        )
        self.assertIn("<svg", svg)

    def test_daily_view(self):
        base = datetime.now() - timedelta(days=10)
        daily_gain = {}
        rows = []
        for i in range(10):
            d = (base + timedelta(days=i)).strftime("%Y-%m-%d")
            daily_gain[d] = 100 + i * 10
            ts = (base + timedelta(days=i, hours=12)).isoformat()
            rows.append({"timestamp": ts, "waste_pct": str(30 + i)})

        svg = generate_waste_daily_chart(
            30,
            load_history=lambda days: rows,
            calc_daily_waste_gain=lambda r: daily_gain,
            tank_capacity_l=10000,
        )
        self.assertIn("<svg", svg)
        self.assertIn("rect", svg)

    def test_yearly_view_delegates_to_monthly(self):
        """days>90 should generate monthly chart."""
        base = datetime.now() - timedelta(days=120)
        daily_gain = {}
        rows = []
        for i in range(120):
            d = (base + timedelta(days=i)).strftime("%Y-%m-%d")
            daily_gain[d] = 150
            ts = (base + timedelta(days=i, hours=12)).isoformat()
            rows.append({"timestamp": ts, "waste_pct": str(40)})

        svg = generate_waste_daily_chart(
            365,
            load_history=lambda days: rows,
            calc_daily_waste_gain=lambda r: daily_gain,
            tank_capacity_l=10000,
        )
        self.assertIn("<svg", svg)


class TestGenerateWasteWeekdayChart(unittest.TestCase):

    def test_no_data(self):
        svg = generate_waste_weekday_chart(
            90,
            load_history=lambda days: [],
            calc_daily_waste_gain=lambda rows: {},
        )
        self.assertIn("Brak danych", svg)

    def test_insufficient_data(self):
        """Less than 7 days of data should show 'Za mało danych'."""
        base = datetime.now() - timedelta(days=5)
        daily_gain = {}
        rows = []
        for i in range(5):
            d = (base + timedelta(days=i)).strftime("%Y-%m-%d")
            daily_gain[d] = 100
            ts = (base + timedelta(days=i, hours=12)).isoformat()
            rows.append({"timestamp": ts, "waste_pct": str(40)})

        svg = generate_waste_weekday_chart(
            90,
            load_history=lambda days: rows,
            calc_daily_waste_gain=lambda r: daily_gain,
        )
        self.assertIn("Za mało danych", svg)

    def test_sufficient_data(self):
        """>= 2 weeks of data should produce bars."""
        base = datetime.now() - timedelta(days=20)
        daily_gain = {}
        rows = []
        for i in range(20):
            d = (base + timedelta(days=i)).strftime("%Y-%m-%d")
            daily_gain[d] = 100 + (i % 7) * 20
            ts = (base + timedelta(days=i, hours=12)).isoformat()
            rows.append({"timestamp": ts, "waste_pct": str(40)})

        svg = generate_waste_weekday_chart(
            90,
            load_history=lambda days: rows,
            calc_daily_waste_gain=lambda r: daily_gain,
        )
        self.assertIn("<svg", svg)
        self.assertIn("Pn", svg)
        self.assertIn("Nd", svg)


class TestGenerateRainEfficiencyChart(unittest.TestCase):

    def test_no_data(self):
        svg = generate_rain_efficiency_chart(
            30,
            load_rainfall=lambda days: [],
            load_history=lambda days: [],
            tank_capacity_l=10000,
        )
        self.assertIn("Brak danych", svg)

    def test_insufficient_points(self):
        """Less than 3 correlated days -> 'Za mało danych'."""
        rain = [
            {"timestamp": "2026-04-29T12:00:00", "precipitation_mm": "5"},
        ]
        history = [
            {"timestamp": "2026-04-29T06:00:00", "rain_pct": "40"},
            {"timestamp": "2026-04-29T18:00:00", "rain_pct": "45"},
        ]
        svg = generate_rain_efficiency_chart(
            30,
            load_rainfall=lambda days: rain,
            load_history=lambda days: history,
            tank_capacity_l=10000,
        )
        self.assertIn("Za mało danych", svg)

    def test_with_enough_points(self):
        """3+ correlated days should produce scatter plot."""
        rain = []
        history = []
        base = datetime(2026, 4, 25)
        for i in range(5):
            d = base + timedelta(days=i)
            rain.append({
                "timestamp": (d + timedelta(hours=12)).isoformat(),
                "precipitation_mm": str(3 + i),
            })
            history.append({
                "timestamp": (d + timedelta(hours=6)).isoformat(),
                "rain_pct": str(30 + i * 2),
            })
            history.append({
                "timestamp": (d + timedelta(hours=18)).isoformat(),
                "rain_pct": str(32 + i * 2),
            })

        svg = generate_rain_efficiency_chart(
            30,
            load_rainfall=lambda days: rain,
            load_history=lambda days: history,
            tank_capacity_l=10000,
        )
        self.assertIn("<svg", svg)
        self.assertIn("circle", svg)


class TestGenerateHourlyUsageChart(unittest.TestCase):

    def test_no_data(self):
        svg = _generate_hourly_usage_chart([], tank_capacity_l=10000)
        self.assertIn("Brak danych", svg)

    def test_with_data(self):
        rows = [
            {"timestamp": "2026-04-30T08:00:00", "waste_pct": "50"},
            {"timestamp": "2026-04-30T08:30:00", "waste_pct": "51"},
            {"timestamp": "2026-04-30T09:00:00", "waste_pct": "52"},
            {"timestamp": "2026-04-30T10:00:00", "waste_pct": "53"},
        ]
        svg = _generate_hourly_usage_chart(rows, tank_capacity_l=10000)
        self.assertIn("<svg", svg)
        self.assertIn("rect", svg)

    def test_invalid_waste_pct(self):
        rows = [
            {"timestamp": "2026-04-30T08:00:00", "waste_pct": "abc"},
        ]
        svg = _generate_hourly_usage_chart(rows, tank_capacity_l=10000)
        self.assertIn("Brak danych", svg)


class TestGenerateMonthlyUsageChart(unittest.TestCase):

    def test_no_data(self):
        svg = _generate_monthly_usage_chart({}, set())
        self.assertIn("Brak danych", svg)

    def test_with_data(self):
        daily_gain = {
            "2026-03-15": 120,
            "2026-03-16": 130,
            "2026-04-01": 100,
            "2026-04-02": 110,
        }
        svg = _generate_monthly_usage_chart(daily_gain, set())
        self.assertIn("<svg", svg)
        self.assertIn("rect", svg)

    def test_incomplete_days_excluded(self):
        daily_gain = {
            "2026-04-01": 999,  # incomplete, should be excluded
            "2026-04-02": 100,
            "2026-04-03": 110,
        }
        svg = _generate_monthly_usage_chart(daily_gain, {"2026-04-01"})
        self.assertIn("<svg", svg)


class TestRainUnstableFiltering(unittest.TestCase):
    """Verify that rain_unstable=1 rows are excluded from stats charts."""

    def test_seasonal_chart_excludes_unstable(self):
        """Unstable rows should not count toward seasonal gain."""
        rows = [
            {"timestamp": "2026-01-01T00:00:00", "rain_pct": "10"},
            {"timestamp": "2026-01-10T00:00:00", "rain_pct": "50", "rain_unstable": "1"},
            {"timestamp": "2026-01-15T00:00:00", "rain_pct": "30"},
        ]
        # Without filtering: first=10, last=30 (gain=20%)
        # The unstable row (50%) should be skipped, so last stable=30
        result = generate_seasonal_rainwater_chart(
            load_history=lambda days: rows,
            sensor_noise_cm=2,
            tank_depth_cm=100,
            tank_capacity_l=10000,
        )
        self.assertIn("<svg", result)

    def test_seasonal_chart_all_unstable(self):
        """All unstable -> no data."""
        rows = [
            {"timestamp": "2026-01-01T00:00:00", "rain_pct": "10", "rain_unstable": "1"},
            {"timestamp": "2026-01-15T00:00:00", "rain_pct": "50", "rain_unstable": "1"},
        ]
        result = generate_seasonal_rainwater_chart(
            load_history=lambda days: rows,
            sensor_noise_cm=2,
            tank_depth_cm=100,
            tank_capacity_l=10000,
        )
        self.assertIn("Brak danych", result)

    def test_efficiency_chart_excludes_unstable(self):
        """Unstable rain_pct rows should be skipped in efficiency calc."""
        rain = []
        history = []
        base = datetime(2026, 4, 25)
        for i in range(5):
            d = base + timedelta(days=i)
            rain.append({
                "timestamp": (d + timedelta(hours=12)).isoformat(),
                "precipitation_mm": str(3 + i),
            })
            # Mark day 2 as unstable
            unstable = "1" if i == 2 else ""
            history.append({
                "timestamp": (d + timedelta(hours=6)).isoformat(),
                "rain_pct": str(30 + i * 2),
                "rain_unstable": unstable,
            })
            history.append({
                "timestamp": (d + timedelta(hours=18)).isoformat(),
                "rain_pct": str(32 + i * 2),
                "rain_unstable": unstable,
            })

        result = generate_rain_efficiency_chart(
            30,
            load_rainfall=lambda days: rain,
            load_history=lambda days: history,
            tank_capacity_l=10000,
        )
        # Should still produce chart (4 days of valid data)
        self.assertIn("<svg", result)


if __name__ == "__main__":
    unittest.main()
