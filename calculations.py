"""Obliczenia — dzienne zużycie, estymacja szambowozu, święta, pogoda."""

import logging
from datetime import date, datetime, timedelta

import requests

from config import settings, PUMPOUT_ESTIMATE_MIN_DAYS
from data import load_history, load_pumpouts, load_planned_pumpout

logger = logging.getLogger("tanks")


def calc_liters(pct):
    """Oblicza szacunkową ilość cieczy w litrach na podstawie procentu napełnienia."""
    return round(settings["tank_capacity_l"] * pct / 100)


def calc_daily_waste_gain(rows):
    """Oblicza dzienne zużycie szamba (netto: ostatni - pierwszy pomiar danego dnia).

    Sumowanie mikro-delt (każdy +1 pp) kumuluje szum czujnika (+-1 cm = +-1 pp).
    Podejście netto bierze różnicę między ostatnim a pierwszym pomiarem dnia,
    co eliminuje szum. Dni z wywozem szambowozu (spadek >= PUMPOUT_DROP_PCT)
    są pomijane.

    Zwraca dict: {"2026-04-30": litry, ...}
    """
    # Zbierz pierwszy i ostatni pomiar per dzień
    day_first = {}  # "2026-04-30" -> first pct
    day_last = {}   # "2026-04-30" -> last pct

    for row in rows:
        wp = row.get("waste_pct")
        if not wp:
            continue
        try:
            ts = datetime.fromisoformat(row["timestamp"])
            pct = float(wp)
        except (ValueError, KeyError):
            continue
        key = ts.strftime("%Y-%m-%d")
        if key not in day_first:
            day_first[key] = pct
        day_last[key] = pct

    # Oblicz netto per dzień, pomijaj dni z wywozem
    pumpout_days = set()
    pumpouts = load_pumpouts()
    for p in pumpouts:
        try:
            pumpout_days.add(datetime.fromisoformat(p["timestamp"]).strftime("%Y-%m-%d"))
        except (ValueError, KeyError):
            pass

    daily_gain = {}
    for key in day_first:
        if key in pumpout_days:
            continue
        delta_pct = day_last[key] - day_first[key]
        if delta_pct > 0:
            daily_gain[key] = delta_pct / 100 * settings["tank_capacity_l"]

    return daily_gain


def find_min_sensor_record():
    """Znajduje najniższy odczyt czujnika (= max napełnienie) od ostatniego wywozu.

    Zwraca dict: {"rain": (distance, date_str), "waste": (distance, date_str)} lub None.
    """
    pumpouts = load_pumpouts()
    last_pumpout_ts = None
    if pumpouts:
        try:
            last_pumpout_ts = pumpouts[-1]["timestamp"]
        except (KeyError, IndexError):
            pass

    rows = load_history(days=9999)
    rain_min = None
    waste_min = None

    for row in rows:
        try:
            ts = row["timestamp"]
            # Dla waste: bierz dane tylko od ostatniego wywozu
            rd = row.get("rain_distance_cm")
            wd = row.get("waste_distance_cm")
            if rd and rd != "":
                rd = float(rd)
                if rain_min is None or rd < rain_min[0]:
                    rain_min = (rd, ts)
            if wd and wd != "":
                if last_pumpout_ts and ts < last_pumpout_ts:
                    continue
                wd = float(wd)
                if waste_min is None or wd < waste_min[0]:
                    waste_min = (wd, ts)
        except (ValueError, KeyError):
            continue

    result = {}
    for key, val in [("rain", rain_min), ("waste", waste_min)]:
        if val:
            try:
                dt = datetime.fromisoformat(val[1])
                date_str = dt.strftime("%d.%m.%Y %H:%M")
            except ValueError:
                date_str = val[1]
            result[key] = (round(val[0]), date_str)
        else:
            result[key] = None
    return result


