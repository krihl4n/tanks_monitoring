"""Odczyt i filtrowanie danych z czujników odległości."""

import logging
import time
import requests

from config import FILTER_WINDOW, settings

logger = logging.getLogger("tanks")


def calc_percent(distance_cm, depth=None):
    if depth is None:
        depth = settings["tank_depth_cm"]
    offset = settings.get("sensor_offset_cm", 0)
    effective_depth = depth + offset  # odległość czujnik -> dno
    level = effective_depth - distance_cm
    level = max(0, min(level, depth))  # cap na głębokość zbiornika (nie offset)
    pct = round(level / depth * 100)
    # Czujnik nie mierzy poniżej sensor_min_distance_cm — powyżej tego poziomu
    # nie znamy dokładnej wartości
    above_sensor_limit = distance_cm <= settings["sensor_min_distance_cm"]
    return pct, round(level), above_sensor_limit


def _read_single(ip, key, timeout=5):
    """Pojedynczy odczyt z czujnika. Zwraca wartość lub None."""
    try:
        data = requests.get(f"http://{ip}", timeout=timeout).json()
        return data[key]
    except Exception:
        return None


def _read_with_median(ip, key, count):
    """Wykonuje `count` odczytów z czujnika i zwraca (medianę, rozrzut).

    Odczyty co ~1s. Pomija None. Jeśli < 50% odczytów poprawnych → (None, None).
    Rozrzut = max - min z surowych odczytów (miara niestabilności czujnika).
    """
    readings = []
    for i in range(count):
        val = _read_single(ip, key)
        if val is not None:
            readings.append(val)
        if i < count - 1:
            time.sleep(settings.get("sensor_read_delay", 2))

    if len(readings) < max(1, count // 2):
        return None, None

    spread = max(readings) - min(readings)
    return round(_median(readings)), spread


def read_sensors(accurate=False):
    """Odczytuje dane z czujników, zwraca (rain_dist, waste_dist, rain_spread, waste_spread).

    accurate=True: sensor_read_count odczytów + mediana (do pomiarów w tle).
    accurate=False: pojedynczy odczyt (do wyświetlania na stronie).
    Spread = rozrzut surowych odczytów (None przy pojedynczym odczycie).
    """
    count = settings.get("sensor_read_count", 5) if accurate else 1

    rain_dist = None
    rain_spread = None
    try:
        if count > 1:
            rain_dist, rain_spread = _read_with_median(
                settings["sensor_rain_ip"], "distanceFromWaterCm", count)
        else:
            rain_dist = _read_single(settings["sensor_rain_ip"], "distanceFromWaterCm")
    except Exception as e:
        logger.warning("Błąd odczytu czujnika deszczówki: %s", e)

    waste_dist = None
    waste_spread = None
    try:
        if count > 1:
            waste_dist, waste_spread = _read_with_median(
                settings["sensor_waste_ip"], "distanceCm", count)
        else:
            waste_dist = _read_single(settings["sensor_waste_ip"], "distanceCm")
    except Exception as e:
        logger.warning("Błąd odczytu czujnika szamba: %s", e)

    return rain_dist, waste_dist, rain_spread, waste_spread


def _median(values):
    s = sorted(values)
    n = len(s)
    if n % 2 == 1:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2


def filter_value(raw, history):
    """Filtruje odczyt: mediana + odrzucanie pojedynczych skoków.

    Jeśli nowy odczyt odbiega od mediany o > spike_threshold_cm,
    traktujemy go jako potencjalny błąd i zwracamy medianę.
    Ale jeśli 2 kolejne odczyty idą w tym samym kierunku (np. wypompowywanie),
    akceptujemy nowy trend.
    """
    if raw is None:
        return history[-1] if history else None

    if len(history) < 2:
        history.append(raw)
        return raw

    med = _median(history[-FILTER_WINDOW:])

    if abs(raw - med) > settings["spike_threshold_cm"]:
        # Sprawdź czy poprzedni odczyt też szedł w tym samym kierunku
        prev = history[-1]
        direction_now = raw - med
        direction_prev = prev - _median(history[-FILTER_WINDOW - 1:-1]) if len(history) >= FILTER_WINDOW else 0

        if abs(direction_prev) > settings["sensor_noise_cm"] and (direction_now > 0) == (direction_prev > 0):
            # Trend się utrzymuje (np. wypompowywanie) — akceptuj
            history.append(raw)
            return raw
        else:
            # Pojedynczy skok — użyj mediany
            history.append(med)
            return med
    else:
        history.append(raw)
        return raw
