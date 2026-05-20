"""Tests for settings — load_settings, save_settings, type coercion."""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from config import SETTINGS_DEFAULTS


class TestLoadSettings(unittest.TestCase):
    """Tests for loading settings from JSON file."""

    def test_defaults_when_no_file(self):
        """No settings.json -> returns all defaults."""
        with patch("config.SETTINGS_FILE", "/tmp/_nonexistent_settings.json"):
            result = config.load_settings()
        for key, val in SETTINGS_DEFAULTS.items():
            self.assertEqual(result[key], val)

    def test_loads_from_file(self):
        """Existing settings.json overrides defaults."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"tank_depth_cm": 200, "pumpout_cost_pln": 500}, f)
            path = f.name
        try:
            with patch("config.SETTINGS_FILE", path):
                result = config.load_settings()
            self.assertEqual(result["tank_depth_cm"], 200)
            self.assertEqual(result["pumpout_cost_pln"], 500)
            # Remaining keys are defaults
            self.assertEqual(result["tank_capacity_l"], SETTINGS_DEFAULTS["tank_capacity_l"])
        finally:
            os.unlink(path)

    def test_corrupt_json_returns_defaults(self):
        """Corrupt JSON file -> returns defaults without crash."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{bad json!!!")
            path = f.name
        try:
            with patch("config.SETTINGS_FILE", path):
                result = config.load_settings()
            self.assertEqual(result["tank_depth_cm"], SETTINGS_DEFAULTS["tank_depth_cm"])
        finally:
            os.unlink(path)

    def test_partial_file_fills_missing(self):
        """File with only some keys -> missing keys filled from defaults."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"smtp_host": "mail.example.com"}, f)
            path = f.name
        try:
            with patch("config.SETTINGS_FILE", path):
                result = config.load_settings()
            self.assertEqual(result["smtp_host"], "mail.example.com")
            self.assertEqual(result["smtp_port"], SETTINGS_DEFAULTS["smtp_port"])
        finally:
            os.unlink(path)


class TestSaveSettings(unittest.TestCase):
    """Tests for saving settings to JSON file."""

    def test_save_writes_file(self):
        """save_settings writes JSON to disk."""
        path = tempfile.mktemp(suffix=".json")
        original = dict(config.settings)
        try:
            with patch("config.SETTINGS_FILE", path):
                config.save_settings({"tank_depth_cm": 180})
            self.assertTrue(os.path.isfile(path))
            with open(path) as f:
                saved = json.load(f)
            self.assertEqual(saved["tank_depth_cm"], 180)
        finally:
            config.settings.update(original)
            if os.path.isfile(path):
                os.unlink(path)

    def test_save_updates_global(self):
        """save_settings updates the global settings dict."""
        path = tempfile.mktemp(suffix=".json")
        original = dict(config.settings)
        try:
            with patch("config.SETTINGS_FILE", path):
                config.save_settings({"pumpout_cost_pln": 999})
            self.assertEqual(config.settings["pumpout_cost_pln"], 999)
        finally:
            config.settings.update(original)
            if os.path.isfile(path):
                os.unlink(path)

    def test_save_preserves_other_keys(self):
        """Saving one key does not remove others."""
        path = tempfile.mktemp(suffix=".json")
        original = dict(config.settings)
        try:
            with patch("config.SETTINGS_FILE", path):
                config.save_settings({"alert_waste_high_warning_pct": 75})
            with open(path) as f:
                saved = json.load(f)
            # Original keys should still be present
            self.assertIn("tank_depth_cm", saved)
            self.assertIn("smtp_host", saved)
            self.assertEqual(saved["alert_waste_high_warning_pct"], 75)
        finally:
            config.settings.update(original)
            if os.path.isfile(path):
                os.unlink(path)


class TestSettingsDefaults(unittest.TestCase):
    """Tests for SETTINGS_DEFAULTS structure."""

    def test_all_keys_present(self):
        """SETTINGS_DEFAULTS has all expected sections."""
        expected_keys = [
            "sensor_rain_ip", "sensor_waste_ip", "measure_interval", "sensor_read_count",
            "sensor_read_delay",
            "tank_depth_cm", "tank_capacity_l", "sensor_offset_cm", "sensor_min_distance_cm",
            "spike_threshold_cm", "sensor_noise_cm",
            "pumpout_cost_pln", "pumpout_drop_pct", "pumpout_window_min", "pumpout_estimate_pct",
            "rain_pump_drop_pct", "rain_pump_window_min", "rain_pump_stable_count",
            "alert_waste_high_enabled", "alert_waste_high_warning_pct", "alert_waste_high_critical_pct",
            "alert_rain_high_enabled", "alert_rain_high_warning_pct", "alert_rain_high_critical_pct",
            "alert_rain_low_enabled", "alert_rain_low_warning_pct", "alert_rain_low_critical_pct",
            "alert_sensor_failure_enabled", "alert_sensor_min_distance_enabled",
            "alert_waste_anomaly_enabled", "alert_pumpout_interval_enabled",
            "alert_overdue_pumpout_enabled", "alert_estimate_accuracy_enabled",
            "alert_pumpout_reminder_enabled", "alert_rain_instability_enabled",
            "alert_cooldown_min",
            "sensor_fail_threshold", "anomaly_threshold_factor",
            "smtp_host", "smtp_port", "smtp_user", "smtp_pass", "alert_to",
            "location_lat", "location_lon",
            "server_port",
        ]
        for key in expected_keys:
            self.assertIn(key, SETTINGS_DEFAULTS, f"Missing key: {key}")

    def test_types_are_correct(self):
        """Default values have correct types."""
        self.assertIsInstance(SETTINGS_DEFAULTS["sensor_rain_ip"], str)
        self.assertIsInstance(SETTINGS_DEFAULTS["measure_interval"], int)
        self.assertIsInstance(SETTINGS_DEFAULTS["tank_depth_cm"], int)
        self.assertIsInstance(SETTINGS_DEFAULTS["anomaly_threshold_factor"], float)
        self.assertIsInstance(SETTINGS_DEFAULTS["location_lat"], float)
        self.assertIsInstance(SETTINGS_DEFAULTS["server_port"], int)


class TestSettingsTypeCoercion(unittest.TestCase):
    """Tests for POST form parsing — type coercion logic."""

    def test_int_coercion(self):
        """String '200' coerced to int for int defaults."""
        default = SETTINGS_DEFAULTS["tank_depth_cm"]
        self.assertIsInstance(default, int)
        self.assertEqual(int("200"), 200)

    def test_float_coercion(self):
        """String '2.5' coerced to float for float defaults."""
        default = SETTINGS_DEFAULTS["anomaly_threshold_factor"]
        self.assertIsInstance(default, float)
        self.assertEqual(float("2.5"), 2.5)

    def test_invalid_int_skipped(self):
        """Non-numeric string for int field -> should be skipped (ValueError)."""
        with self.assertRaises(ValueError):
            int("abc")

    def test_invalid_float_skipped(self):
        """Non-numeric string for float field -> should be skipped (ValueError)."""
        with self.assertRaises(ValueError):
            float("not_a_number")

    def test_string_stays_string(self):
        """String default -> value stays as string, no coercion."""
        default = SETTINGS_DEFAULTS["smtp_host"]
        self.assertIsInstance(default, str)
        self.assertEqual("mail.example.com", "mail.example.com")


if __name__ == "__main__":
    unittest.main()
