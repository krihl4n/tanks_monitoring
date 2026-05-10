"""
Monitoring zbiorników — deszczówka i szambo.

Serwer HTTP + pętla pomiarowa. Szczegóły implementacji w modułach:
  config.py       — stałe, parametry, stan aplikacji
  sensors.py      — odczyt i filtrowanie czujników
  data.py         — zapis/odczyt CSV, szablony
  calculations.py — obliczenia, estymacja, pogoda, święta
  alerts.py       — email, detekcja szambowozu, alerty
  html_gen.py     — badge, prognoza, tabele, wrappery wykresów
  charts.py       — wykresy SVG

Interfejs WWW:
  /           — szambo (napełnienie, estymacja, wykresy, koszty)
  /rainwater  — deszczówka (napełnienie, prognoza, wykresy opadów)
  /static/    — pliki statyczne (CSS) z cache
"""

from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs
import io
import json
import logging
import logging.handlers
import os
import threading
import time
from datetime import datetime, timedelta

from config import (
    settings, SETTINGS_DEFAULTS, save_settings,
    DATA_FILE, LOG_FILE,
    HOST_NAME, PUMPOUT_EXPECTED_INTERVAL_DAYS,
    sensor_filter, alert_state, sensor_monitor, rain_pump_detector,
    last_reading,
)

# Logging — rotacja: max 1 MB na plik, 3 pliki historyczne
logger = logging.getLogger("tanks")
logger.setLevel(logging.INFO)
_handler = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
logger.addHandler(_handler)
_console = logging.StreamHandler()
_console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"))
logger.addHandler(_console)


from sensors import calc_percent, read_sensors, filter_value
from data import (
    load_history, load_pumpouts, load_rainfall, load_rain_usage,
    save_pumpout, save_rainfall, save_estimate, save_measurement_row,
    load_alert_log, load_planned_pumpout, save_planned_pumpout, clear_planned_pumpout,
    load_rain_instability,
    _load_template, _load_static,
)
from calculations import (
    calc_liters, find_min_sensor_record,
    calc_daily_stats, calc_daily_waste_gain, estimate_pumpout_date,
    fetch_rainfall,
)
from alerts import (
    detect_pumpout, detect_rain_pump, check_tank_alert,
    check_sensor_failure, check_sensor_min_distance,
    check_waste_anomaly, check_overdue_pumpout, check_pumpout_reminder,
    check_rain_sensor_instability,
)
from html_gen import (
    generate_tank_status_badge, generate_forecast_html,
    generate_rainfall_chart, generate_seasonal_rainwater_chart,
    generate_waste_daily_chart, generate_waste_weekday_chart,
    generate_rain_efficiency_chart, generate_svg_chart,
    generate_monthly_table, generate_pumpout_costs_table,
)


def save_measurement(rain_dist, waste_dist, rain_spread=None, waste_spread=None):
    """Filtruje odczyty, zapisuje pomiar i uruchamia alerty."""
    # Filtrowanie
    rain_filtered = filter_value(rain_dist, sensor_filter.history_rain)
    waste_filtered = filter_value(waste_dist, sensor_filter.history_waste)

    rain_pct = calc_percent(rain_filtered)[0] if rain_filtered is not None else None
    waste_pct = calc_percent(waste_filtered)[0] if waste_filtered is not None else None

    # Cache ostatniego pomiaru (do wyświetlania na stronach)
    if rain_filtered is not None:
        r_pct, r_level, r_over = calc_percent(rain_filtered)
        last_reading.rain_dist = rain_filtered
        last_reading.rain_pct = r_pct
        last_reading.rain_level = r_level
        last_reading.rain_over = r_over
    else:
        last_reading.rain_dist = None

    if waste_filtered is not None:
        w_pct, w_level, w_over = calc_percent(waste_filtered)
        last_reading.waste_dist = waste_filtered
        last_reading.waste_pct = w_pct
        last_reading.waste_level = w_level
        last_reading.waste_over = w_over
    else:
        last_reading.waste_dist = None

    # Detekcja szambowozu
    if waste_pct is not None and waste_filtered is not None:
        detect_pumpout(waste_pct, waste_filtered)

    # Niestabilność czujnika deszczówki (przed detekcją pompowania)
    rain_unstable = check_rain_sensor_instability(rain_spread, waste_spread)

    # Detekcja pompowania deszczówki (pomija niestabilne odczyty)
    detect_rain_pump(rain_pct, rain_unstable)

    # Zapis do CSV (po detekcji niestabilności — flaga rain_unstable)
    save_measurement_row(rain_filtered, rain_pct, waste_filtered, waste_pct,
                         rain_unstable=rain_unstable)

    logger.info("Pomiar: deszczówka=%s cm (%s%%), szambo=%s cm (%s%%)%s",
                round(rain_filtered) if rain_filtered is not None else "-",
                rain_pct if rain_pct is not None else "-",
                round(waste_filtered) if waste_filtered is not None else "-",
                waste_pct if waste_pct is not None else "-",
                " [NIESTABILNY]" if rain_unstable else "")

    # Alerty
    check_tank_alert("Rainwater", rain_pct)
    check_tank_alert("Waste", waste_pct)
    check_sensor_failure("Rainwater", rain_dist)
    check_sensor_failure("Waste", waste_dist)
    check_sensor_min_distance("Rainwater", rain_dist)
    check_sensor_min_distance("Waste", waste_dist)
    check_waste_anomaly()
    check_overdue_pumpout()
    check_pumpout_reminder()

    # Estymacja (raz dziennie)
    try:
        est = estimate_pumpout_date()
        if est and est != "teraz" and est.get("full_date"):
            save_estimate(est["full_date"])
    except Exception as e:
        logger.warning("Błąd zapisu estymacji: %s", e)


