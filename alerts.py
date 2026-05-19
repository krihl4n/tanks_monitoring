"""Alerty email, detekcja szambowozu, monitoring czujników."""

import csv
import logging
import os
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText

from config import (
    settings,
    PUMPOUT_EXPECTED_INTERVAL_DAYS,
    ESTIMATE_FILE,
    pumpout_detector, rain_pump_detector, sensor_monitor, alert_state,
)
from data import (
    load_history, load_pumpouts, load_rainfall,
    save_pumpout, save_rain_usage, save_alert_log, save_rain_instability,
    load_rain_instability, _rain_intensity,
)
from calculations import calc_liters, calc_daily_waste_gain

logger = logging.getLogger("tanks")


# ─── Wysyłanie email ─────────────────────────────────────────────


def send_email(subject, body):
    """Wysyła email przez SMTP (Gmail). Wspólna funkcja dla wszystkich alertów."""
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = settings["smtp_user"]
    msg["To"] = settings["alert_to"]
    try:
        with smtplib.SMTP(settings["smtp_host"], settings["smtp_port"]) as server:
            server.starttls()
            server.login(settings["smtp_user"], settings["smtp_pass"])
            server.sendmail(settings["smtp_user"], settings["alert_to"], msg.as_string())
        logger.info("Email wysłany: %s", subject)
    except Exception as e:
        logger.error("Błąd wysyłania emaila '%s': %s", subject, e)


# ─── Detekcja szambowozu ─────────────────────────────────────────


def detect_pumpout(waste_pct, waste_dist):
    """Wykrywa opróżnienie szamba (wizytę szambowozu)."""
    now = datetime.now()
    state = pumpout_detector

    # Cooldown po ostatnim wykryciu
    if state.cooldown_until and now < state.cooldown_until:
        state.recent_readings = [(now, waste_pct, waste_dist)]
        return

    state.recent_readings.append((now, waste_pct, waste_dist))

    # Wyrzuć odczyty starsze niż okno
    cutoff = now - timedelta(minutes=settings["pumpout_window_min"])
    state.recent_readings = [(t, v, d) for t, v, d in state.recent_readings if t >= cutoff]

    if len(state.recent_readings) < 2:
        return

    # Oblicz stabilny max/min — wymagaj co najmniej 3 odczytów powyżej/poniżej progu,
    # żeby pojedynczy spike nie wystarczył do wykrycia szambowozu
    PUMPOUT_MIN_CONFIRM_READINGS = 3
    readings_pct = [v for _, v, _ in state.recent_readings]
    max_pct = max(readings_pct)
    min_pct = min(readings_pct)
    drop = max_pct - min_pct

    if drop >= settings["pumpout_drop_pct"]:
        # Sprawdź czy max napełnienie było stabilne (nie jednorazowy spike)
        high_threshold = max_pct - settings["sensor_noise_cm"] / settings["tank_depth_cm"] * 100  # tolerancja szumu
        high_count = sum(1 for v in readings_pct if v >= high_threshold)
        if high_count < PUMPOUT_MIN_CONFIRM_READINGS:
            logger.debug("Pominięto potencjalny pumpout — max %s%% potwierdzony tylko %d/%d odczytami",
                         max_pct, high_count, PUMPOUT_MIN_CONFIRM_READINGS)
            return

        # Sprawdź czy min napełnienie było stabilne (nie jednorazowy spike w dół)
        low_threshold = min_pct + settings["sensor_noise_cm"] / settings["tank_depth_cm"] * 100
        low_count = sum(1 for v in readings_pct if v <= low_threshold)
        if low_count < PUMPOUT_MIN_CONFIRM_READINGS:
            logger.debug("Pominięto potencjalny pumpout — min %s%% potwierdzony tylko %d/%d odczytami",
                         min_pct, low_count, PUMPOUT_MIN_CONFIRM_READINGS)
            return

        # Znajdź odległości przy max i min napełnieniu
        dist_at_max = next(d for _, v, d in state.recent_readings if v == max_pct)
        dist_at_min = next(d for _, v, d in state.recent_readings if v == min_pct)

        logger.info("Wykryto szambowóz! Spadek: %s%% -> %s%%, odległość: %s -> %s cm",
                    max_pct, waste_pct, dist_at_max, dist_at_min)
        save_pumpout(now, max_pct, waste_pct)
        send_pumpout_diagnostic(now, max_pct, waste_pct, dist_at_max, dist_at_min)
        check_pumpout_interval_anomaly()
        check_estimate_accuracy()
        state.recent_readings = [(now, waste_pct, waste_dist)]
        state.cooldown_until = now + timedelta(minutes=30)


