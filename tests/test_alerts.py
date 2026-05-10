"""Tests for alerts module — pumpout detection, tank alerts, cooldowns."""

import sys
import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    settings,
    pumpout_detector, rain_pump_detector, sensor_monitor, alert_state,
)
from alerts import (
    detect_pumpout, detect_rain_pump, check_tank_alert,
    check_sensor_failure, check_waste_anomaly,
    check_rain_sensor_instability, INSTABILITY_SPREAD_THRESHOLD_CM,
)


class TestDetectPumpout(unittest.TestCase):

    def setUp(self):
        """Reset detekcji przed każdym testem."""
        pumpout_detector.recent_readings = []
        pumpout_detector.cooldown_until = None

    @patch("alerts.save_pumpout")
    @patch("alerts.send_pumpout_diagnostic")
    @patch("alerts.check_pumpout_interval_anomaly")
    @patch("alerts.check_estimate_accuracy")
    def test_large_drop_detected(self, mock_acc, mock_int, mock_diag, mock_save):
        """Spadek >= PUMPOUT_DROP_PCT -> wykrycie szambowozu."""
        now = datetime.now()
        for i in range(4):
            pumpout_detector.recent_readings.append(
                (now - timedelta(minutes=10-i), 80, 33)
            )
        detect_pumpout(80 - settings["pumpout_drop_pct"], 130)
        self.assertTrue(mock_save.called)
        self.assertTrue(mock_diag.called)

    @patch("alerts.save_pumpout")
    @patch("alerts.send_pumpout_diagnostic")
    @patch("alerts.check_pumpout_interval_anomaly")
    @patch("alerts.check_estimate_accuracy")
    def test_small_drop_ignored(self, mock_acc, mock_int, mock_diag, mock_save):
        """Spadek < PUMPOUT_DROP_PCT -> brak wykrycia."""
        now = datetime.now()
        for i in range(4):
            pumpout_detector.recent_readings.append(
                (now - timedelta(minutes=10-i), 60, 66)
            )
        detect_pumpout(60 - settings["pumpout_drop_pct"] + 1, 100)
        self.assertFalse(mock_save.called)

    @patch("alerts.save_pumpout")
    @patch("alerts.send_pumpout_diagnostic")
    @patch("alerts.check_pumpout_interval_anomaly")
    @patch("alerts.check_estimate_accuracy")
    def test_cooldown_blocks_detection(self, mock_acc, mock_int, mock_diag, mock_save):
        """W trakcie cooldownu -> brak wykrycia."""
        pumpout_detector.cooldown_until = datetime.now() + timedelta(minutes=30)
        detect_pumpout(20, 130)
        self.assertFalse(mock_save.called)
        self.assertEqual(len(pumpout_detector.recent_readings), 1)


