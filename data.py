"""Operacje plikowe — odczyt i zapis CSV, szablony, pliki statyczne."""

import csv
import json
import logging
import os
from datetime import datetime, timedelta
from string import Template

from config import (
    _BASE_DIR, DATA_FILE, PUMPOUT_FILE, RAINFALL_FILE, ESTIMATE_FILE,
    RAIN_USAGE_FILE, ALERT_LOG_FILE, RAIN_INSTABILITY_FILE,
    PLANNED_PUMPOUT_FILE, alert_state,
)

logger = logging.getLogger("tanks")


# ─── Pomiary (measurements.csv) ──────────────────────────────────


def save_measurement_row(rain_dist, rain_pct, waste_dist, waste_pct, rain_unstable=False):
    """Zapisuje pojedynczy wiersz pomiaru do CSV."""
    file_exists = os.path.isfile(DATA_FILE)
    with open(DATA_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["timestamp", "rain_distance_cm", "rain_pct",
                             "waste_distance_cm", "waste_pct", "rain_unstable"])
        writer.writerow([
            datetime.now().isoformat(timespec="seconds"),
            round(rain_dist) if rain_dist is not None else "",
            rain_pct if rain_pct is not None else "",
            round(waste_dist) if waste_dist is not None else "",
            waste_pct if waste_pct is not None else "",
            "1" if rain_unstable else "",
        ])