def send_pumpout_diagnostic(when, pct_before, pct_after, dist_before, dist_after):
    """Wysyła maila z diagnostyką przy opróżnieniu szamba."""
    subject = f"Szambowóz - diagnostyka odczytów ({when.strftime('%d.%m.%Y %H:%M')})"
    body = (
        f"Wykryto opróżnienie szamba.\n\n"
        f"Napełnienie przed: {pct_before}%\n"
        f"Napełnienie po: {pct_after}%\n\n"
        f"Odległość czujnika przed: {dist_before} cm\n"
        f"Odległość czujnika po: {dist_after} cm\n\n"
        f"Skonfigurowana głębokość zbiornika: {settings['tank_depth_cm']} cm\n\n"
        f"Jeśli po opróżnieniu odległość czujnika ≈ głębokość zbiornika,\n"
        f"to kalibracja jest poprawna. Jeśli nie — głębokość wymaga korekty.\n"
    )
    send_email(subject, body)
    save_alert_log("pumpout_diagnostic", "Waste", "info", subject)


# ─── Alerty napełnienia zbiorników ────────────────────────────────

TANK_NAME_PL = {"Waste": "Szambo", "Rainwater": "Deszczówka"}


def send_alert_email(tank_name, pct, level, alert_type="high"):
    """Wysyła alert o napełnieniu zbiornika (warning lub critical).

    alert_type: "high" = przepełnienie, "low" = niski poziom.
    """
    name_pl = TANK_NAME_PL.get(tank_name, tank_name)
    liters = calc_liters(pct)
    level_pl = "KRYTYCZNY" if level == "critical" else "Ostrzeżenie"
    if alert_type == "low":
        subject = f"{level_pl}: {name_pl} — niski poziom {pct}% ({liters} l)"
        body = (
            f"Zbiornik \"{name_pl}\" ma niski poziom: {pct}%.\n\n"
            f"Poziom alertu: {level_pl}\n"
            f"Szacowana ilość: {liters} litrów\n"
            f"Data: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
        )
    else:
        subject = f"{level_pl}: {name_pl} — napełnienie {pct}% ({liters} l)"
        body = (
            f"Zbiornik \"{name_pl}\" osiągnął {pct}% napełnienia.\n\n"
            f"Poziom alertu: {level_pl}\n"
            f"Szacowana ilość: {liters} litrów\n"
            f"Data: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
        )
    send_email(subject, body)
    save_alert_log(alert_type, tank_name, level, subject)


def _send_if_cooldown(cooldown_key, tank_name, pct, level, alert_type):
    """Wysyła alert jeśli jeszcze nie wysłano dla tego poziomu.

    Wysyła ponownie tylko gdy:
    - Alert tego typu jeszcze nie był wysłany
    - Poziom eskalował (warning -> critical)
    - Poziom wcześniej spadł poniżej progu (reset) i znowu przekroczył
    """
    now = datetime.now()
    last_level = alert_state.tank_last_sent.get(cooldown_key)
    if last_level == level:
        # Już wysłano alert tego samego poziomu — nie powtarzaj
        return False
    send_alert_email(tank_name, pct, level, alert_type)
    alert_state.tank_last_sent[cooldown_key] = level
    return True


def _reset_alert(cooldown_key):
    """Resetuje alert — poziom spadł poniżej progu."""
    alert_state.tank_last_sent.pop(cooldown_key, None)


def check_tank_alert(tank_name, pct):
    """Sprawdza czy wysłać alerty dla danego zbiornika.

    Trzy niezależne grupy alertów (każda z osobnym enable i cooldown):
    - Przepełnienie szamba: warning/critical gdy pct >= próg
    - Przepełnienie deszczówki: warning/critical gdy pct >= próg
    - Niski poziom deszczówki: warning/critical gdy pct <= próg
    """
    if pct is None:
        return

    if tank_name == "Waste":
        _check_high_alert(tank_name, pct,
                          "alert_waste_high_enabled",
                          "alert_waste_high_warning_pct",
                          "alert_waste_high_critical_pct")
    else:  # Rainwater
        _check_high_alert(tank_name, pct,
                          "alert_rain_high_enabled",
                          "alert_rain_high_warning_pct",
                          "alert_rain_high_critical_pct")
        _check_low_alert(tank_name, pct)