class TestCheckTankAlert(unittest.TestCase):

    def setUp(self):
        alert_state.tank_last_sent = {}
        self._patcher = patch.dict(settings, {
            "alert_waste_high_enabled": True,
            "alert_rain_high_enabled": True,
            "alert_rain_low_enabled": True,
        })
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    # ─── Szambo: przepełnienie ────────────────────────────────────

    @patch("alerts.send_alert_email")
    def test_waste_warning(self, mock_send):
        check_tank_alert("Waste", settings["alert_waste_high_warning_pct"])
        mock_send.assert_called_once()
        self.assertEqual(mock_send.call_args[0][2], "warning")

    @patch("alerts.send_alert_email")
    def test_waste_critical(self, mock_send):
        check_tank_alert("Waste", settings["alert_waste_high_critical_pct"])
        mock_send.assert_called_once()
        self.assertEqual(mock_send.call_args[0][2], "critical")

    @patch("alerts.send_alert_email")
    def test_waste_below_warning_no_alert(self, mock_send):
        check_tank_alert("Waste", settings["alert_waste_high_warning_pct"] - 1)
        self.assertFalse(mock_send.called)

    @patch("alerts.send_alert_email")
    def test_waste_disabled_no_alert(self, mock_send):
        settings["alert_waste_high_enabled"] = False
        check_tank_alert("Waste", 95)
        self.assertFalse(mock_send.called)

    @patch("alerts.send_alert_email")
    def test_none_pct_no_alert(self, mock_send):
        check_tank_alert("Waste", None)
        self.assertFalse(mock_send.called)

    @patch("alerts.send_alert_email")
    def test_cooldown_blocks_same_level(self, mock_send):
        check_tank_alert("Waste", settings["alert_waste_high_warning_pct"])
        self.assertEqual(mock_send.call_count, 1)
        check_tank_alert("Waste", settings["alert_waste_high_warning_pct"])
        self.assertEqual(mock_send.call_count, 1)  # cooldown

    @patch("alerts.send_alert_email")
    def test_different_levels_independent_cooldown(self, mock_send):
        check_tank_alert("Waste", settings["alert_waste_high_warning_pct"])
        self.assertEqual(mock_send.call_count, 1)
        check_tank_alert("Waste", settings["alert_waste_high_critical_pct"])
        self.assertEqual(mock_send.call_count, 2)  # inny level = inny cooldown

    # ─── Deszczówka: przepełnienie ────────────────────────────────

    @patch("alerts.send_alert_email")
    def test_rain_high_warning(self, mock_send):
        settings["alert_rain_low_warning_pct"] = 0  # wyłącz niski próg
        settings["alert_rain_low_critical_pct"] = 0
        check_tank_alert("Rainwater", settings["alert_rain_high_warning_pct"])
        mock_send.assert_called_once()
        self.assertEqual(mock_send.call_args[0][2], "warning")
        self.assertEqual(mock_send.call_args[0][3], "high")

    @patch("alerts.send_alert_email")
    def test_rain_high_disabled_no_alert(self, mock_send):
        settings["alert_rain_high_enabled"] = False
        settings["alert_rain_low_warning_pct"] = 0
        settings["alert_rain_low_critical_pct"] = 0
        check_tank_alert("Rainwater", 95)
        self.assertFalse(mock_send.called)

    # ─── Deszczówka: niski poziom ─────────────────────────────────

    @patch("alerts.send_alert_email")
    def test_rain_low_warning(self, mock_send):
        check_tank_alert("Rainwater", settings["alert_rain_low_warning_pct"])
        # Powinien wysłać alert low
        calls = [c for c in mock_send.call_args_list if c[0][3] == "low"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0][2], "warning")

    @patch("alerts.send_alert_email")
    def test_rain_low_critical(self, mock_send):
        check_tank_alert("Rainwater", settings["alert_rain_low_critical_pct"])
        calls = [c for c in mock_send.call_args_list if c[0][3] == "low"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0][2], "critical")

    @patch("alerts.send_alert_email")
    def test_rain_low_disabled_no_alert(self, mock_send):
        settings["alert_rain_low_enabled"] = False
        settings["alert_rain_high_enabled"] = False
        check_tank_alert("Rainwater", 5)
        self.assertFalse(mock_send.called)


class TestCheckSensorFailure(unittest.TestCase):

    def setUp(self):
        sensor_monitor.fail_count = {"Rainwater": 0, "Waste": 0}
        sensor_monitor.fail_alerted = {"Rainwater": False, "Waste": False}
        self._patcher = patch.dict(settings, {"alert_sensor_failure_enabled": True})
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    @patch("alerts.send_sensor_failure_email")
    def test_consecutive_failures_trigger_alert(self, mock_send):
        for _ in range(settings["sensor_fail_threshold"]):
            check_sensor_failure("Waste", None)
        self.assertTrue(mock_send.called)

    @patch("alerts.send_sensor_failure_email")
    def test_single_failure_no_alert(self, mock_send):
        check_sensor_failure("Waste", None)
        self.assertFalse(mock_send.called)

    @patch("alerts.send_sensor_recovery_email")
    @patch("alerts.send_sensor_failure_email")
    def test_recovery_after_failure(self, mock_fail, mock_recovery):
        for _ in range(settings["sensor_fail_threshold"]):
            check_sensor_failure("Waste", None)
        # Czujnik wraca
        check_sensor_failure("Waste", 100)
        self.assertTrue(mock_recovery.called)

    @patch("alerts.send_sensor_failure_email")
    def test_good_reading_resets_counter(self, mock_send):
        check_sensor_failure("Waste", None)
        check_sensor_failure("Waste", None)
        check_sensor_failure("Waste", 100)  # reset
        check_sensor_failure("Waste", None)
        self.assertFalse(mock_send.called)  # za mało awarii po resecie