def load_history(days=30):
    """Wczytuje dane z CSV z ostatnich N dni."""
    rows = []
    if not os.path.isfile(DATA_FILE):
        return rows
    cutoff = datetime.now() - timedelta(days=days)
    with open(DATA_FILE, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                ts = datetime.fromisoformat(row["timestamp"])
                if ts >= cutoff:
                    rows.append(row)
            except (ValueError, KeyError):
                continue
    return rows


# ─── Szambo — wywozy (pumpouts.csv) ──────────────────────────────

def save_pumpout(when, pct_before, pct_after):
    """Zapisuje wizytę szambowozu do pliku CSV."""
    file_exists = os.path.isfile(PUMPOUT_FILE)
    with open(PUMPOUT_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["timestamp", "pct_before", "pct_after"])
        writer.writerow([when.isoformat(timespec="seconds"), pct_before, pct_after])


def load_pumpouts():
    """Wczytuje historię wizyt szambowozu."""
    rows = []
    if not os.path.isfile(PUMPOUT_FILE):
        return rows
    with open(PUMPOUT_FILE, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


# ─── Estymacje (estimates.csv) ────────────────────────────────────

def save_estimate(estimated_full_date_str):
    """Zapisuje aktualną estymację do pliku CSV (max raz dziennie).

    Pozwala później porównać predykcję z rzeczywistą datą wizyty szambowozu.
    """
    now = datetime.now()
    if alert_state.last_estimate_save and (now - alert_state.last_estimate_save) < timedelta(hours=23):
        return

    alert_state.last_estimate_save = now
    file_exists = os.path.isfile(ESTIMATE_FILE)
    with open(ESTIMATE_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["timestamp", "estimated_full_date"])
        writer.writerow([now.isoformat(timespec="seconds"), estimated_full_date_str])


# ─── Opady (rainfall.csv) ────────────────────────────────────────

def save_rainfall(precip_mm):
    """Zapisuje dane o opadach do CSV."""
    if precip_mm is None:
        return
    file_exists = os.path.isfile(RAINFALL_FILE)
    with open(RAINFALL_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["timestamp", "precipitation_mm"])
        writer.writerow([datetime.now().isoformat(timespec="seconds"), precip_mm])


def load_rainfall(days=30):
    """Wczytuje historię opadów."""
    rows = []
    if not os.path.isfile(RAINFALL_FILE):
        return rows
    cutoff = datetime.now() - timedelta(days=days)
    with open(RAINFALL_FILE, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                ts = datetime.fromisoformat(row["timestamp"])
                if ts >= cutoff:
                    rows.append(row)
            except (ValueError, KeyError):
                continue
    return rows


# ─── Zużycie deszczówki (rain_usage.csv) ──────────────────────────

def save_rain_usage(start_time, duration_min, liters, pct_before, pct_after,
                    merge_last=False):
    """Zapisuje wykryte pompowanie deszczówki do CSV.

    merge_last=True: podmienia ostatni wiersz (scalanie pompowań).
    """
    header = ["timestamp", "duration_min", "liters", "pct_before", "pct_after"]

    if merge_last and os.path.isfile(RAIN_USAGE_FILE):
        # Wczytaj wszystkie wiersze, podmień ostatni
        rows = []
        with open(RAIN_USAGE_FILE, "r") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        if rows:
            rows[-1] = {
                "timestamp": start_time.isoformat(timespec="seconds"),
                "duration_min": str(round(duration_min)),
                "liters": str(round(liters)),
                "pct_before": str(pct_before),
                "pct_after": str(pct_after),
            }
        with open(RAIN_USAGE_FILE, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=header)
            writer.writeheader()
            writer.writerows(rows)
        return

    file_exists = os.path.isfile(RAIN_USAGE_FILE)
    with open(RAIN_USAGE_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(header)
        writer.writerow([
            start_time.isoformat(timespec="seconds"),
            round(duration_min),
            round(liters),
            pct_before,
            pct_after,
        ])


def load_rain_usage():
    """Wczytuje historię pompowań deszczówki."""
    rows = []
    if not os.path.isfile(RAIN_USAGE_FILE):
        return rows
    with open(RAIN_USAGE_FILE, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


# ─── Szablony i pliki statyczne ───────────────────────────────────

def _load_template(name):
    """Ładuje szablon HTML z katalogu templates/."""
    path = os.path.join(_BASE_DIR, "templates", name)
    with open(path, encoding="utf-8") as f:
        return Template(f.read())


def _load_static(name):
    """Ładuje plik statyczny z katalogu static/."""
    path = os.path.join(_BASE_DIR, "static", name)
    with open(path, encoding="utf-8") as f:
        return f.read()


# ─── Log alertów ──────────────────────────────────────────────────

def save_alert_log(alert_type, tank_name, level, message):
    """Zapisuje wpis do logu alertów (alert_log.csv).

    Kolumny: timestamp, alert_type, tank_name, level, message
    """
    is_new = not os.path.isfile(ALERT_LOG_FILE)
    with open(ALERT_LOG_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(["timestamp", "alert_type", "tank_name", "level", "message"])
        writer.writerow([
            datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            alert_type, tank_name, level, message,
        ])


def load_alert_log(limit=50):
    """Ładuje ostatnie N wpisów z logu alertów (najnowsze najpierw)."""
    if not os.path.isfile(ALERT_LOG_FILE):
        return []
    with open(ALERT_LOG_FILE, newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    return rows[-limit:][::-1]


# ─── Planowany wywóz szamba ───────────────────────────────────────

def save_planned_pumpout(date_str, note=""):
    """Zapisuje planowaną datę wywozu szamba.

    date_str: "YYYY-MM-DD", note: opcjonalna notatka.
    """
    data = {"date": date_str, "note": note, "created": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")}
    with open(PLANNED_PUMPOUT_FILE, "w") as f:
        json.dump(data, f, indent=2)
    logger.info("Zapisano planowany wywóz: %s", date_str)


def load_planned_pumpout():
    """Ładuje planowaną datę wywozu. Zwraca dict lub None."""
    if not os.path.isfile(PLANNED_PUMPOUT_FILE):
        return None
    try:
        with open(PLANNED_PUMPOUT_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def clear_planned_pumpout():
    """Usuwa planowany wywóz (po wykonaniu)."""
    if os.path.isfile(PLANNED_PUMPOUT_FILE):
        os.unlink(PLANNED_PUMPOUT_FILE)
        logger.info("Planowany wywóz usunięty")


# ─── Niestabilność czujnika deszczówki ───────────────────────────


def _rain_intensity(precipitation_mm):
    """Klasyfikuje intensywność opadów na podstawie mm/h.

    Skala WMO (przybliżona):
      brak:        0 mm
      lekki:     0.1 – 2.5 mm/h
      umiarkowany: 2.5 – 7.5 mm/h
      intensywny:  7.5 – 50 mm/h
      ulewny:    > 50 mm/h
    """
    if precipitation_mm is None or precipitation_mm <= 0:
        return "brak"
    if precipitation_mm <= 2.5:
        return "lekki"
    if precipitation_mm <= 7.5:
        return "umiarkowany"
    if precipitation_mm <= 50:
        return "intensywny"
    return "ulewny"


def save_rain_instability(rain_spread, waste_spread, precipitation_mm):
    """Zapisuje zdarzenie niestabilności czujnika deszczówki.

    rain_spread: rozrzut odczytów deszczówki (cm)
    waste_spread: rozrzut odczytów szamba (cm) — dla porównania
    precipitation_mm: aktualne opady (mm) lub None
    """
    intensity = _rain_intensity(precipitation_mm)
    is_new = not os.path.isfile(RAIN_INSTABILITY_FILE)
    with open(RAIN_INSTABILITY_FILE, "a", newline="") as f:
        w = csv.writer(f)
        if is_new:
            w.writerow(["timestamp", "rain_spread_cm", "waste_spread_cm",
                         "precipitation_mm", "rain_intensity"])
        w.writerow([
            datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            round(rain_spread, 1) if rain_spread is not None else "",
            round(waste_spread, 1) if waste_spread is not None else "",
            round(precipitation_mm, 1) if precipitation_mm is not None else "",
            intensity,
        ])


def load_rain_instability(limit=100):
    """Ładuje ostatnie zdarzenia niestabilności czujnika.

    Zwraca listę dict (najnowsze pierwsze), max `limit` wpisów.
    """
    if not os.path.isfile(RAIN_INSTABILITY_FILE):
        return []
    try:
        with open(RAIN_INSTABILITY_FILE, newline="") as f:
            rows = list(csv.DictReader(f))
        return list(reversed(rows[-limit:]))
    except (IOError, csv.Error):
        return []