def _check_high_alert(tank_name, pct, enabled_key, warning_key, critical_key):
    """Sprawdza alert przepełnienia (pct >= próg)."""
    if not settings.get(enabled_key, True):
        return

    critical_pct = settings[critical_key]
    warning_pct = settings[warning_key]
    cooldown_key = f"{tank_name}_high"

    if pct >= critical_pct:
        _send_if_cooldown(f"{cooldown_key}_critical", tank_name, pct, "critical", "high")
    elif pct >= warning_pct:
        _send_if_cooldown(f"{cooldown_key}_warning", tank_name, pct, "warning", "high")
        # Spadł z critical -> warning: reset critical
        _reset_alert(f"{cooldown_key}_critical")
    else:
        # Poniżej progu — reset obu
        _reset_alert(f"{cooldown_key}_warning")
        _reset_alert(f"{cooldown_key}_critical")


def _check_low_alert(tank_name, pct):
    """Sprawdza alert niskiego poziomu deszczówki (pct <= próg)."""
    if not settings.get("alert_rain_low_enabled", True):
        return

    critical_pct = settings["alert_rain_low_critical_pct"]
    warning_pct = settings["alert_rain_low_warning_pct"]
    cooldown_key = f"{tank_name}_low"

    if pct <= critical_pct:
        _send_if_cooldown(f"{cooldown_key}_critical", tank_name, pct, "critical", "low")
    elif pct <= warning_pct:
        _send_if_cooldown(f"{cooldown_key}_warning", tank_name, pct, "warning", "low")
        _reset_alert(f"{cooldown_key}_critical")
    else:
        # Powyżej progu — reset obu
        _reset_alert(f"{cooldown_key}_warning")
        _reset_alert(f"{cooldown_key}_critical")


# ─── Monitoring awarii czujników ──────────────────────────────────


def check_sensor_failure(tank_name, raw_value):
    """Sprawdza czy czujnik nie odpowiada. Wysyła alert po N kolejnych błędach."""
    if not settings.get("alert_sensor_failure_enabled", True):
        return
    if raw_value is not None:
        sensor_monitor.fail_count[tank_name] = 0
        if sensor_monitor.fail_alerted[tank_name]:
            # Czujnik wrócił — powiadom
            sensor_monitor.fail_alerted[tank_name] = False
            send_sensor_recovery_email(tank_name)
        return

    sensor_monitor.fail_count[tank_name] += 1

    if sensor_monitor.fail_count[tank_name] >= settings["sensor_fail_threshold"] and not sensor_monitor.fail_alerted[tank_name]:
        sensor_monitor.fail_alerted[tank_name] = True
        send_sensor_failure_email(tank_name)


def send_sensor_failure_email(tank_name):
    name_pl = TANK_NAME_PL.get(tank_name, tank_name)
    subject = f"Awaria czujnika: {name_pl}"
    body = (
        f"Czujnik zbiornika \"{name_pl}\" nie odpowiada.\n"
        f"Brak odczytu przez {settings['sensor_fail_threshold']} kolejnych pomiarów "
        f"({settings['sensor_fail_threshold'] * settings['measure_interval'] // 60} min).\n"
        f"Data: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
    )
    send_email(subject, body)
    save_alert_log("sensor_failure", tank_name, "critical", subject)


def send_sensor_recovery_email(tank_name):
    name_pl = TANK_NAME_PL.get(tank_name, tank_name)
    subject = f"Czujnik przywrócony: {name_pl}"
    body = (
        f"Czujnik zbiornika \"{name_pl}\" ponownie działa.\n"
        f"Data: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
    )
    send_email(subject, body)
    save_alert_log("sensor_recovery", tank_name, "info", subject)


# ─── Monitoring minimalnej odległości czujnika ────────────────────


