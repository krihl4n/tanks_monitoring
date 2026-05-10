"""Testy modułu data.py — zapis i odczyt CSV, szablony."""

import sys
import os
import csv
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import alert_state


class TestSaveMeasurementRow(unittest.TestCase):

    def setUp(self):
        self.tmpfile = tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False
        )
        self.tmpfile.close()
        self.patcher = patch("data.DATA_FILE", self.tmpfile.name)
        self.patcher.start()
        # Usuwamy plik żeby przetestować tworzenie nagłówka
        os.unlink(self.tmpfile.name)

    def tearDown(self):
        self.patcher.stop()
        if os.path.exists(self.tmpfile.name):
            os.unlink(self.tmpfile.name)

    def test_creates_header_on_new_file(self):
        from data import save_measurement_row
        save_measurement_row(100, 50, 80, 60)
        with open(self.tmpfile.name) as f:
            reader = csv.reader(f)
            header = next(reader)
        self.assertEqual(header[0], "timestamp")
        self.assertIn("rain_pct", header)

    def test_writes_values(self):
        from data import save_measurement_row
        save_measurement_row(100, 50, 80, 60)
        with open(self.tmpfile.name) as f:
            reader = csv.DictReader(f)
            row = next(reader)
        self.assertEqual(row["rain_distance_cm"], "100")
        self.assertEqual(row["rain_pct"], "50")
        self.assertEqual(row["waste_distance_cm"], "80")
        self.assertEqual(row["waste_pct"], "60")

    def test_none_values_saved_as_empty(self):
        from data import save_measurement_row
        save_measurement_row(None, None, None, None)
        with open(self.tmpfile.name) as f:
            reader = csv.DictReader(f)
            row = next(reader)
        self.assertEqual(row["rain_distance_cm"], "")
        self.assertEqual(row["rain_pct"], "")

    def test_appends_without_duplicate_header(self):
        from data import save_measurement_row
        save_measurement_row(100, 50, 80, 60)
        save_measurement_row(101, 51, 81, 61)
        with open(self.tmpfile.name) as f:
            lines = f.readlines()
        # 1 nagłówek + 2 wiersze danych
        self.assertEqual(len(lines), 3)


class TestLoadHistory(unittest.TestCase):

    def setUp(self):
        self.tmpfile = tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False
        )
        self.tmpfile.close()
        self.patcher = patch("data.DATA_FILE", self.tmpfile.name)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        if os.path.exists(self.tmpfile.name):
            os.unlink(self.tmpfile.name)

    def test_empty_file_returns_empty_list(self):
        os.unlink(self.tmpfile.name)
        from data import load_history
        self.assertEqual(load_history(), [])

    def test_filters_by_days(self):
        now = datetime.now()
        old = now - timedelta(days=60)
        recent = now - timedelta(days=5)
        with open(self.tmpfile.name, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "rain_pct", "waste_pct"])
            writer.writerow([old.isoformat(), "30", "40"])
            writer.writerow([recent.isoformat(), "50", "60"])
        from data import load_history
        rows = load_history(days=30)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["rain_pct"], "50")

    def test_invalid_rows_skipped(self):
        with open(self.tmpfile.name, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "rain_pct"])
            writer.writerow(["not-a-date", "50"])
            writer.writerow([datetime.now().isoformat(), "60"])
        from data import load_history
        rows = load_history(days=30)
        self.assertEqual(len(rows), 1)


class TestSavePumpout(unittest.TestCase):

    def setUp(self):
        self.tmpfile = tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False
        )
        self.tmpfile.close()
        os.unlink(self.tmpfile.name)
        self.patcher = patch("data.PUMPOUT_FILE", self.tmpfile.name)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        if os.path.exists(self.tmpfile.name):
            os.unlink(self.tmpfile.name)

    def test_saves_pumpout(self):
        from data import save_pumpout
        now = datetime.now()
        save_pumpout(now, 85, 20)
        with open(self.tmpfile.name) as f:
            reader = csv.DictReader(f)
            row = next(reader)
        self.assertEqual(row["pct_before"], "85")
        self.assertEqual(row["pct_after"], "20")


class TestLoadPumpouts(unittest.TestCase):

    def setUp(self):
        self.tmpfile = tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False
        )
        self.tmpfile.close()
        self.patcher = patch("data.PUMPOUT_FILE", self.tmpfile.name)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        if os.path.exists(self.tmpfile.name):
            os.unlink(self.tmpfile.name)

    def test_no_file_returns_empty(self):
        os.unlink(self.tmpfile.name)
        from data import load_pumpouts
        self.assertEqual(load_pumpouts(), [])

    def test_loads_rows(self):
        with open(self.tmpfile.name, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "pct_before", "pct_after"])
            writer.writerow(["2026-04-01T10:00:00", "85", "20"])
            writer.writerow(["2026-04-15T10:00:00", "90", "15"])
        from data import load_pumpouts
        rows = load_pumpouts()
        self.assertEqual(len(rows), 2)


