"""Testy modułu html_gen.py — badge statusu, tabele, prognoza."""

import sys
import os
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings


class TestGenerateTankStatusBadge(unittest.TestCase):

    def setUp(self):
        from html_gen import generate_tank_status_badge
        self.badge = generate_tank_status_badge

    def test_sensor_failure_critical(self):
        result = self.badge("Waste", None, None)
        self.assertIn("#e74c3c", result)  # czerwony
        self.assertIn("Czujnik nie odpowiada", result)

    def test_waste_high_critical(self):
        result = self.badge("Waste", settings["alert_waste_high_critical_pct"], 50)
        self.assertIn("#e74c3c", result)

    def test_waste_medium_warning(self):
        result = self.badge("Waste", settings["alert_waste_high_warning_pct"], 70)
        self.assertIn("#e9c46a", result)  # żółty

    def test_waste_low_ok(self):
        result = self.badge("Waste", 30, 100)
        self.assertIn("#2a9d8f", result)  # zielony

    def test_rain_low_critical(self):
        result = self.badge("Rainwater", settings["alert_rain_low_critical_pct"], 150)
        self.assertIn("#e74c3c", result)

    def test_rain_low_warning(self):
        result = self.badge("Rainwater", settings["alert_rain_low_warning_pct"], 140)
        self.assertIn("#e9c46a", result)

    def test_rain_mid_ok(self):
        result = self.badge("Rainwater", 50, 80)
        self.assertIn("#2a9d8f", result)

    def test_rain_high_critical(self):
        result = self.badge("Rainwater", settings["alert_rain_high_critical_pct"], 20)
        self.assertIn("#e74c3c", result)

    def test_rain_high_warning(self):
        result = self.badge("Rainwater", settings["alert_rain_high_warning_pct"], 30)
        self.assertIn("#e9c46a", result)


class TestGenerateForecastHtml(unittest.TestCase):

    @patch("html_gen.fetch_weather_forecast")
    def test_empty_forecast(self, mock_fetch):
        mock_fetch.return_value = []
        from html_gen import generate_forecast_html
        self.assertEqual(generate_forecast_html(), "")

    @patch("html_gen.fetch_weather_forecast")
    def test_valid_forecast(self, mock_fetch):
        mock_fetch.return_value = [
            {"date": "2026-05-01", "precip_sum": 3.5, "precip_prob": 60,
             "temp_max": 20, "temp_min": 10},
        ]
        from html_gen import generate_forecast_html
        html = generate_forecast_html()
        self.assertIn("3.5 mm", html)
        self.assertIn("60%", html)
        self.assertIn("10°", html)

    @patch("html_gen.fetch_weather_forecast")
    def test_heavy_rain_icon(self, mock_fetch):
        mock_fetch.return_value = [
            {"date": "2026-05-01", "precip_sum": 10, "precip_prob": 90},
        ]
        from html_gen import generate_forecast_html
        html = generate_forecast_html()
        # Ikona deszczu (&#127783;)
        self.assertIn("&#127783;", html)

    @patch("html_gen.fetch_weather_forecast")
    def test_sunny_icon(self, mock_fetch):
        mock_fetch.return_value = [
            {"date": "2026-05-01", "precip_sum": 0, "precip_prob": 0},
        ]
        from html_gen import generate_forecast_html
        html = generate_forecast_html()
        # Ikona słońca (&#9728;)
        self.assertIn("&#9728;", html)


class TestGenerateMonthlyTable(unittest.TestCase):

    @patch("html_gen.load_history")
    def test_no_data(self, mock_load):
        mock_load.return_value = []
        from html_gen import generate_monthly_table
        result = generate_monthly_table()
        self.assertIn("Brak danych", result)

    @patch("html_gen.load_history")
    def test_with_data(self, mock_load):
        from datetime import datetime
        now = datetime.now()
        mock_load.return_value = [
            {"timestamp": now.isoformat(), "rain_pct": "50", "waste_pct": "40"},
            {"timestamp": now.isoformat(), "rain_pct": "60", "waste_pct": "45"},
        ]
        from html_gen import generate_monthly_table
        result = generate_monthly_table()
        self.assertIn("<table", result)
        self.assertIn("%", result)


class TestGeneratePumpoutCostsTable(unittest.TestCase):

    @patch("html_gen.load_pumpouts")
    def test_no_pumpouts(self, mock_load):
        mock_load.return_value = []
        from html_gen import generate_pumpout_costs_table
        result = generate_pumpout_costs_table()
        self.assertIn("Brak danych", result)

    @patch("html_gen.load_pumpouts")
    def test_with_pumpouts(self, mock_load):
        mock_load.return_value = [
            {"timestamp": "2026-04-01T10:00:00", "pct_before": "85", "pct_after": "20"},
            {"timestamp": "2026-04-15T10:00:00", "pct_before": "90", "pct_after": "15"},
        ]
        from html_gen import generate_pumpout_costs_table
        result = generate_pumpout_costs_table()
        self.assertIn("680 zł", result)  # 2 * 340 zł
        self.assertIn("RAZEM", result)

    @patch("html_gen.load_pumpouts")
    def test_multiple_years(self, mock_load):
        mock_load.return_value = [
            {"timestamp": "2025-12-01T10:00:00", "pct_before": "85", "pct_after": "20"},
            {"timestamp": "2026-04-01T10:00:00", "pct_before": "90", "pct_after": "15"},
        ]
        from html_gen import generate_pumpout_costs_table
        result = generate_pumpout_costs_table()
        self.assertIn("2025", result)
        self.assertIn("2026", result)


if __name__ == "__main__":
    unittest.main()
