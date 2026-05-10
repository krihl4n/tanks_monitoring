"""Tests for server module — HTTP endpoints."""

import sys
import os
import json
import unittest
import threading
import http.client
from unittest.mock import patch, MagicMock
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import last_reading, sensor_monitor, rain_pump_detector

# Pre-fill last_reading so pages render without real sensors
last_reading.rain_dist = 50.0
last_reading.rain_pct = 60
last_reading.rain_level = 50.0
last_reading.rain_over = False
last_reading.waste_dist = 80.0
last_reading.waste_pct = 45
last_reading.waste_level = 70.0
last_reading.waste_over = False

from server import MyServer, ThreadingHTTPServer

TEST_PORT = 18765


def setUpModule():
    global _server, _thread
    _server = ThreadingHTTPServer(("127.0.0.1", TEST_PORT), MyServer)
    _thread = threading.Thread(target=_server.serve_forever, daemon=True)
    _thread.start()


def tearDownModule():
    _server.shutdown()


def _get(path):
    conn = http.client.HTTPConnection("127.0.0.1", TEST_PORT, timeout=5)
    conn.request("GET", path)
    resp = conn.getresponse()
    body = resp.read().decode("utf-8")
    conn.close()
    return resp.status, body


def _post(path, data=None, content_type="application/json"):
    conn = http.client.HTTPConnection("127.0.0.1", TEST_PORT, timeout=5)
    body = json.dumps(data) if data else ""
    conn.request("POST", path, body=body, headers={"Content-Type": content_type})
    resp = conn.getresponse()
    resp_body = resp.read().decode("utf-8")
    conn.close()
    return resp.status, resp_body


def _delete(path):
    conn = http.client.HTTPConnection("127.0.0.1", TEST_PORT, timeout=5)
    conn.request("DELETE", path)
    resp = conn.getresponse()
    body = resp.read().decode("utf-8")
    conn.close()
    return resp.status, body


class TestWastePage(unittest.TestCase):

    def test_root_returns_200(self):
        status, body = _get("/")
        self.assertEqual(status, 200)
        self.assertIn("Szambo", body)

    def test_root_with_range(self):
        status, body = _get("/?range=7d")
        self.assertEqual(status, 200)

    def test_root_with_24h(self):
        status, body = _get("/?range=24h")
        self.assertEqual(status, 200)


class TestRainwaterPage(unittest.TestCase):

    def test_rainwater_returns_200(self):
        status, body = _get("/rainwater")
        self.assertEqual(status, 200)
        self.assertIn("Deszcz", body)

    def test_rainwater_with_range(self):
        status, body = _get("/rainwater?range=365d")
        self.assertEqual(status, 200)


class TestDiagnosticsPage(unittest.TestCase):

    def test_diagnostics_returns_200(self):
        status, body = _get("/diagnostics")
        self.assertEqual(status, 200)
        self.assertIn("Diagnostyka", body)


class TestSettingsPage(unittest.TestCase):

    def test_settings_returns_200(self):
        status, body = _get("/settings")
        self.assertEqual(status, 200)
        self.assertIn("Ustawienia", body)


class TestChartsApi(unittest.TestCase):

    def test_waste_charts(self):
        status, body = _get("/api/charts?page=waste&range=7d")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertIn("waste_svg", data)
        self.assertIn("waste_daily_svg", data)
        self.assertIn("waste_weekday_svg", data)

    def test_rain_charts(self):
        status, body = _get("/api/charts?page=rain&range=30d")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertIn("rain_svg", data)
        self.assertIn("rainfall_svg", data)
        self.assertIn("rain_efficiency_svg", data)

    def test_default_range(self):
        status, body = _get("/api/charts?page=waste")
        self.assertEqual(status, 200)

    def test_daily_title_24h(self):
        status, body = _get("/api/charts?page=waste&range=24h")
        data = json.loads(body)
        self.assertEqual(data["waste_daily_title"], "Zużycie per godzina (litry)")

    def test_daily_title_365d(self):
        status, body = _get("/api/charts?page=waste&range=365d")
        data = json.loads(body)
        self.assertIn("miesiąc", data["waste_daily_title"])