class TestSaveEstimate(unittest.TestCase):

    def setUp(self):
        self.tmpfile = tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False
        )
        self.tmpfile.close()
        os.unlink(self.tmpfile.name)
        self.patcher = patch("data.ESTIMATE_FILE", self.tmpfile.name)
        self.patcher.start()
        alert_state.last_estimate_save = None

    def tearDown(self):
        self.patcher.stop()
        if os.path.exists(self.tmpfile.name):
            os.unlink(self.tmpfile.name)

    def test_saves_estimate(self):
        from data import save_estimate
        save_estimate("2026-05-15")
        with open(self.tmpfile.name) as f:
            reader = csv.DictReader(f)
            row = next(reader)
        self.assertEqual(row["estimated_full_date"], "2026-05-15")

    def test_cooldown_blocks_duplicate(self):
        from data import save_estimate
        save_estimate("2026-05-15")
        save_estimate("2026-05-16")  # powinien być zablokowany przez cooldown
        with open(self.tmpfile.name) as f:
            lines = f.readlines()
        # nagłówek + 1 wiersz
        self.assertEqual(len(lines), 2)


class TestSaveRainfall(unittest.TestCase):

    def setUp(self):
        self.tmpfile = tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False
        )
        self.tmpfile.close()
        os.unlink(self.tmpfile.name)
        self.patcher = patch("data.RAINFALL_FILE", self.tmpfile.name)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        if os.path.exists(self.tmpfile.name):
            os.unlink(self.tmpfile.name)

    def test_saves_rainfall(self):
        from data import save_rainfall
        save_rainfall(5.2)
        with open(self.tmpfile.name) as f:
            reader = csv.DictReader(f)
            row = next(reader)
        self.assertEqual(row["precipitation_mm"], "5.2")

    def test_none_skipped(self):
        from data import save_rainfall
        save_rainfall(None)
        self.assertFalse(os.path.exists(self.tmpfile.name))


class TestLoadRainfall(unittest.TestCase):

    def setUp(self):
        self.tmpfile = tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False
        )
        self.tmpfile.close()
        self.patcher = patch("data.RAINFALL_FILE", self.tmpfile.name)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        if os.path.exists(self.tmpfile.name):
            os.unlink(self.tmpfile.name)

    def test_filters_old_data(self):
        now = datetime.now()
        old = now - timedelta(days=60)
        recent = now - timedelta(days=2)
        with open(self.tmpfile.name, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "precipitation_mm"])
            writer.writerow([old.isoformat(), "10"])
            writer.writerow([recent.isoformat(), "5"])
        from data import load_rainfall
        rows = load_rainfall(days=30)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["precipitation_mm"], "5")


class TestSaveRainUsage(unittest.TestCase):

    def setUp(self):
        self.tmpfile = tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False
        )
        self.tmpfile.close()
        os.unlink(self.tmpfile.name)
        self.patcher = patch("data.RAIN_USAGE_FILE", self.tmpfile.name)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        if os.path.exists(self.tmpfile.name):
            os.unlink(self.tmpfile.name)

    def test_saves_rain_usage(self):
        from data import save_rain_usage
        now = datetime.now()
        save_rain_usage(now, 45, 3000, 80, 50)
        with open(self.tmpfile.name) as f:
            reader = csv.DictReader(f)
            row = next(reader)
        self.assertEqual(row["duration_min"], "45")
        self.assertEqual(row["liters"], "3000")
        self.assertEqual(row["pct_before"], "80")
        self.assertEqual(row["pct_after"], "50")

    def test_appends_multiple(self):
        from data import save_rain_usage
        now = datetime.now()
        save_rain_usage(now, 30, 2000, 70, 50)
        save_rain_usage(now, 15, 1000, 50, 40)
        with open(self.tmpfile.name) as f:
            lines = f.readlines()
        # nagłówek + 2 wiersze
        self.assertEqual(len(lines), 3)


class TestLoadRainUsage(unittest.TestCase):

    def setUp(self):
        self.tmpfile = tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False
        )
        self.tmpfile.close()
        self.patcher = patch("data.RAIN_USAGE_FILE", self.tmpfile.name)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        if os.path.exists(self.tmpfile.name):
            os.unlink(self.tmpfile.name)

    def test_no_file_returns_empty(self):
        os.unlink(self.tmpfile.name)
        from data import load_rain_usage
        self.assertEqual(load_rain_usage(), [])

    def test_loads_rows(self):
        with open(self.tmpfile.name, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "duration_min", "liters", "pct_before", "pct_after"])
            writer.writerow(["2026-05-01T10:00:00", "45", "3000", "80", "50"])
            writer.writerow(["2026-05-01T14:00:00", "20", "1500", "60", "45"])
        from data import load_rain_usage
        rows = load_rain_usage()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["liters"], "3000")