def calc_daily_stats():
    """Oblicza średnie dzienne tempo zmian napełnienia.

    Dla szamba bierze dane od ostatniego wywozu (nie z 7 dni na sztywno),
    żeby wypompowanie nie zaburzało statystyk.

    Zwraca (rain_daily_liters, waste_daily_liters):
      - ujemne = zużycie/spadek
      - dodatnie = napełnianie/wzrost
      - None = brak danych
    """
    rows = load_history(days=7)
    rain_points = []
    waste_points = []

    # Ustal cutoff dla szamba — dane tylko od ostatniego wywozu
    pumpouts = load_pumpouts()
    waste_cutoff = 0
    if pumpouts:
        try:
            waste_cutoff = datetime.fromisoformat(pumpouts[-1]["timestamp"]).timestamp()
        except (ValueError, KeyError):
            pass

    for row in rows:
        try:
            ts = datetime.fromisoformat(row["timestamp"]).timestamp()
        except (ValueError, KeyError):
            continue
        rp = row.get("rain_pct")
        wp = row.get("waste_pct")
        rain_unstable = row.get("rain_unstable") == "1"
        if rp and not rain_unstable:
            rain_points.append((ts, float(rp)))
        if wp and ts >= waste_cutoff:
            waste_points.append((ts, float(wp)))

    def daily_change(points):
        if len(points) < 2:
            return None
        span_days = (points[-1][0] - points[0][0]) / 86400
        if span_days < 1:
            return None
        pct_change = points[-1][1] - points[0][1]
        daily_pct = pct_change / span_days
        return round(daily_pct * settings["tank_capacity_l"] / 100)

    return daily_change(rain_points), daily_change(waste_points)


# ─── Polskie święta i dni wolne ───────────────────────────────────


def _polish_holidays(year):
    """Zwraca zbiór dat świąt ustawowo wolnych od pracy w Polsce dla danego roku.

    Uwzględnia święta stałe i ruchome (Wielkanoc, Boże Ciało).
    """
    # Wielkanoc — algorytm Gaussa
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    easter = datetime(year, month, day).date()

    holidays = {
        date(year, 1, 1),    # Nowy Rok
        date(year, 1, 6),    # Trzech Króli
        date(year, 5, 1),    # Święto Pracy
        date(year, 5, 3),    # Konstytucja 3 Maja
        date(year, 8, 15),   # Wniebowzięcie NMP
        date(year, 11, 1),   # Wszystkich Świętych
        date(year, 11, 11),  # Święto Niepodległości
        date(year, 12, 25),  # Boże Narodzenie
        date(year, 12, 26),  # Drugi dzień Bożego Narodzenia
        easter,                                          # Wielkanoc (niedziela)
        easter + timedelta(days=1),                      # Poniedziałek Wielkanocny
        easter + timedelta(days=60),                     # Boże Ciało
    }
    return holidays


def _is_non_working_day(dt_date):
    """Sprawdza czy dana data to dzień wolny (niedziela lub święto)."""
    if dt_date.weekday() == 6:  # niedziela
        return True
    holidays = _polish_holidays(dt_date.year)
    # Na przełomie roku sprawdź też święta z sąsiedniego roku
    if dt_date.month == 12:
        holidays |= _polish_holidays(dt_date.year + 1)
    elif dt_date.month == 1:
        holidays |= _polish_holidays(dt_date.year - 1)
    return dt_date in holidays


def _next_working_day(dt_date):
    """Zwraca najbliższy dzień roboczy >= dt_date."""
    while _is_non_working_day(dt_date):
        dt_date += timedelta(days=1)
    return dt_date


def _prev_working_day(dt_date):
    """Zwraca najbliższy dzień roboczy <= dt_date."""
    while _is_non_working_day(dt_date):
        dt_date -= timedelta(days=1)
    return dt_date


def _count_non_working_streak(start_date):
    """Liczy ile dni wolnych z rzędu zaczyna się od start_date (włącznie)."""
    count = 0
    d = start_date
    while _is_non_working_day(d):
        count += 1
        d += timedelta(days=1)
    return count