def rainfall_loop():
    """Wątek pobierania opadów co godzinę."""
    while True:
        precip = fetch_rainfall()
        save_rainfall(precip)
        if precip is not None and precip > 0:
            logger.info("Opady: %.1f mm", precip)
        alert_state.last_rainfall_fetch = datetime.now()
        time.sleep(3600)  # co godzinę


def measurement_loop():
    """Wątek cyklicznego pomiaru."""
    while True:
        rain_dist, waste_dist, rain_spread, waste_spread = read_sensors(accurate=True)
        save_measurement(rain_dist, waste_dist, rain_spread, waste_spread)
        time.sleep(settings["measure_interval"])



class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """Serwer HTTP obsługujący żądania w osobnych wątkach."""
    daemon_threads = True


class MyServer(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/rainwater":
            self.serve_rainwater(parsed)
        elif parsed.path == "/settings":
            self.serve_settings()
        elif parsed.path == "/diagnostics":
            self.serve_diagnostics()
        elif parsed.path == "/api/charts":
            self.serve_charts_api(parsed)
        elif parsed.path == "/api/status":
            self.serve_api_status()
        elif parsed.path.startswith("/static/"):
            self.serve_static(parsed.path)
        else:
            self.serve_waste(parsed)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/settings":
            self.serve_settings_post()
        elif parsed.path == "/api/test-email":
            self.serve_test_email()
        elif parsed.path == "/api/planned-pumpout":
            self.serve_planned_pumpout_post()
        else:
            self.send_error(404)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/planned-pumpout":
            self.serve_planned_pumpout_delete()
        else:
            self.send_error(404)

    def serve_static(self, path):
        """Serwuje pliki statyczne z katalogu static/."""
        # Zabezpieczenie przed path traversal
        filename = os.path.basename(path)
        try:
            content = _load_static(filename)
        except FileNotFoundError:
            self.send_error(404)
            return
        content_type = "text/css" if filename.endswith(".css") else "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(content.encode("utf-8"))

    def _read_sensors(self):
        """Zwraca dane obu zbiorników z ostatniego pomiaru w tle (bez odpytywania czujników)."""
        rain_err = "" if last_reading.rain_dist is not None else '<div class="error-msg">Błąd czujnika deszczówki</div>'
        waste_err = "" if last_reading.waste_dist is not None else '<div class="error-msg">Błąd czujnika szamba</div>'

        return {
            "rain_pct": last_reading.rain_pct or 0,
            "rain_level": last_reading.rain_level or 0,
            "rain_dist": last_reading.rain_dist or 0,
            "rain_err": rain_err,
            "rain_over": last_reading.rain_over,
            "waste_pct": last_reading.waste_pct or 0,
            "waste_level": last_reading.waste_level or 0,
            "waste_dist": last_reading.waste_dist or 0,
            "waste_err": waste_err,
            "waste_over": last_reading.waste_over,
        }

    def _parse_range(self, parsed):
        qs = parse_qs(parsed.query)
        range_key = qs.get("range", ["30d"])[0]
        range_map = {
            "24h":  (1,   "hour"),
            "7d":   (7,   "day"),
            "30d":  (30,  "day"),
            "365d": (365, "month"),
        }
        days, time_unit = range_map.get(range_key, (30, "day"))
        return range_key, days, time_unit

    def serve_charts_api(self, parsed):
        """Endpoint API zwracający wykresy jako JSON (bez przeładowania strony)."""
        qs = parse_qs(parsed.query)
        page = qs.get("page", ["waste"])[0]
        range_key, days, time_unit = self._parse_range(parsed)

        if page == "waste":
            rows = load_history(days=days)
            if days <= 1:
                daily_title = "Zużycie per godzina (litry)"
            elif days > 90:
                daily_title = "Średnie dzienne zużycie per miesiąc (litry/dzień)"
            else:
                daily_title = "Dzienne zużycie (litry)"
            data = {
                "waste_svg": generate_svg_chart(rows, "waste_pct", "#a67c52", "#a67c52", time_unit),
                "waste_daily_svg": generate_waste_daily_chart(days=days),
                "waste_daily_title": daily_title,
                "waste_weekday_svg": generate_waste_weekday_chart(days=days),
            }
        else:
            rows = load_history(days=days)
            data = {
                "rain_svg": generate_svg_chart(rows, "rain_pct", "#48cae4", "#48cae4", time_unit),
                "rainfall_svg": generate_rainfall_chart(days=days),
                "rain_efficiency_svg": generate_rain_efficiency_chart(days=days),
            }

        payload = json.dumps(data)
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(payload.encode("utf-8"))

    def serve_settings(self, save_msg=""):
        """Strona ustawień — formularz z aktualnymi wartościami."""
        substitutions = {k: v for k, v in settings.items()}
        substitutions["save_msg"] = save_msg
        # Generuj atrybuty checked dla checkboxów
        for key, default_val in SETTINGS_DEFAULTS.items():
            if isinstance(default_val, bool):
                substitutions[f"{key}_checked"] = "checked" if settings.get(key, default_val) else ""
        html = _load_template("settings.html").substitute(substitutions)
        self._send_html(html)

    def serve_settings_post(self):
        """Obsługa zapisu ustawień z formularza."""
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8")
        from urllib.parse import parse_qs as _pqs
        form = _pqs(body)

        new_settings = {}
        for key, default_val in SETTINGS_DEFAULTS.items():
            if isinstance(default_val, bool):
                # Checkbox: obecny w form = True, brak = False
                new_settings[key] = key in form
                continue
            raw = form.get(key, [None])[0]
            if raw is None:
                continue
            try:
                if isinstance(default_val, int):
                    new_settings[key] = int(raw)
                elif isinstance(default_val, float):
                    new_settings[key] = float(raw)
                else:
                    new_settings[key] = raw
            except (ValueError, TypeError):
                continue

        save_settings(new_settings)
        logger.info("Ustawienia zapisane: %d kluczy", len(new_settings))
        self.serve_settings(
            save_msg='<div class="save-msg">Ustawienia zapisane.</div>'
        )

    def serve_test_email(self):
        """Wysyła testowy email i zwraca JSON z wynikiem."""
        import json as _json
        try:
            from alerts import send_email
            send_email(
                "Test — monitoring zbiorników",
                f"To jest testowy email z systemu monitoringu zbiorników.\n"
                f"Data: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
                f"Konfiguracja SMTP działa poprawnie."
            )
            resp = _json.dumps({"ok": True}).encode()
        except Exception as e:
            logger.error("Test email failed: %s", e)
            resp = _json.dumps({"ok": False, "error": str(e)}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(resp)

    def _json_response(self, data):
        """Wysyła odpowiedź JSON."""
        import json as _json
        resp = _json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(resp)

    def serve_diagnostics(self):
        """Strona diagnostyki — status czujników, alerty, statystyki."""
        # Status czujników
        def sensor_status(tank):
            if sensor_monitor.fail_alerted[tank]:
                return '<span style="color:#e74c3c;">Awaria</span>'
            elif sensor_monitor.fail_count[tank] > 0:
                return '<span style="color:#e9c46a;">Niestabilny</span>'
            return '<span style="color:#2a9d8f;">OK</span>'

        # Statystyki szambowozu
        pumpouts = load_pumpouts()
        pumpout_count = len(pumpouts)
        if pumpouts:
            try:
                last_ts = datetime.fromisoformat(pumpouts[-1]["timestamp"])
                last_pumpout_date = last_ts.strftime("%d.%m.%Y %H:%M")
                days_since = (datetime.now() - last_ts).days
            except (ValueError, KeyError):
                last_pumpout_date = "—"
                days_since = "—"

            if len(pumpouts) >= 2:
                intervals = []
                for i in range(1, len(pumpouts)):
                    try:
                        t1 = datetime.fromisoformat(pumpouts[i-1]["timestamp"])
                        t2 = datetime.fromisoformat(pumpouts[i]["timestamp"])
                        intervals.append((t2 - t1).days)
                    except (ValueError, KeyError):
                        continue
                avg_interval = f"{sum(intervals) / len(intervals):.0f}" if intervals else "—"
            else:
                avg_interval = "—"
        else:
            last_pumpout_date = "brak danych"
            days_since = "—"
            avg_interval = "—"

        # Estymacja
        rows = load_history(days=30)
        est = estimate_pumpout_date()
        pumpout_estimate = est if est else "brak danych"

        # Anomalie zużycia
        daily_gain = calc_daily_waste_gain(rows)
        today_key = datetime.now().strftime("%Y-%m-%d")
        today_usage = daily_gain.get(today_key, 0)
        past_values = [v for k, v in daily_gain.items() if k != today_key and v > 0]
        avg_30d = sum(past_values) / len(past_values) if past_values else 0
        factor = today_usage / max(avg_30d, 1)
        threshold = settings["anomaly_threshold_factor"]
        if factor >= threshold:
            anomaly_status = f'<span style="color:#e74c3c;">Anomalia ({factor:.1f}x)</span>'
        elif factor >= threshold * 0.75:
            anomaly_status = f'<span style="color:#e9c46a;">Podwyższone ({factor:.1f}x)</span>'
        else:
            anomaly_status = f'<span style="color:#2a9d8f;">Norma ({factor:.1f}x)</span>'

        # Pompowanie deszczówki
        rain_usage = load_rain_usage()
        rain_pump_active = "Tak" if rain_pump_detector.pumping else "Nie"
        rain_pump_count = len(rain_usage)
        if rain_usage:
            last_rain_pump = rain_usage[-1].get("timestamp", "—")
            try:
                last_rain_pump = datetime.fromisoformat(last_rain_pump).strftime("%d.%m.%Y %H:%M")
            except (ValueError, TypeError):
                pass
        else:
            last_rain_pump = "brak danych"

        # Planowany wywóz
        planned = load_planned_pumpout()
        if planned:
            planned_date_value = planned.get("date", "")
            planned_note_value = planned.get("note", "")
            try:
                pd = datetime.strptime(planned["date"], "%Y-%m-%d")
                days_to = (pd - datetime.now()).days
                if days_to < 0:
                    color = "#e74c3c"
                    label = f"Zaplanowano na {pd.strftime('%d.%m.%Y')} ({abs(days_to)} dni temu!)"
                elif days_to == 0:
                    color = "#e9c46a"
                    label = f"Zaplanowano na dziś ({pd.strftime('%d.%m.%Y')})"
                else:
                    color = "#2a9d8f"
                    label = f"Zaplanowano na {pd.strftime('%d.%m.%Y')} (za {days_to} dni)"
                planned_html = f'<span style="color:{color};font-weight:bold;">{label}</span>'
                if planned_note_value:
                    planned_html += f'<br><span style="font-size:0.85em;color:#aaa;">{planned_note_value}</span>'
            except ValueError:
                planned_html = ""
        else:
            planned_date_value = ""
            planned_note_value = ""
            planned_html = '<span style="color:#aaa;">Brak zaplanowanego wywozu</span>'

        # Historia alertów
        alerts = load_alert_log(limit=50)
        if alerts:
            alert_rows = ""
            for a in alerts:
                ts = a.get("timestamp", "")
                try:
                    ts = datetime.fromisoformat(ts).strftime("%d.%m.%Y %H:%M")
                except (ValueError, TypeError):
                    pass
                atype = a.get("alert_type", "")
                level = a.get("level", "")
                msg = a.get("message", "")
                level_color = {"critical": "#e74c3c", "warning": "#e9c46a", "info": "#2a9d8f"}.get(level, "#ccc")
                alert_rows += f'<tr><td>{ts}</td><td>{atype}</td><td style="color:{level_color}">{level}</td><td>{msg}</td></tr>\n'
            alert_log_html = (
                '<table class="diag-table"><thead>'
                '<tr><th>Data</th><th>Typ</th><th>Poziom</th><th>Wiadomość</th></tr>'
                f'</thead><tbody>{alert_rows}</tbody></table>'
            )
        else:
            alert_log_html = '<p style="color:#aaa;">Brak zapisanych alertów</p>'

        # Niestabilność czujnika deszczówki
        instability_events = load_rain_instability(limit=50)
        if instability_events:
            # Statystyki
            rain_correlated = sum(1 for e in instability_events
                                  if e.get("precipitation_mm") and float(e.get("precipitation_mm", 0) or 0) > 0.5)
            total_events = len(instability_events)
            correlation_pct = round(rain_correlated / total_events * 100) if total_events else 0

            inst_rows = ""
            for e in instability_events[:30]:  # ostatnie 30
                ts = e.get("timestamp", "")
                try:
                    ts = datetime.fromisoformat(ts).strftime("%d.%m.%Y %H:%M")
                except (ValueError, TypeError):
                    pass
                r_spread = e.get("rain_spread_cm", "—")
                w_spread = e.get("waste_spread_cm", "—")
                precip = e.get("precipitation_mm", "—")
                intensity = e.get("rain_intensity", "—")
                # Koloruj intensywność
                int_colors = {"brak": "#aaa", "lekki": "#2a9d8f", "umiarkowany": "#e9c46a",
                               "intensywny": "#e76f51", "ulewny": "#e74c3c"}
                int_color = int_colors.get(intensity, "#ccc")
                inst_rows += (
                    f'<tr><td>{ts}</td><td>{r_spread}</td><td>{w_spread}</td>'
                    f'<td>{precip}</td><td style="color:{int_color}">{intensity}</td></tr>\n'
                )
            instability_html = (
                f'<p style="margin-bottom:10px;">Zdarzeń: <b>{total_events}</b>, '
                f'skorelowanych z deszczem: <b>{rain_correlated}</b> ({correlation_pct}%)</p>'
                '<table class="diag-table"><thead>'
                '<tr><th>Data</th><th>Rozrzut deszcz. (cm)</th><th>Rozrzut szambo (cm)</th>'
                '<th>Opady (mm/h)</th><th>Intensywność</th></tr>'
                f'</thead><tbody>{inst_rows}</tbody></table>'
            )
        else:
            instability_html = '<p style="color:#aaa;">Brak zarejestrowanych zdarzeń niestabilności</p>'

        substitutions = {
            "waste_sensor_status": sensor_status("Waste"),
            "waste_fail_count": sensor_monitor.fail_count["Waste"],
            "waste_min_ever": sensor_monitor.min_ever.get("Waste") or "—",
            "rain_sensor_status": sensor_status("Rainwater"),
            "rain_fail_count": sensor_monitor.fail_count["Rainwater"],
            "rain_min_ever": sensor_monitor.min_ever.get("Rainwater") or "—",
            "last_pumpout_date": last_pumpout_date,
            "days_since_pumpout": days_since,
            "avg_pumpout_interval": avg_interval,
            "pumpout_count": pumpout_count,
            "pumpout_estimate": pumpout_estimate,
            "planned_pumpout_html": planned_html,
            "planned_date_value": planned_date_value,
            "planned_note_value": planned_note_value,
            "today_usage": f"{today_usage:.0f}",
            "avg_30d_usage": f"{avg_30d:.0f}",
            "usage_factor": f"{factor:.1f}",
            "anomaly_status": anomaly_status,
            "rain_pump_active": rain_pump_active,
            "last_rain_pump": last_rain_pump,
            "rain_pump_count": rain_pump_count,
            "instability_html": instability_html,
            "alert_log_html": alert_log_html,
        }
        html = _load_template("diagnostics.html").substitute(substitutions)
        self._send_html(html)

    def serve_planned_pumpout_post(self):
        """Zapisuje planowaną datę wywozu szamba."""
        import json as _json
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8")
        try:
            data = _json.loads(body)
            date_str = data.get("date", "")
            note = data.get("note", "")
            # Walidacja daty
            datetime.strptime(date_str, "%Y-%m-%d")
            save_planned_pumpout(date_str, note)
            self._json_response({"ok": True})
        except (ValueError, _json.JSONDecodeError) as e:
            self._json_response({"ok": False, "error": str(e)})

    def serve_planned_pumpout_delete(self):
        """Usuwa planowany wywóz szamba."""
        clear_planned_pumpout()
        self._json_response({"ok": True})

    def serve_waste(self, parsed):
        s = self._read_sensors()
        range_key, days, time_unit = self._parse_range(parsed)

        waste_pct = s["waste_pct"]
        waste_over = s["waste_over"]
        waste_err = s["waste_err"]

        # Status badge
        raw_waste = s["waste_dist"] if waste_err == "" else None
        waste_status = generate_tank_status_badge("Waste", waste_pct, raw_waste)
        waste_sensor_note = '<div style="color:#e9c46a;font-size:0.8em;margin-top:6px;">Powyżej zakresu czujnika</div>' if waste_over else ""

        # Min record
        min_records = find_min_sensor_record()
        waste_mr = min_records.get("waste")
        waste_min_record = f"{waste_mr[0]} cm ({waste_mr[1]})" if waste_mr and waste_mr[0] <= settings["sensor_min_distance_cm"] else ""
        waste_min_line = f'<br><span style="color:#e9c46a;font-size:0.85em;">Min. odczyt: {waste_min_record}</span>' if waste_min_record else ""

        # Daily stat
        _, waste_daily = calc_daily_stats()

        def format_daily_waste(value):
            if value is None:
                return '<div class="daily-stat"><span class="stat-label">Zużycie / dzień</span><span class="stat-value stat-na">brak danych</span></div>'
            abs_val = abs(value)
            if value > 0:
                return '<div class="daily-stat"><span class="stat-label">Zużycie / dzień</span><span class="stat-value stat-falling">~%d l przybywa</span></div>' % abs_val
            return '<div class="daily-stat"><span class="stat-label">Zużycie / dzień</span><span class="stat-value stat-rising">~%d l ubywa</span></div>' % abs_val

        waste_daily_stat = format_daily_waste(waste_daily)

        # Pumpout estimate
        est = estimate_pumpout_date()
        if est == "teraz":
            pumpout_estimate = '<div class="pumpout-estimate"><span class="label">Szambo</span><span class="date">Wezwij szambowóz teraz!</span></div>'
        elif est:
            warning_html = ""
            if est.get("warning"):
                warning_html = f'<br><span style="color:#e9c46a;font-size:0.85em;">&#9888; {est["warning"]}</span>'
            planned_ok_html = ""
            if est.get("planned_ok"):
                planned_ok_html = f'<br><span style="color:#2a9d8f;font-size:0.85em;">&#10003; {est["planned_ok"]}</span>'
            pumpout_estimate = (
                '<div class="pumpout-estimate">'
                f'<span class="label">Szacowane zapełnienie ({settings["pumpout_estimate_pct"]}%)</span>'
                f'<span class="date">{est["full_date"]}</span>'
                f'<br><span class="label" style="margin-top:8px;">Wywóz najpóźniej</span>'
                f'<span class="date">{est["service_date"]}</span>'
                f'<br><span class="label" style="margin-top:8px;">Zamów szambowóz</span>'
                f'<span class="date">{est["order_date"]}</span>'
                f'{warning_html}'
                f'{planned_ok_html}'
                '</div>'
            )
        else:
            pumpout_estimate = '<div class="pumpout-estimate" style="color:#666;">Za mało danych do estymacji (min. 3 dni pomiarów)</div>'

        # Pumpout list
        pumpouts = load_pumpouts()
        if pumpouts:
            items = ""
            for p in reversed(pumpouts[-10:]):
                try:
                    dt = datetime.fromisoformat(p["timestamp"])
                    date_str = dt.strftime("%d.%m.%Y %H:%M")
                except (ValueError, KeyError):
                    date_str = p.get("timestamp", "?")
                before = p.get("pct_before", "?")
                after = p.get("pct_after", "?")
                items += f'<li><span class="date">{date_str}</span> — <span class="drop">{before}% &rarr; {after}%</span></li>'
            pumpout_list = f'<ul class="pumpout-list">{items}</ul>'
        else:
            pumpout_list = '<p class="pumpout-empty">Brak zarejestrowanych wizyt</p>'

        # Charts
        rows = load_history(days=days)
        waste_svg = generate_svg_chart(rows, "waste_pct", "#a67c52", "#a67c52", time_unit)
        waste_daily_svg = generate_waste_daily_chart(days=days)
        if days <= 1:
            waste_daily_title = "Zużycie per godzina (litry)"
        elif days > 90:
            waste_daily_title = "Średnie dzienne zużycie per miesiąc (litry/dzień)"
        else:
            waste_daily_title = "Dzienne zużycie (litry)"
        waste_weekday_svg = generate_waste_weekday_chart(days=days)
        pumpout_costs_table = generate_pumpout_costs_table()

        html = _load_template("waste.html").substitute(
            waste_pct=f">{waste_pct}" if waste_over else str(waste_pct),
            waste_pct_num=waste_pct,
            waste_level_cm=s["waste_level"],
            waste_distance_cm=s["waste_dist"],
            waste_liters=calc_liters(waste_pct),
            waste_min_line=waste_min_line,
            waste_error=waste_err + waste_sensor_note,
            waste_status=waste_status,
            waste_daily_stat=waste_daily_stat,
            pumpout_estimate=pumpout_estimate,
            pumpout_list=pumpout_list,
            waste_svg=waste_svg,
            waste_daily_svg=waste_daily_svg,
            waste_daily_title=waste_daily_title,
            waste_weekday_svg=waste_weekday_svg,
            pumpout_costs_table=pumpout_costs_table,
            pumpout_cost_pln=settings["pumpout_cost_pln"],
            active_24h="active" if range_key == "24h" else "",
            active_7d="active" if range_key == "7d" else "",
            active_30d="active" if range_key == "30d" else "",
            active_365d="active" if range_key == "365d" else "",
        )
        self._send_html(html)

    def serve_rainwater(self, parsed):
        s = self._read_sensors()
        range_key, days, time_unit = self._parse_range(parsed)

        rain_pct = s["rain_pct"]
        rain_over = s["rain_over"]
        rain_err = s["rain_err"]

        # Status badge
        raw_rain = s["rain_dist"] if rain_err == "" else None
        rain_status = generate_tank_status_badge("Rainwater", rain_pct, raw_rain)
        rain_sensor_note = '<div style="color:#e9c46a;font-size:0.8em;margin-top:6px;">Powyżej zakresu czujnika</div>' if rain_over else ""

        # Min record
        min_records = find_min_sensor_record()
        rain_mr = min_records.get("rain")
        rain_min_record = f"{rain_mr[0]} cm ({rain_mr[1]})" if rain_mr and rain_mr[0] <= settings["sensor_min_distance_cm"] else ""
        rain_min_line = f'<br><span style="color:#e9c46a;font-size:0.85em;">Min. odczyt: {rain_min_record}</span>' if rain_min_record else ""

        # Daily stat
        rain_daily, _ = calc_daily_stats()

        def format_daily_rain(value):
            if value is None:
                return '<div class="daily-stat"><span class="stat-label">Zmiana / dzień</span><span class="stat-value stat-na">brak danych</span></div>'
            abs_val = abs(value)
            if value > 0:
                return '<div class="daily-stat"><span class="stat-label">Zmiana / dzień</span><span class="stat-value stat-rising">~%d l napływa</span></div>' % abs_val
            return '<div class="daily-stat"><span class="stat-label">Zmiana / dzień</span><span class="stat-value stat-falling">~%d l ubywa</span></div>' % abs_val

        rain_daily_stat = format_daily_rain(rain_daily)

        # Forecast
        forecast_html = generate_forecast_html()

        # Charts
        rows = load_history(days=days)
        rain_svg = generate_svg_chart(rows, "rain_pct", "#48cae4", "#48cae4", time_unit)
        rainfall_svg = generate_rainfall_chart(days=days)
        rain_efficiency_svg = generate_rain_efficiency_chart(days=days)
        seasonal_rain_svg = generate_seasonal_rainwater_chart()

        # Lista pompowań deszczówki
        usages = load_rain_usage()
        if usages:
            items = ""
            total_liters = 0
            for u in reversed(usages[-10:]):
                try:
                    dt = datetime.fromisoformat(u["timestamp"])
                    date_str = dt.strftime("%d.%m.%Y %H:%M")
                except (ValueError, KeyError):
                    date_str = u.get("timestamp", "?")
                liters = u.get("liters", "?")
                duration = u.get("duration_min", "?")
                pct_b = u.get("pct_before", "?")
                pct_a = u.get("pct_after", "?")
                try:
                    total_liters += int(liters)
                except (ValueError, TypeError):
                    pass
                items += (
                    f'<li><span class="date">{date_str}</span> — '
                    f'<span class="usage-amount">{liters} l</span> '
                    f'<span class="usage-detail">({duration} min, {pct_b}% &rarr; {pct_a}%)</span></li>'
                )
            usage_list = f'<ul class="usage-list">{items}</ul>'
            usage_total = f'<div class="usage-total">Łącznie: {total_liters} l (ostatnie {len(usages)} pompowań)</div>'
        else:
            usage_list = '<p class="usage-empty">Brak zarejestrowanych pompowań</p>'
            usage_total = ''

        html = _load_template("rainwater.html").substitute(
            rain_pct=f">{rain_pct}" if rain_over else str(rain_pct),
            rain_pct_num=rain_pct,
            rain_level_cm=s["rain_level"],
            rain_distance_cm=s["rain_dist"],
            rain_liters=calc_liters(rain_pct),
            rain_min_line=rain_min_line,
            rain_error=rain_err + rain_sensor_note,
            rain_status=rain_status,
            rain_daily_stat=rain_daily_stat,
            forecast_html=forecast_html,
            rain_svg=rain_svg,
            rainfall_svg=rainfall_svg,
            rain_efficiency_svg=rain_efficiency_svg,
            seasonal_rain_svg=seasonal_rain_svg,
            usage_list=usage_list,
            usage_total=usage_total,
            active_24h="active" if range_key == "24h" else "",
            active_7d="active" if range_key == "7d" else "",
            active_30d="active" if range_key == "30d" else "",
            active_365d="active" if range_key == "365d" else "",
        )
        self._send_html(html)

    def _send_html(self, html):
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def _send_json(self, data, status=200):
        import json as _json
        body = _json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    # ─── API JSON ─────────────────────────────────────────────────

    def serve_api_status(self):
        """Aktualny stan obu zbiorników — do integracji z Home Assistant.

        GET /api/status

        Zwraca JSON:
        {
          "rain": {"pct", "liters", "distance_cm", "level_cm", "overflow"},
          "waste": {"pct", "liters", "distance_cm", "level_cm", "overflow"},
          "tank_capacity_l": int,
          "tank_depth_cm": int
        }
        """
        from calculations import calc_liters

        rain_liters = calc_liters(last_reading.rain_pct) if last_reading.rain_pct is not None else None
        waste_liters = calc_liters(last_reading.waste_pct) if last_reading.waste_pct is not None else None

        self._send_json({
            "rain": {
                "pct": last_reading.rain_pct,
                "liters": rain_liters,
                "distance_cm": last_reading.rain_dist,
                "level_cm": last_reading.rain_level,
                "overflow": last_reading.rain_over,
            },
            "waste": {
                "pct": last_reading.waste_pct,
                "liters": waste_liters,
                "distance_cm": last_reading.waste_dist,
                "level_cm": last_reading.waste_level,
                "overflow": last_reading.waste_over,
            },
            "tank_capacity_l": settings["tank_capacity_l"],
            "tank_depth_cm": settings["tank_depth_cm"],
        })


if __name__ == "__main__":
    # Uruchom wątek cyklicznego pomiaru
    t = threading.Thread(target=measurement_loop, daemon=True)
    t.start()
    logger.info("Pomiary uruchomione (co %d s), zapis do %s", settings["measure_interval"], DATA_FILE)

    # Uruchom wątek pobierania opadów
    t2 = threading.Thread(target=rainfall_loop, daemon=True)
    t2.start()
    logger.info("Monitoring opadów uruchomiony (co 1h), Kraków")

    webServer = ThreadingHTTPServer((HOST_NAME, settings["server_port"]), MyServer)
    logger.info("Serwer uruchomiony http://%s:%s", HOST_NAME, settings["server_port"])

    try:
        webServer.serve_forever()
    except KeyboardInterrupt:
        pass

    webServer.server_close()
    logger.info("Serwer zatrzymany.")