class TestCheckWasteAnomaly(unittest.TestCase):

    def setUp(self):
        alert_state.anomaly_last_sent = None
        self._patcher = patch.dict(settings, {"alert_waste_anomaly_enabled": True})
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    @patch("alerts.send_anomaly_email")
    @patch("alerts.calc_daily_waste_gain")
    @patch("alerts.load_history")
    def test_anomaly_detected(self, mock_load, mock_gain, mock_send):
        mock_load.return_value = [{"timestamp": "2026-04-30T10:00:00"}]
        today = datetime.now().strftime("%Y-%m-%d")
        # Średnia 30 dni = 100 l, dziś = 300 l (3x)
        past_data = {f"2026-04-{d:02d}": 100 for d in range(1, 25)}
        past_data[today] = 300
        mock_gain.return_value = past_data
        check_waste_anomaly()
        self.assertTrue(mock_send.called)

    @patch("alerts.send_anomaly_email")
    @patch("alerts.calc_daily_waste_gain")
    @patch("alerts.load_history")
    def test_normal_usage_no_alert(self, mock_load, mock_gain, mock_send):
        mock_load.return_value = [{"timestamp": "2026-04-30T10:00:00"}]
        today = datetime.now().strftime("%Y-%m-%d")
        past_data = {f"2026-04-{d:02d}": 100 for d in range(1, 25)}
        past_data[today] = 100  # normalne zużycie
        mock_gain.return_value = past_data
        check_waste_anomaly()
        self.assertFalse(mock_send.called)