def extrapolate_current_pct():
    """Estymuje aktualny % napełnienia szamba na podstawie trendu (regresja liniowa).

    Używane gdy czujnik jest przy granicy zakresu i nie mierzy dokładnie.
    Bierze dane od ostatniego wywozu (min 3 dni). Zwraca int lub None.
    """
    pumpouts = load_pumpouts()
    last_pumpout_ts = None
    if pumpouts:
        try:
            last_pumpout_ts = datetime.fromisoformat(pumpouts[-1]["timestamp"])
            since_pumpout = datetime.now() - last_pumpout_ts
            days = max(1, int(since_pumpout.total_seconds() / 86400) + 1)
        except (ValueError, KeyError):
            days = 7
    else:
        days = 7

    rows = load_history(days=days)
    pumpout_cutoff = last_pumpout_ts.timestamp() if last_pumpout_ts else 0

    # Zbierz punkty — tylko te poniżej granicy czujnika (wiarygodne)
    sensor_max_pct = round((settings["tank_depth_cm"] + settings.get("sensor_offset_cm", 0) - settings["sensor_min_distance_cm"]) / settings["tank_depth_cm"] * 100)
    points = []
    for row in rows:
        wp = row.get("waste_pct")
        if wp:
            try:
                t = datetime.fromisoformat(row["timestamp"]).timestamp()
                if t < pumpout_cutoff:
                    continue
                pct = float(wp)
                if pct < sensor_max_pct:
                    points.append((t, pct))
            except (ValueError, KeyError):
                continue

    if len(points) < 2:
        return None

    span_days = (points[-1][0] - points[0][0]) / 86400
    if span_days < PUMPOUT_ESTIMATE_MIN_DAYS:
        return None

    # Regresja liniowa
    n = len(points)
    sum_t = sum(t for t, _ in points)
    sum_p = sum(p for _, p in points)
    sum_tp = sum(t * p for t, p in points)
    sum_t2 = sum(t * t for t, _ in points)
    denom = n * sum_t2 - sum_t * sum_t
    if denom == 0:
        return None

    a = (n * sum_tp - sum_t * sum_p) / denom
    b = (sum_p - a * sum_t) / n

    if a <= 0:
        return None  # trend spadkowy — ekstrapolacja nie ma sensu

    now_t = datetime.now().timestamp()
    estimated = round(a * now_t + b)
    return max(estimated, sensor_max_pct)  # nie mniej niż to co czujnik mierzy