class TestLoadTemplate(unittest.TestCase):

    def test_loads_existing_template(self):
        from data import _load_template
        # Powinien załadować templates/waste.html
        tpl = _load_template("waste.html")
        self.assertIsNotNone(tpl)


class TestLoadStatic(unittest.TestCase):

    def test_loads_existing_css(self):
        from data import _load_static
        css = _load_static("style.css")
        self.assertIn("body", css)


class TestRainIntensity(unittest.TestCase):

    def test_none(self):
        from data import _rain_intensity
        self.assertEqual(_rain_intensity(None), "brak")

    def test_zero(self):
        from data import _rain_intensity
        self.assertEqual(_rain_intensity(0), "brak")

    def test_light(self):
        from data import _rain_intensity
        self.assertEqual(_rain_intensity(1.0), "lekki")
        self.assertEqual(_rain_intensity(2.5), "lekki")

    def test_moderate(self):
        from data import _rain_intensity
        self.assertEqual(_rain_intensity(3.0), "umiarkowany")
        self.assertEqual(_rain_intensity(7.5), "umiarkowany")

    def test_heavy(self):
        from data import _rain_intensity
        self.assertEqual(_rain_intensity(10.0), "intensywny")
        self.assertEqual(_rain_intensity(50.0), "intensywny")

    def test_extreme(self):
        from data import _rain_intensity
        self.assertEqual(_rain_intensity(51.0), "ulewny")


class TestSaveLoadRainInstability(unittest.TestCase):

    def setUp(self):
        self.tmpfile = tempfile.NamedTemporaryFile(
            suffix=".csv", delete=False)
        self.tmpfile.close()
        os.unlink(self.tmpfile.name)
        self.patcher = patch("data.RAIN_INSTABILITY_FILE", self.tmpfile.name)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        if os.path.exists(self.tmpfile.name):
            os.unlink(self.tmpfile.name)

    def test_save_creates_file(self):
        from data import save_rain_instability
        save_rain_instability(8.0, 1.0, 3.5)
        self.assertTrue(os.path.exists(self.tmpfile.name))
        with open(self.tmpfile.name) as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["rain_spread_cm"], "8.0")
        self.assertEqual(rows[0]["waste_spread_cm"], "1.0")
        self.assertEqual(rows[0]["precipitation_mm"], "3.5")
        self.assertEqual(rows[0]["rain_intensity"], "umiarkowany")

    def test_save_none_values(self):
        from data import save_rain_instability
        save_rain_instability(6.0, None, None)
        with open(self.tmpfile.name) as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        self.assertEqual(rows[0]["waste_spread_cm"], "")
        self.assertEqual(rows[0]["precipitation_mm"], "")
        self.assertEqual(rows[0]["rain_intensity"], "brak")

    def test_load_empty(self):
        from data import load_rain_instability
        self.assertEqual(load_rain_instability(), [])

    def test_load_returns_newest_first(self):
        from data import save_rain_instability, load_rain_instability
        save_rain_instability(6.0, 1.0, 1.0)
        save_rain_instability(10.0, 1.0, 8.0)
        rows = load_rain_instability()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["rain_spread_cm"], "10.0")  # newest first

    def test_load_limit(self):
        from data import save_rain_instability, load_rain_instability
        for i in range(5):
            save_rain_instability(float(i + 6), 1.0, 1.0)
        rows = load_rain_instability(limit=3)
        self.assertEqual(len(rows), 3)


class TestSaveMeasurementRowUnstable(unittest.TestCase):

    def setUp(self):
        self.tmpfile = tempfile.NamedTemporaryFile(
            suffix=".csv", delete=False)
        self.tmpfile.close()
        os.unlink(self.tmpfile.name)
        self.patcher = patch("data.DATA_FILE", self.tmpfile.name)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        if os.path.exists(self.tmpfile.name):
            os.unlink(self.tmpfile.name)

    def test_unstable_flag_written(self):
        from data import save_measurement_row
        save_measurement_row(141, 14, 128, 22, rain_unstable=True)
        with open(self.tmpfile.name) as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        self.assertEqual(rows[0]["rain_unstable"], "1")

    def test_stable_flag_empty(self):
        from data import save_measurement_row
        save_measurement_row(141, 14, 128, 22, rain_unstable=False)
        with open(self.tmpfile.name) as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        self.assertEqual(rows[0]["rain_unstable"], "")

    def test_default_not_unstable(self):
        from data import save_measurement_row
        save_measurement_row(141, 14, 128, 22)
        with open(self.tmpfile.name) as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        self.assertEqual(rows[0]["rain_unstable"], "")


if __name__ == "__main__":
    unittest.main()
