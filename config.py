"""Konfiguracja i stałe systemu monitoringu zbiorników."""

import json
import os
from dataclasses import dataclass, field
from datetime import datetime

# ─── Ścieżki plików ──────────────────────────────────────────────

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DATA_FILE = os.path.join(_BASE_DIR, "measurements.csv")
PUMPOUT_FILE = os.path.join(_BASE_DIR, "pumpouts.csv")
RAINFALL_FILE = os.path.join(_BASE_DIR, "rainfall.csv")
ESTIMATE_FILE = os.path.join(_BASE_DIR, "estimates.csv")
RAIN_USAGE_FILE = os.path.join(_BASE_DIR, "rain_usage.csv")
ALERT_LOG_FILE = os.path.join(_BASE_DIR, "alert_log.csv")
RAIN_INSTABILITY_FILE = os.path.join(_BASE_DIR, "rain_instability.csv")
PLANNED_PUMPOUT_FILE = os.path.join(_BASE_DIR, "planned_pumpout.json")
SETTINGS_FILE = os.path.join(_BASE_DIR, "settings.json")
LOG_FILE = os.path.join(_BASE_DIR, "server.log")

# ─── Domyślne wartości ustawień ───────────────────────────────────

SETTINGS_DEFAULTS = {
    # Czujniki
    "sensor_rain_ip": "192.168.100.30",
    "sensor_waste_ip": "192.168.100.31",
    "measure_interval": 60,
    "sensor_read_count": 5,
    "sensor_read_delay": 2,

    # Zbiorniki
    "tank_depth_cm": 164,
    "tank_capacity_l": 9873,
    "sensor_min_distance_cm": 20,

    # Filtrowanie
    "spike_threshold_cm": 15,
    "sensor_noise_cm": 2,

    # Szambo
    "pumpout_cost_pln": 340,
    "pumpout_drop_pct": 50,
    "pumpout_window_min": 15,
    "pumpout_estimate_pct": 90,

    # Deszczówka — detekcja pompowania
    "rain_pump_drop_pct": 5,
    "rain_pump_window_min": 15,
    "rain_pump_stable_count": 2,

    # Alerty — przepełnienie szamba
    "alert_waste_high_enabled": True,
    "alert_waste_high_warning_pct": 80,
    "alert_waste_high_critical_pct": 90,
    # Alerty — przepełnienie deszczówki
    "alert_rain_high_enabled": True,
    "alert_rain_high_warning_pct": 80,
    "alert_rain_high_critical_pct": 90,
    # Alerty — niski poziom deszczówki
    "alert_rain_low_enabled": True,
    "alert_rain_low_warning_pct": 10,
    "alert_rain_low_critical_pct": 5,
    # Alerty — czujniki
    "alert_sensor_failure_enabled": True,
    "alert_sensor_min_distance_enabled": True,
    # Alerty — szambo
    "alert_waste_anomaly_enabled": True,
    "alert_pumpout_interval_enabled": True,
    "alert_overdue_pumpout_enabled": True,
    "alert_estimate_accuracy_enabled": True,
    "alert_pumpout_reminder_enabled": True,
    # Alerty — deszczówka
    "alert_rain_instability_enabled": True,
    # Alerty — ogólne
    "alert_cooldown_min": 60,
    "sensor_fail_threshold": 3,
    "anomaly_threshold_factor": 2.0,

    # SMTP
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587,
    "smtp_user": "lkolasa1989@gmail.com",
    "smtp_pass": "ucrz tlve knba bknr",
    "alert_to": "lkolasa1989@gmail.com",

    # Lokalizacja
    "location_lat": 50.0647,
    "location_lon": 19.9450,

    # Serwer
    "server_port": 3000,
}


def load_settings():
    """Wczytuje ustawienia z pliku JSON, uzupełnia brakujące domyślnymi."""
    result = dict(SETTINGS_DEFAULTS)
    if os.path.isfile(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            result.update(saved)
        except (json.JSONDecodeError, OSError):
            pass
    return result


def save_settings(new_settings):
    """Zapisuje ustawienia do pliku JSON i aktualizuje globalny obiekt."""
    global settings
    settings.update(new_settings)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)


# Globalny obiekt ustawień — ładowany przy starcie
settings = load_settings()


# ─── Stałe wewnętrzne (nie konfigurowalne) ────────────────────────

FILTER_WINDOW = 5
PUMPOUT_EXPECTED_INTERVAL_DAYS = (25, 30)
PUMPOUT_ESTIMATE_MIN_DAYS = 3
HOST_NAME = "0.0.0.0"


# ─── Stan aplikacji (dataclassy) ──────────────────────────────────


@dataclass
class SensorFilter:
    """Stan filtrowania odczytów z czujników (mediana + anty-spike)."""
    history_rain: list = field(default_factory=list)
    history_waste: list = field(default_factory=list)


@dataclass
class PumpoutDetector:
    """Stan detekcji wizyt szambowozu."""
    recent_readings: list = field(default_factory=list)
    cooldown_until: datetime = None


@dataclass
class RainPumpDetector:
    """Stan detekcji pompowania deszczówki."""
    recent_readings: list = field(default_factory=list)   # [(timestamp, rain_pct)]
    pumping: bool = False          # czy trwa pompowanie
    pump_start: datetime = None    # początek pompowania
    pct_before: float = None       # % przed pompowaniem
    stable_count: int = 0          # ile odczytów stabilnych z rzędu
    last_pump_end: datetime = None     # koniec ostatniego pompowania (do scalania)
    last_pump_start: datetime = None   # początek ostatniego pompowania
    last_pct_before: float = None      # % przed ostatnim pompowaniem


@dataclass
class SensorMonitor:
    """Stan monitoringu czujników — awarie i minimalne odległości."""
    fail_count: dict = field(default_factory=lambda: {"Rainwater": 0, "Waste": 0})
    fail_alerted: dict = field(default_factory=lambda: {"Rainwater": False, "Waste": False})
    min_ever: dict = field(default_factory=lambda: {"Rainwater": None, "Waste": None})
    min_alerted: dict = field(default_factory=lambda: {"Rainwater": False, "Waste": False})


@dataclass
class AlertState:
    """Stan cooldownów alertów email."""
    tank_last_sent: dict = field(default_factory=dict)
    low_rain_last_sent: datetime = None
    anomaly_last_sent: datetime = None
    last_estimate_save: datetime = None
    last_rainfall_fetch: datetime = None


@dataclass
class LastReading:
    """Ostatni pomiar z wątku w tle — do wyświetlania na stronach."""
    rain_dist: float = None
    rain_pct: int = None
    rain_level: int = None
    rain_over: bool = False
    waste_dist: float = None
    waste_pct: int = None
    waste_level: int = None
    waste_over: bool = False


# Instancje stanu (singletony)
sensor_filter = SensorFilter()
pumpout_detector = PumpoutDetector()
rain_pump_detector = RainPumpDetector()
sensor_monitor = SensorMonitor()
alert_state = AlertState()
last_reading = LastReading()