def check_sensor_min_distance(tank_name, raw_dist):
    """Monitoruje zachowanie czujnika przy małych odległościach.

    Wysyła maila gdy odczyt osiągnie lub spadnie poniżej SENSOR_MIN_DISTANCE_CM,
    w tym gdy pokaże 0. Rejestruje najmniejszą zaobserwowaną odległość.
    """
    if raw_dist is None:
        return
    if not settings.get("alert_sensor_min_distance_enabled", True):
        return

    prev_min = sensor_monitor.min_ever[tank_name]
    if prev_min is None or raw_dist < prev_min:
        sensor_monitor.min_ever[tank_name] = raw_dist
        logger.info("Nowe minimum odległości %s: %s cm (poprzednie: %s cm)",
                    tank_name, raw_dist, prev_min)

    if raw_dist <= settings["sensor_min_distance_cm"] and not sensor_monitor.min_alerted[tank_name]:
        sensor_monitor.min_alerted[tank_name] = True
        send_sensor_min_distance_email(tank_name, raw_dist)
    elif raw_dist > settings["sensor_min_distance_cm"] + 10:
        # Reset gdy odległość wróci wyraźnie powyżej progu
        sensor_monitor.min_alerted[tank_name] = False


def send_sensor_min_distance_email(tank_name, distance):
    name_pl = TANK_NAME_PL.get(tank_name, tank_name)
    subject = f"Czujnik {name_pl} — odczyt przy granicy zakresu: {distance} cm"
    body = (
        f"Czujnik zbiornika \"{name_pl}\" odczytał odległość {distance} cm.\n"
        f"Skonfigurowana minimalna odległość czujnika: {settings['sensor_min_distance_cm']} cm.\n\n"
    )
    if distance == 0:
        body += "Czujnik pokazał 0 cm — możliwe że zwraca 0 gdy obiekt jest zbyt blisko.\n"
    elif distance < settings["sensor_min_distance_cm"]:
        body += f"Odczyt poniżej zadeklarowanego minimum ({settings['sensor_min_distance_cm']} cm) — czujnik mierzy dokładniej niż zakładano.\n"
    else:
        body += f"Odczyt równy zadeklarowanemu minimum — zbiornik prawdopodobnie prawie pełny.\n"

    body += (
        f"\nHistorycznie najniższy odczyt tego czujnika: {sensor_monitor.min_ever[tank_name]} cm\n"
        f"Data: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
    )
    send_email(subject, body)
    save_alert_log("sensor_min_distance", tank_name, "warning", subject)


# ─── Anomalia zużycia szamba ──────────────────────────────────────


def check_waste_anomaly():
    """Sprawdza czy dzienne zużycie szamba jest anomalnie wysokie.

    Porównuje dzisiejsze zużycie z 30-dniową średnią dzienną. Jeśli dzisiejsze
    zużycie przekracza ANOMALY_THRESHOLD_FACTOR * średnia, wysyła alert.
    Max 1 alert na 24h.
    """
    if not settings.get("alert_waste_anomaly_enabled", True):
        return
    now = datetime.now()
    if alert_state.anomaly_last_sent and (now - alert_state.anomaly_last_sent) < timedelta(hours=24):
        return

    rows = load_history(days=30)
    if not rows:
        return

    daily_gain = calc_daily_waste_gain(rows)

    today = now.strftime("%Y-%m-%d")
    today_liters = daily_gain.get(today, 0)

    # Średnia z poprzednich dni (bez dzisiaj)
    past_days = {k: v for k, v in daily_gain.items() if k != today}
    if len(past_days) < 7:
        return  # za mało danych

    avg = sum(past_days.values()) / len(past_days)
    if avg <= 0:
        return

    if today_liters > avg * settings["anomaly_threshold_factor"]:
        alert_state.anomaly_last_sent = now
        send_anomaly_email(today_liters, avg)


def send_anomaly_email(today_liters, avg_liters):
    subject = f"Anomalia: szambo zapełnia się {today_liters / max(avg_liters, 1):.1f}x szybciej niż zwykle"
    body = (
        f"Dzisiejsze zużycie szamba: ~{today_liters:.0f} l\n"
        f"Średnie dzienne zużycie (30 dni): ~{avg_liters:.0f} l\n"
        f"Współczynnik: {today_liters / max(avg_liters, 1):.1f}x\n\n"
        f"Możliwe przyczyny:\n"
        f"- Nieszczelność instalacji\n"
        f"- Większe niż zwykle zużycie wody\n"
        f"- Fałszywy odczyt czujnika\n\n"
        f"Data: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
    )
    send_email(subject, body)
    save_alert_log("anomaly", "Waste", "warning", subject)