class TestStaticFiles(unittest.TestCase):

    def test_css_returns_200(self):
        status, body = _get("/static/style.css")
        self.assertEqual(status, 200)
        self.assertIn("body", body)

    def test_missing_static_404(self):
        status, body = _get("/static/nonexistent.xyz")
        self.assertEqual(status, 404)

    def test_path_traversal(self):
        status, body = _get("/static/../../config.py")
        # basename strips traversal, so it looks for "config.py" in static/
        self.assertEqual(status, 404)


class TestPlannedPumpout(unittest.TestCase):

    @patch("server.clear_planned_pumpout")
    @patch("server.save_planned_pumpout")
    def test_post_and_delete(self, mock_save, mock_clear):
        # POST
        status, body = _post("/api/planned-pumpout", {"date": "2026-06-15", "note": "test"})
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["ok"])
        mock_save.assert_called_once_with("2026-06-15", "test")

        # DELETE
        status, body = _delete("/api/planned-pumpout")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["ok"])
        mock_clear.assert_called_once()

    def test_post_invalid_date(self):
        status, body = _post("/api/planned-pumpout", {"date": "not-a-date", "note": ""})
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertFalse(data["ok"])

    def test_post_invalid_json(self):
        conn = http.client.HTTPConnection("127.0.0.1", TEST_PORT, timeout=5)
        conn.request("POST", "/api/planned-pumpout", body="not json",
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        body = json.loads(resp.read().decode("utf-8"))
        conn.close()
        self.assertFalse(body["ok"])


class TestSettingsPost(unittest.TestCase):

    @patch("server.save_settings")
    def test_post_settings(self, mock_save):
        conn = http.client.HTTPConnection("127.0.0.1", TEST_PORT, timeout=5)
        form_data = "tank_depth_cm=164&tank_capacity_l=10000"
        conn.request("POST", "/settings", body=form_data,
                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        resp = conn.getresponse()
        body = resp.read().decode("utf-8")
        conn.close()
        self.assertEqual(resp.status, 200)
        self.assertIn("Ustawienia zapisane", body)
        mock_save.assert_called_once()


class TestNotFound(unittest.TestCase):

    def test_post_unknown_404(self):
        status, _ = _post("/api/unknown", {})
        self.assertEqual(status, 404)

    def test_delete_unknown_404(self):
        status, _ = _delete("/api/unknown")
        self.assertEqual(status, 404)


class TestParseRange(unittest.TestCase):

    def test_all_ranges(self):
        """Verify all range values produce valid charts API responses."""
        for r in ["24h", "7d", "30d", "365d"]:
            status, body = _get(f"/api/charts?page=waste&range={r}")
            self.assertEqual(status, 200, f"Failed for range={r}")

    def test_invalid_range_defaults(self):
        status, body = _get("/api/charts?page=waste&range=xyz")
        self.assertEqual(status, 200)


class TestApiStatus(unittest.TestCase):

    def test_returns_json(self):
        status, body = _get("/api/status")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertIn("rain", data)
        self.assertIn("waste", data)
        self.assertIn("tank_capacity_l", data)

    def test_rain_fields(self):
        status, body = _get("/api/status")
        data = json.loads(body)
        rain = data["rain"]
        self.assertIn("pct", rain)
        self.assertIn("liters", rain)
        self.assertIn("distance_cm", rain)
        self.assertIn("overflow", rain)
        self.assertEqual(rain["pct"], 60)

    def test_waste_fields(self):
        status, body = _get("/api/status")
        data = json.loads(body)
        waste = data["waste"]
        self.assertEqual(waste["pct"], 45)
        self.assertIsNotNone(waste["liters"])


if __name__ == "__main__":
    unittest.main()