def estimate_pumpout_date():
    """Estymuje datę wezwania szambowozu na podstawie trendu liniowego.

    Bierze dane od ostatniego wywozu (jeśli był) lub z ostatnich 7 dni.
    Po wywozi estymacja bazuje wyłącznie na nowych danych (rosnący trend).

    Zwraca dict {"full_date": str, "service_date": str, "order_date": str,
    "warning": str|None} lub "teraz" lub None.
    """
    # Ustal od kiedy brać dane — od ostatniego wywozu lub 7 dni
    pumpouts = load_pumpouts()
    last_pumpout_ts = None
    if pumpouts:
        try:
            last_pumpout_ts = datetime.fromisoformat(pumpouts[-1]["timestamp"])
            since_pumpout = datetime.now() - last_pumpout_ts
            days = max(1, int(since_pumpout.total_seconds() / 86400) + 1)
        except (ValueError, KeyError):
            days = 7
    else:
        days = 7

    rows = load_history(days=days)

    # Zbierz punkty (timestamp_sekundy, waste_pct)
    # Odfiltruj dane sprzed ostatniego wywozu
    pumpout_cutoff = last_pumpout_ts.timestamp() if last_pumpout_ts else 0
    points = []
    for row in rows:
        wp = row.get("waste_pct")
        if wp:
            try:
                t = datetime.fromisoformat(row["timestamp"]).timestamp()
                if t < pumpout_cutoff:
                    continue
                points.append((t, float(wp)))
            except (ValueError, KeyError):
                continue

    if len(points) < 2:
        return None

    # Odrzuć outliers — punkty odchylone o > 3 * IQR od mediany
    pcts = sorted(p for _, p in points)
    n = len(pcts)
    q1 = pcts[n // 4]
    q3 = pcts[3 * n // 4]
    iqr = q3 - q1
    lower = q1 - 3 * max(iqr, 1)
    upper = q3 + 3 * max(iqr, 1)
    points = [(t, p) for t, p in points if lower <= p <= upper]

    if len(points) < 2:
        return None

    # Sprawdź czy mamy dane z min. PUMPOUT_ESTIMATE_MIN_DAYS dni
    span_days = (points[-1][0] - points[0][0]) / 86400
    if span_days < PUMPOUT_ESTIMATE_MIN_DAYS:
        return None

    # Regresja liniowa: pct = a * t + b
    n = len(points)
    sum_t = sum(t for t, _ in points)
    sum_p = sum(p for _, p in points)
    sum_tp = sum(t * p for t, p in points)
    sum_t2 = sum(t * t for t, _ in points)
    denom = n * sum_t2 - sum_t * sum_t
    if denom == 0:
        return None

    a = (n * sum_tp - sum_t * sum_p) / denom  # nachylenie (pct/s)
    b = (sum_p - a * sum_t) / n

    # Jeśli trend nie rośnie lub jest zbyt wolny — szambo się nie napełnia
    if a <= 1e-8:
        return None

    # Kiedy osiągnie PUMPOUT_ESTIMATE_PCT?
    current_pct = points[-1][1]
    pumpout_estimate_pct = settings["pumpout_estimate_pct"]
    if current_pct >= pumpout_estimate_pct:
        return "teraz"

    remaining_pct = pumpout_estimate_pct - current_pct
    remaining_seconds = remaining_pct / a
    full_date = datetime.fromtimestamp(points[-1][0] + remaining_seconds)
    full_d = full_date.date()

    # Wywóz nie może wypaść w dzień wolny — przesuń na poprzedni dzień roboczy
    service_date = _prev_working_day(full_d)

    # Sprawdź czy przed zapełnieniem wypada długa przerwa (>= 3 dni wolne z rzędu)
    # Jeśli tak, przesuń wywóz na ostatni dzień roboczy PRZED tą przerwą
    warning = None
    check_from = datetime.now().date() + timedelta(days=1)
    d = check_from
    while d <= full_d:
        streak = _count_non_working_streak(d)
        if streak >= 3:
            # Długa przerwa znaleziona
            break_start = d
            break_end = d + timedelta(days=streak - 1)

            # Estymuj % napełnienia na początku przerwy
            seconds_to_break = (datetime.combine(break_start, datetime.min.time()) - datetime.now()).total_seconds()
            pct_at_break = current_pct + a * seconds_to_break
            pct_at_break_end = current_pct + a * (seconds_to_break + streak * 86400)

            # Jeśli po przerwie będzie >= 80% lub zapełni się w trakcie przerwy
            if pct_at_break_end >= pumpout_estimate_pct * 0.9 or (break_start <= full_d <= break_end):
                earlier_service = _prev_working_day(break_start - timedelta(days=1))
                if earlier_service < service_date:
                    service_date = earlier_service
                    warning = f"Przerwa {break_start.strftime('%d.%m')}-{break_end.strftime('%d.%m')} ({streak} dni wolne) — zalecany wcześniejszy wywóz"
                break
        d += timedelta(days=1)

    # Zamówienie: 2 dni robocze przed wywozem
    order_date = service_date
    working_days_back = 0
    while working_days_back < 2:
        order_date -= timedelta(days=1)
        if not _is_non_working_day(order_date):
            working_days_back += 1
    # Upewnij się, że zamówienie też wypada w dzień roboczy
    order_date = _prev_working_day(order_date)

    DAY_NAMES = {
        "Monday": "poniedziałek", "Tuesday": "wtorek", "Wednesday": "środa",
        "Thursday": "czwartek", "Friday": "piątek", "Saturday": "sobota", "Sunday": "niedziela",
    }

    now = datetime.now().date()

    def format_date(d):
        s = d.strftime("%d.%m.%Y (%A)")
        for en, pl in DAY_NAMES.items():
            s = s.replace(en, pl)
        return s

    if order_date <= now:
        order_display = "jak najszybciej"
    else:
        order_display = format_date(order_date)

    result = {
        "full_date": format_date(full_d),
        "service_date": format_date(service_date),
        "order_date": order_display,
        "warning": warning,
    }

    # Porównaj z planowanym wywozem
    planned = load_planned_pumpout()
    if planned:
        try:
            planned_d = datetime.strptime(planned["date"], "%Y-%m-%d").date()
            if planned_d > full_d:
                days_late = (planned_d - full_d).days
                planned_warning = (
                    f"Planowany wywóz ({planned_d.strftime('%d.%m.%Y')}) jest "
                    f"{days_late} dni PO estymowanym zapełnieniu — rozważ przyspieszenie"
                )
                if result["warning"]:
                    result["warning"] += "; " + planned_warning
                else:
                    result["warning"] = planned_warning
            elif planned_d <= full_d:
                days_before = (full_d - planned_d).days
                result["planned_ok"] = (
                    f"Planowany wywóz ({planned_d.strftime('%d.%m.%Y')}) — "
                    f"{days_before} dni przed estymowanym zapełnieniem"
                )
        except (ValueError, KeyError):
            pass

    return result


# ─── Pogoda / opady ──────────────────────────────────────────────


def fetch_rainfall():
    """Pobiera aktualną sumę opadów z ostatniej godziny z Open-Meteo API."""
    try:
        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={settings['location_lat']}&longitude={settings['location_lon']}"
            f"&current=precipitation"
        )
        data = requests.get(url, timeout=10).json()
        precip = data.get("current", {}).get("precipitation", 0)
        return precip  # mm
    except Exception as e:
        logger.warning("Błąd pobierania opadów: %s", e)
        return None


_forecast_cache = {"data": [], "fetched_at": None}


def fetch_weather_forecast():
    """Pobiera prognozę pogody na 3 dni z Open-Meteo API.

    Cache 30 minut — nie odpytuje API przy każdym ładowaniu strony.
    Zwraca listę dict: [{"date": "2026-04-30", "precip_sum": mm, "precip_prob": %,
    "temp_max": C, "temp_min": C}] lub pustą listę.
    """
    now = datetime.now()
    if _forecast_cache["fetched_at"] and (now - _forecast_cache["fetched_at"]).total_seconds() < 1800:
        return _forecast_cache["data"]

    try:
        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={settings['location_lat']}&longitude={settings['location_lon']}"
            f"&daily=precipitation_sum,precipitation_probability_max,temperature_2m_max,temperature_2m_min"
            f"&timezone=Europe%2FWarsaw&forecast_days=3"
        )
        data = requests.get(url, timeout=10).json()
        daily = data.get("daily", {})
        dates = daily.get("time", [])
        precip = daily.get("precipitation_sum", [])
        prob = daily.get("precipitation_probability_max", [])
        tmax = daily.get("temperature_2m_max", [])
        tmin = daily.get("temperature_2m_min", [])
        result = []
        for i in range(len(dates)):
            result.append({
                "date": dates[i],
                "precip_sum": precip[i] if i < len(precip) else 0,
                "precip_prob": prob[i] if i < len(prob) else 0,
                "temp_max": tmax[i] if i < len(tmax) else None,
                "temp_min": tmin[i] if i < len(tmin) else None,
            })
        _forecast_cache["data"] = result
        _forecast_cache["fetched_at"] = now
        return result
    except Exception as e:
        logger.warning("Błąd pobierania prognozy: %s", e)
        return _forecast_cache["data"]  # zwróć stary cache jeśli jest