class TestDetectRainPump(unittest.TestCase):

    def setUp(self):
        """Reset detektora przed każdym testem."""
        rain_pump_detector.recent_readings = []
        rain_pump_detector.pumping = False
        rain_pump_detector.pump_start = None
        rain_pump_detector.pct_before = None
        rain_pump_detector.stable_count = 0
        rain_pump_detector.last_pump_end = None
        rain_pump_detector.last_pump_start = None
        rain_pump_detector.last_pct_before = None
        self._patcher = patch.dict(settings, {
            "rain_pump_drop_pct": 5,
            "rain_pump_window_min": 15,
            "rain_pump_stable_count": 2,
            "sensor_noise_cm": 2,
        })
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    @patch("alerts.save_rain_usage")
    def test_pump_detected_and_saved(self, mock_save):
        """Spadek >= 5pp w oknie 15 min -> wykrycie pompowania, stabilizacja -> zapis."""
        now = datetime.now()
        with patch("alerts.datetime") as mock_dt:
            for i, pct in enumerate([80, 78, 76, 74, 72]):
                mock_dt.now.return_value = now + timedelta(minutes=i)
                detect_rain_pump(pct)

            # Teraz stabilizacja
            for i in range(settings["rain_pump_stable_count"]):
                mock_dt.now.return_value = now + timedelta(minutes=5 + i)
                detect_rain_pump(72)

        self.assertTrue(mock_save.called)
        args = mock_save.call_args[0]
        self.assertEqual(args[3], 80)   # pct_before
        self.assertEqual(args[4], 72)   # pct_after

    @patch("alerts.save_rain_usage")
    def test_small_drop_not_detected(self, mock_save):
        """Spadek < 5pp -> brak detekcji."""
        now = datetime.now()
        with patch("alerts.datetime") as mock_dt:
            for i, pct in enumerate([80, 79, 78, 77, 76]):
                mock_dt.now.return_value = now + timedelta(minutes=i)
                detect_rain_pump(pct)
        self.assertFalse(rain_pump_detector.pumping)
        self.assertFalse(mock_save.called)

    @patch("alerts.save_rain_usage")
    def test_none_reading_ignored(self, mock_save):
        """None odczyt -> brak detekcji."""
        detect_rain_pump(None)
        self.assertEqual(len(rain_pump_detector.recent_readings), 0)
        self.assertFalse(mock_save.called)

    @patch("alerts.save_rain_usage")
    def test_unstable_reading_ignored(self, mock_save):
        """Odczyt z rain_unstable=True nie wchodzi do bufora."""
        now = datetime.now()
        with patch("alerts.datetime") as mock_dt:
            mock_dt.now.return_value = now
            detect_rain_pump(80, rain_unstable=True)
        self.assertEqual(len(rain_pump_detector.recent_readings), 0)

    @patch("alerts.save_rain_usage")
    def test_pump_not_saved_until_stable(self, mock_save):
        """Pompowanie nie jest zapisywane dopóki trwa spadek."""
        now = datetime.now()
        with patch("alerts.datetime") as mock_dt:
            for i, pct in enumerate([80, 76, 72, 68, 64]):
                mock_dt.now.return_value = now + timedelta(minutes=i)
                detect_rain_pump(pct)
        self.assertTrue(rain_pump_detector.pumping)
        self.assertFalse(mock_save.called)  # jeszcze nie stabilny

    @patch("alerts.save_rain_usage")
    def test_merge_close_pumpings(self, mock_save):
        """Dwa pompowania < 10 min od siebie -> scalenie."""
        now = datetime.now()
        with patch("alerts.datetime") as mock_dt:
            # Pierwsze pompowanie: 80 -> 70
            for i, pct in enumerate([80, 76, 72, 70]):
                mock_dt.now.return_value = now + timedelta(minutes=i)
                detect_rain_pump(pct)
            # Stabilizacja
            for i in range(2):
                mock_dt.now.return_value = now + timedelta(minutes=4 + i)
                detect_rain_pump(70)
            self.assertEqual(mock_save.call_count, 1)

            # Drugie pompowanie 5 min później: 70 -> 60
            for i, pct in enumerate([70, 66, 62, 60]):
                mock_dt.now.return_value = now + timedelta(minutes=8 + i)
                detect_rain_pump(pct)
            # Stabilizacja
            for i in range(2):
                mock_dt.now.return_value = now + timedelta(minutes=12 + i)
                detect_rain_pump(60)

        # Drugie wywołanie save_rain_usage powinno mieć merge_last=True
        self.assertEqual(mock_save.call_count, 2)
        kwargs = mock_save.call_args[1]
        self.assertTrue(kwargs.get("merge_last"))