# ─── Monitoring wywozów i estymacji ──────────────────────────────


def check_pumpout_interval_anomaly():
    """Sprawdza odstępy między wywozami szamba.

    Wysyła alert gdy:
    - Odstęp jest krótszy niż PUMPOUT_EXPECTED_INTERVAL_DAYS[0] (= szambo zapełnia się
      szybciej niż zwykle — możliwy wyciek lub większe zużycie wody)
    - Odstęp jest dłuższy niż PUMPOUT_EXPECTED_INTERVAL_DAYS[1] + 5 i napełnienie > 70%
      (= dawno nie było wywozu, może zapomniano zamówić)
    """
    if not settings.get("alert_pumpout_interval_enabled", True):
        return
    pumpouts = load_pumpouts()
    if len(pumpouts) < 2:
        return

    # Ostatnie dwa wywozy
    try:
        last_ts = datetime.fromisoformat(pumpouts[-1]["timestamp"])
        prev_ts = datetime.fromisoformat(pumpouts[-2]["timestamp"])
    except (ValueError, KeyError):
        return

    interval = (last_ts - prev_ts).days
    min_expected, max_expected = PUMPOUT_EXPECTED_INTERVAL_DAYS

    if interval < min_expected:
        subject = f"Szambo: krótki odstęp między wywozami ({interval} dni)"
        body = (
            f"Ostatni wywóz: {last_ts.strftime('%d.%m.%Y %H:%M')}\n"
            f"Poprzedni wywóz: {prev_ts.strftime('%d.%m.%Y %H:%M')}\n"
            f"Odstęp: {interval} dni (oczekiwany: {min_expected}-{max_expected} dni)\n\n"
            f"Szambo zapełnia się szybciej niż zwykle.\n"
            f"Możliwe przyczyny:\n"
            f"- Większe zużycie wody\n"
            f"- Nieszczelność instalacji / woda gruntowa\n"
            f"- Deszcz wpadający do szamba\n"
        )
        send_email(subject, body)
        save_alert_log("pumpout_interval", "Waste", "warning", subject)

    logger.info("Odstęp między wywozami: %d dni (oczekiwany: %d-%d)", interval, min_expected, max_expected)


def check_overdue_pumpout():
    """Sprawdza czy dawno nie było wywozu szamba i napełnienie rośnie.

    Wysyła alert gdy od ostatniego wywozu minęło > max_expected + 5 dni
    i aktualne napełnienie > 70%.
    """
    if not settings.get("alert_overdue_pumpout_enabled", True):
        return
    pumpouts = load_pumpouts()
    if not pumpouts:
        return

    try:
        last_ts = datetime.fromisoformat(pumpouts[-1]["timestamp"])
    except (ValueError, KeyError):
        return

    days_since = (datetime.now() - last_ts).days
    _, max_expected = PUMPOUT_EXPECTED_INTERVAL_DAYS

    if days_since <= max_expected + 5:
        return

    # Sprawdź aktualne napełnienie
    rows = load_history(days=1)
    if not rows:
        return
    latest_pct = None
    for row in reversed(rows):
        wp = row.get("waste_pct")
        if wp:
            try:
                latest_pct = float(wp)
                break
            except ValueError:
                continue
    if latest_pct is None or latest_pct < 70:
        return

    subject = f"Szambo: {days_since} dni od ostatniego wywozu, napełnienie {latest_pct:.0f}%"
    body = (
        f"Ostatni wywóz: {last_ts.strftime('%d.%m.%Y')}\n"
        f"Dni od wywozu: {days_since} (oczekiwany cykl: {PUMPOUT_EXPECTED_INTERVAL_DAYS[0]}-{max_expected} dni)\n"
        f"Aktualne napełnienie: {latest_pct:.0f}%\n\n"
        f"Czy szambowóz został zamówiony?\n"
    )
    send_email(subject, body)
    save_alert_log("overdue_pumpout", "Waste", "warning", subject)


def check_estimate_accuracy():
    """Porównuje estymację z rzeczywistą datą wizyty szambowozu.

    Po wykryciu wywozu, sprawdza ostatnią estymację sprzed wywozu
    i loguje/alertuje o dokładności predykcji.
    """
    if not settings.get("alert_estimate_accuracy_enabled", True):
        return
    pumpouts = load_pumpouts()
    if not pumpouts:
        return

    if not os.path.isfile(ESTIMATE_FILE):
        return

    try:
        last_pumpout = datetime.fromisoformat(pumpouts[-1]["timestamp"])
    except (ValueError, KeyError):
        return

    # Znajdź ostatnią estymację sprzed wywozu (1-7 dni przed)
    best_estimate = None
    with open(ESTIMATE_FILE, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                est_ts = datetime.fromisoformat(row["timestamp"])
                est_date_str = row["estimated_full_date"]
                # Estymacja musi być z okresu 1-7 dni przed wywozem
                days_before = (last_pumpout - est_ts).days
                if 1 <= days_before <= 7:
                    best_estimate = (est_ts, est_date_str)
            except (ValueError, KeyError):
                continue

    if not best_estimate:
        return

    est_ts, est_date_str = best_estimate
    # Parsuj datę estymacji (format "DD.MM.YYYY (dzień)")
    try:
        est_date = datetime.strptime(est_date_str[:10], "%d.%m.%Y")
        actual_date = last_pumpout
        diff_days = abs((actual_date - est_date).days)
        logger.info("Trafność estymacji: przewidywano %s, faktyczny wywóz %s (różnica: %d dni)",
                    est_date_str, actual_date.strftime("%d.%m.%Y"), diff_days)

        if diff_days > 5:
            subject = f"Estymacja szambowozu: niedokładna (różnica {diff_days} dni)"
            body = (
                f"Estymacja z {est_ts.strftime('%d.%m.%Y')}: zapełnienie {est_date_str}\n"
                f"Faktyczny wywóz: {actual_date.strftime('%d.%m.%Y %H:%M')}\n"
                f"Różnica: {diff_days} dni\n\n"
                f"Estymacja może wymagać kalibracji — wzorzec zużycia mógł się zmienić.\n"
            )
            send_email(subject, body)
            save_alert_log("estimate_accuracy", "Waste", "info", subject)
    except ValueError:
        pass


# ─── Przypomnienie o wywozie szamba ──────────────────────────────


def check_pumpout_reminder():
    """Wysyła alert gdy estymowana data wywozu szamba jest za <= 7 dni.

    Alert wysyłany raz na 24h (cooldown w alert_state.tank_last_sent).
    """
    if not settings.get("alert_pumpout_reminder_enabled", True):
        return
    from calculations import estimate_pumpout_date

    est = estimate_pumpout_date()
    if est is None:
        return
    if est == "teraz":
        # check_overdue_pumpout już to obsługuje
        return

    try:
        est_date = datetime.strptime(est["full_date"][:10], "%d.%m.%Y").date()
    except (ValueError, KeyError):
        return

    days_left = (est_date - datetime.now().date()).days
    if days_left > 7 or days_left < 0:
        return

    cooldown_key = "pumpout_reminder"
    now = datetime.now()
    last = alert_state.tank_last_sent.get(cooldown_key)
    # Cooldown 24h
    if last and (now - last) < timedelta(hours=24):
        return

    order_info = est.get("order_date", "")
    service_info = est.get("service_date", "")
    subject = f"Szambo: estymowane zapełnienie za {days_left} dni ({est['full_date']})"
    body = (
        f"Estymowana data zapełnienia szamba: {est['full_date']}\n"
        f"Zalecana data wywozu: {service_info}\n"
        f"Zalecana data zamówienia: {order_info}\n\n"
        f"Pozostało {days_left} dni do estymowanego zapełnienia.\n"
        f"Data: {now.strftime('%d.%m.%Y %H:%M')}\n"
    )
    warning = est.get("warning")
    if warning:
        body += f"\nUwaga: {warning}\n"

    send_email(subject, body)
    save_alert_log("pumpout_reminder", "Waste", "warning", subject)
    alert_state.tank_last_sent[cooldown_key] = now


# ─── Detekcja pompowania deszczówki ──────────────────────────────


def detect_rain_pump(rain_pct, rain_unstable=False):
    """Wykrywa pompowanie deszczówki na podstawie ciągłego spadku poziomu.

    Algorytm:
    1. Odczyty z rain_unstable=True są pomijane (deszcz zakłóca czujnik)
    2. Spadek >= RAIN_PUMP_DROP_PCT w oknie RAIN_PUMP_WINDOW_MIN -> początek pompowania
    3. Pompowanie trwa dopóki kolejne odczyty spadają (> szum czujnika)
    4. Koniec = RAIN_PUMP_STABLE_COUNT odczytów bez spadku
    5. Jeśli nowe pompowanie zaczyna się < 10 min po poprzednim -> scalenie
    6. Zapis: czas rozpoczęcia, czas trwania, litry, % przed/po
    """
    if rain_pct is None:
        return

    # Pomijaj niestabilne odczyty — nie wchodzą do bufora
    if rain_unstable:
        return

    now = datetime.now()
    det = rain_pump_detector

    det.recent_readings.append((now, rain_pct))

    # Ograniczamy bufor do ostatnich 20 min (zwiększone z 10)
    cutoff = now - timedelta(minutes=20)
    det.recent_readings = [(ts, pct) for ts, pct in det.recent_readings if ts >= cutoff]

    if det.pumping:
        # Trwa pompowanie — sprawdź czy nadal spada
        prev_pct = det.recent_readings[-2][1] if len(det.recent_readings) >= 2 else rain_pct
        if rain_pct >= prev_pct - settings["sensor_noise_cm"] * 0.7:
            # Brak spadku — zliczaj stabilne odczyty
            det.stable_count += 1
            if det.stable_count >= settings["rain_pump_stable_count"]:
                # Koniec pompowania
                duration_min = (now - det.pump_start).total_seconds() / 60
                liters = calc_liters(det.pct_before) - calc_liters(rain_pct)

                # Scalanie: jeśli < 10 min od ostatniego pompowania, podmień ostatni wpis
                merged = False
                if hasattr(det, "last_pump_end") and det.last_pump_end:
                    gap = (det.pump_start - det.last_pump_end).total_seconds() / 60
                    if gap < 10:
                        # Scal z poprzednim — użyj pct_before z poprzedniego
                        total_duration = (now - det.last_pump_start).total_seconds() / 60
                        total_liters = calc_liters(det.last_pct_before) - calc_liters(rain_pct)
                        logger.info(
                            "Scalono pompowanie deszczówki: %d min, %d l (%d%% -> %d%%)",
                            round(total_duration), round(total_liters),
                            det.last_pct_before, rain_pct
                        )
                        save_rain_usage(
                            det.last_pump_start, total_duration,
                            total_liters, det.last_pct_before, rain_pct,
                            merge_last=True
                        )
                        merged = True

                if not merged:
                    logger.info(
                        "Koniec pompowania deszczówki: %d min, %d l (%d%% -> %d%%)",
                        round(duration_min), round(liters), det.pct_before, rain_pct
                    )
                    save_rain_usage(det.pump_start, duration_min, liters, det.pct_before, rain_pct)

                det.last_pump_end = now
                det.last_pump_start = det.last_pump_start if merged else det.pump_start
                det.last_pct_before = det.last_pct_before if merged else det.pct_before
                det.pumping = False
                det.pump_start = None
                det.pct_before = None
                det.stable_count = 0
        else:
            # Nadal spada
            det.stable_count = 0
    else:
        # Nie pompuje — sprawdź czy zaczęło się pompowanie
        if len(det.recent_readings) < 2:
            return
        window_cutoff = now - timedelta(minutes=settings["rain_pump_window_min"])
        window_readings = [(ts, pct) for ts, pct in det.recent_readings if ts >= window_cutoff]
        if len(window_readings) < 2:
            return
        oldest_pct = window_readings[0][1]
        drop = oldest_pct - rain_pct
        if drop >= settings["rain_pump_drop_pct"]:
            det.pumping = True
            det.pump_start = window_readings[0][0]
            det.pct_before = oldest_pct
            det.stable_count = 0
            logger.info(
                "Wykryto pompowanie deszczówki: spadek %d pp (%d%% -> %d%%)",
                round(drop), oldest_pct, rain_pct
            )


# ─── Niestabilność czujnika deszczówki ───────────────────────────

# Próg rozrzutu odczytów (cm) powyżej którego uznajemy niestabilność.
# Normalny szum czujnika to +-1 cm (spread <= 2 cm).
# Przy deszczu rozpryski mogą powodować spread > 5 cm.
INSTABILITY_SPREAD_THRESHOLD_CM = 5


def check_rain_sensor_instability(rain_spread, waste_spread):
    """Wykrywa niestabilność czujnika deszczówki i koreluje z opadami.

    Jeśli rozrzut odczytów deszczówki > INSTABILITY_SPREAD_THRESHOLD_CM:
    - Sprawdza aktualne opady z rainfall.csv
    - Zapisuje zdarzenie do rain_instability.csv
    - Jeśli koreluje z opadami: email z informacją (cooldown 2h)

    Porównanie z rozrzutem szamba pozwala odróżnić problem z czujnikiem
    deszczówki od ogólnego problemu z elektroniką.

    Zwraca True jeśli czujnik jest niestabilny (odczyty nie powinny być
    uwzględniane w statystykach efektywności/sezonowych).
    """
    if rain_spread is None or rain_spread <= INSTABILITY_SPREAD_THRESHOLD_CM:
        return False

    # Pobierz aktualne opady z ostatniego wpisu rainfall.csv
    rain_rows = load_rainfall(days=1)
    precipitation_mm = None
    if rain_rows:
        try:
            precipitation_mm = float(rain_rows[-1].get("precipitation_mm", 0))
        except (ValueError, TypeError):
            pass

    # Zapisz zdarzenie
    save_rain_instability(rain_spread, waste_spread, precipitation_mm)

    is_rain_correlated = precipitation_mm is not None and precipitation_mm > 0.5
    waste_stable = waste_spread is not None and waste_spread <= INSTABILITY_SPREAD_THRESHOLD_CM

    # Log
    intensity = _rain_intensity(precipitation_mm)
    correlation_info = ""
    if is_rain_correlated:
        correlation_info = f", opady {precipitation_mm:.1f} mm/h ({intensity}) — prawdopodobnie deszcz zakłóca czujnik"
    if waste_stable:
        correlation_info += f", szambo stabilne (spread {waste_spread:.0f} cm)"

    logger.warning(
        "Niestabilność czujnika deszczówki: spread %.1f cm%s",
        rain_spread, correlation_info
    )

    # Email — cooldown 2h (plik-based, przeżywa restart serwera)
    if not settings.get("alert_rain_instability_enabled", True):
        return True

    # Sprawdzamy przedostatni wpis w rain_instability.csv (ostatni to właśnie zapisany)
    cooldown_key = "rain_instability"
    now = datetime.now()
    instability_rows = load_rain_instability(limit=2)
    if len(instability_rows) >= 2:
        try:
            prev_ts = datetime.fromisoformat(instability_rows[1]["timestamp"])
            if (now - prev_ts) < timedelta(hours=2):
                alert_state.tank_last_sent[cooldown_key] = now
                return True
        except (ValueError, KeyError):
            pass

    subject = f"Deszczówka: niestabilny czujnik (rozrzut {rain_spread:.0f} cm)"
    body = (
        f"Wykryto niestabilność czujnika deszczówki.\n\n"
        f"Rozrzut odczytów: {rain_spread:.1f} cm (próg: {INSTABILITY_SPREAD_THRESHOLD_CM} cm)\n"
    )
    if waste_spread is not None:
        body += f"Rozrzut szamba: {waste_spread:.1f} cm (dla porównania)\n"
    if precipitation_mm is not None:
        intensity = _rain_intensity(precipitation_mm)
        body += f"Aktualne opady: {precipitation_mm:.1f} mm/h ({intensity})\n"
    if is_rain_correlated and waste_stable:
        body += (
            f"\nKorelacja z opadami: TAK — deszcz prawdopodobnie zakłóca pomiar "
            f"(rozpryski/fale na powierzchni wody w zbiorniku).\n"
            f"Czujnik szamba działa stabilnie, co potwierdza że problem dotyczy "
            f"tylko zbiornika deszczówki.\n"
        )
    elif is_rain_correlated:
        body += "\nKorelacja z opadami: TAK — możliwe zakłócenia od deszczu.\n"
    else:
        body += (
            "\nBrak korelacji z opadami — możliwa inna przyczyna niestabilności "
            "(problem z czujnikiem, zakłócenie elektryczne).\n"
        )
    body += f"\nData: {now.strftime('%d.%m.%Y %H:%M')}\n"

    send_email(subject, body)
    alert_type = "rain_instability_rain" if is_rain_correlated else "rain_instability"
    save_alert_log(alert_type, "Rainwater", "warning", subject)
    alert_state.tank_last_sent[cooldown_key] = now
    return True