class TestCheckRainSensorInstability(unittest.TestCase):

    def setUp(self):
        alert_state.tank_last_sent = {}
        self._patcher = patch.dict(settings, {"alert_rain_instability_enabled": True})
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    @patch("alerts.save_alert_log")
    @patch("alerts.send_email")
    @patch("alerts.load_rain_instability", return_value=[])
    @patch("alerts.save_rain_instability")
    @patch("alerts.load_rainfall")
    def test_stable_returns_false(self, mock_rain, mock_save, mock_load, mock_email, mock_log):
        """Spread <= threshold -> False, no save."""
        result = check_rain_sensor_instability(2, 1)
        self.assertFalse(result)
        mock_save.assert_not_called()

    @patch("alerts.save_alert_log")
    @patch("alerts.send_email")
    @patch("alerts.load_rain_instability", return_value=[])
    @patch("alerts.save_rain_instability")
    @patch("alerts.load_rainfall")
    def test_none_spread_returns_false(self, mock_rain, mock_save, mock_load, mock_email, mock_log):
        result = check_rain_sensor_instability(None, None)
        self.assertFalse(result)

    @patch("alerts.save_alert_log")
    @patch("alerts.send_email")
    @patch("alerts.load_rain_instability", return_value=[])
    @patch("alerts.save_rain_instability")
    @patch("alerts.load_rainfall")
    def test_unstable_returns_true(self, mock_rain, mock_save, mock_load, mock_email, mock_log):
        """Spread > threshold -> True, saves instability."""
        mock_rain.return_value = [{"precipitation_mm": "3.0"}]
        result = check_rain_sensor_instability(8, 1)
        self.assertTrue(result)
        mock_save.assert_called_once()

    @patch("alerts.save_alert_log")
    @patch("alerts.send_email")
    @patch("alerts.load_rain_instability", return_value=[])
    @patch("alerts.save_rain_instability")
    @patch("alerts.load_rainfall")
    def test_unstable_sends_email(self, mock_rain, mock_save, mock_load, mock_email, mock_log):
        """First instability event sends email."""
        mock_rain.return_value = [{"precipitation_mm": "5.0"}]
        check_rain_sensor_instability(10, 1)
        mock_email.assert_called_once()

    @patch("alerts.save_alert_log")
    @patch("alerts.send_email")
    @patch("alerts.save_rain_instability")
    @patch("alerts.load_rainfall")
    def test_cooldown_blocks_email(self, mock_rain, mock_save, mock_email, mock_log):
        """Second call within 2h should not send email (file-based cooldown)."""
        mock_rain.return_value = [{"precipitation_mm": "3.0"}]
        now = datetime.now().isoformat()
        # Simulate: load returns 2 recent entries (current + previous within 2h)
        with patch("alerts.load_rain_instability", return_value=[
            {"timestamp": now, "rain_spread_cm": "8.0"},
            {"timestamp": now, "rain_spread_cm": "8.0"},
        ]):
            check_rain_sensor_instability(8, 1)
        mock_email.assert_not_called()

    @patch("alerts.save_alert_log")
    @patch("alerts.send_email")
    @patch("alerts.save_rain_instability")
    @patch("alerts.load_rainfall")
    def test_cooldown_still_returns_true(self, mock_rain, mock_save, mock_email, mock_log):
        """Even with cooldown, should return True (unstable)."""
        mock_rain.return_value = []
        now = datetime.now().isoformat()
        with patch("alerts.load_rain_instability", return_value=[
            {"timestamp": now, "rain_spread_cm": "8.0"},
            {"timestamp": now, "rain_spread_cm": "8.0"},
        ]):
            result = check_rain_sensor_instability(8, 1)
        self.assertTrue(result)

    @patch("alerts.save_alert_log")
    @patch("alerts.send_email")
    @patch("alerts.load_rain_instability", return_value=[])
    @patch("alerts.save_rain_instability")
    @patch("alerts.load_rainfall")
    def test_no_rain_still_unstable(self, mock_rain, mock_save, mock_load, mock_email, mock_log):
        """No rain but unstable -> still returns True and sends email."""
        mock_rain.return_value = []
        result = check_rain_sensor_instability(10, 2)
        self.assertTrue(result)
        mock_email.assert_called_once()
        # Email should mention lack of correlation
        body = mock_email.call_args[0][1]
        self.assertIn("Brak korelacji", body)

    @patch("alerts.save_alert_log")
    @patch("alerts.send_email")
    @patch("alerts.load_rain_instability", return_value=[])
    @patch("alerts.save_rain_instability")
    @patch("alerts.load_rainfall")
    def test_rain_correlation_in_email(self, mock_rain, mock_save, mock_load, mock_email, mock_log):
        """Rain + unstable -> email mentions correlation."""
        mock_rain.return_value = [{"precipitation_mm": "8.0"}]
        check_rain_sensor_instability(12, 1)
        body = mock_email.call_args[0][1]
        self.assertIn("Korelacja z opadami: TAK", body)
        self.assertIn("intensywny", body)


if __name__ == "__main__":
    unittest.main()
