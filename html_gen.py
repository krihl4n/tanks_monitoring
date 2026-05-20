"""Generatory HTML — badge statusu, prognoza pogody, tabele, wrappery wykresów SVG."""

import logging
from datetime import datetime

import charts as _charts
from config import settings
from data import load_history, load_pumpouts, load_rainfall
from calculations import calc_daily_waste_gain, fetch_weather_forecast

logger = logging.getLogger("tanks")


# ─── Badge statusu zbiornika ─────────────────────────────────────


def generate_tank_status_badge(tank_name, pct, raw_dist):
    """Generuje mały badge statusu dla konkretnego zbiornika.

    Zwraca HTML span z ikoną i kolorem:
    - Krytyczne (czerwony): czujnik nie odpowiada, przepełnienie critical, niski poziom critical
    - Uwaga (żółty): przepełnienie warning, niski poziom warning
    - OK (zielony): norma
    """
    colors = {"ok": "#2a9d8f", "warning": "#e9c46a", "critical": "#e74c3c"}
    icons = {"ok": "&#10004;", "warning": "&#9888;", "critical": "&#10006;"}
    labels = {"ok": "OK", "warning": "Uwaga", "critical": "!"}

    if raw_dist is None:
        level = "critical"
        tip = "Czujnik nie odpowiada"
    elif tank_name == "Waste":
        if pct >= settings["alert_waste_high_critical_pct"]:
            level = "critical"
            tip = f"Napełnienie {pct}%"
        elif pct >= settings["alert_waste_high_warning_pct"]:
            level = "warning"
            tip = f"Napełnienie {pct}%"
        else:
            level = "ok"
            tip = ""
    else:  # Deszczówka — sprawdź przepełnienie i niski poziom, pokaż gorszy
        level = "ok"
        tip = ""
        # Przepełnienie
        if pct >= settings["alert_rain_high_critical_pct"]:
            level = "critical"
            tip = f"Napełnienie {pct}%"
        elif pct >= settings["alert_rain_high_warning_pct"]:
            level = "warning"
            tip = f"Napełnienie {pct}%"
        # Niski poziom (nadpisuje jeśli gorszy)
        if pct <= settings["alert_rain_low_critical_pct"]:
            level = "critical"
            tip = f"Niski poziom ({pct}%)"
        elif pct <= settings["alert_rain_low_warning_pct"] and level != "critical":
            level = "warning"
            tip = f"Niski poziom ({pct}%)"

    color = colors[level]
    icon = icons[level]
    label = labels[level]

    badge = f'<span class="status-badge" style="color:{color};font-size:0.75em;">{icon} {label}</span>'
    if tip:
        badge = f'<span class="status-badge" style="color:{color};font-size:0.75em;" title="{tip}">{icon} {tip}</span>'
    return badge


# ─── Prognoza pogody ─────────────────────────────────────────────


def generate_forecast_html():
    """Generuje HTML prognozy pogody na 3 dni."""
    forecast = fetch_weather_forecast()
    if not forecast:
        return ''

    DAY_NAMES = {
        0: "Pn", 1: "Wt", 2: "Śr", 3: "Cz", 4: "Pt", 5: "So", 6: "Nd",
    }

    html = '<div class="forecast-section"><h3>Prognoza pogody (3 dni)</h3><div class="forecast-cards">'
    for day in forecast:
        try:
            dt = datetime.strptime(day["date"], "%Y-%m-%d")
            dn = DAY_NAMES[dt.weekday()]
            date_str = dt.strftime("%d.%m")
        except ValueError:
            dn = ""
            date_str = day["date"]

        precip = day.get("precip_sum", 0) or 0
        prob = day.get("precip_prob", 0) or 0
        tmax = day.get("temp_max")
        tmin = day.get("temp_min")

        # Ikona deszczu
        if precip >= 5:
            rain_icon = "&#127783;"  # deszcz
            rain_color = "#48cae4"
        elif precip >= 0.5:
            rain_icon = "&#127782;"  # lekki deszcz
            rain_color = "#48cae4"
        else:
            rain_icon = "&#9728;"  # słońce
            rain_color = "#e9c46a"

        temp_str = ""
        if tmax is not None and tmin is not None:
            temp_str = f'<div style="font-size:0.8em;color:#aaa;">{tmin:.0f}° / {tmax:.0f}°</div>'

        html += f'<div class="forecast-card">'
        html += f'<div style="font-weight:600;color:#eee;">{dn} {date_str}</div>'
        html += f'<div style="font-size:1.5em;">{rain_icon}</div>'
        html += f'<div style="color:{rain_color};font-size:0.9em;font-weight:500;">{precip:.1f} mm</div>'
        html += f'<div style="font-size:0.8em;color:#888;">{prob}% szans</div>'
        html += temp_str
        html += '</div>'

    html += '</div></div>'
    return html


# ─── Wrappery wykresów SVG ────────────────────────────────────────


def generate_rainfall_chart(days):
    return _charts.generate_rainfall_chart(days, load_rainfall=load_rainfall, load_history=load_history)


def generate_seasonal_rainwater_chart():
    return _charts.generate_seasonal_rainwater_chart(
        load_history=load_history, sensor_noise_cm=settings["sensor_noise_cm"],
        tank_depth_cm=settings["tank_depth_cm"], tank_capacity_l=settings["tank_capacity_l"])


def generate_waste_daily_chart(days):
    return _charts.generate_waste_daily_chart(
        days, load_history=load_history, calc_daily_waste_gain=calc_daily_waste_gain,
        tank_capacity_l=settings["tank_capacity_l"])


def generate_waste_weekday_chart(days=90):
    return _charts.generate_waste_weekday_chart(
        days, load_history=load_history, calc_daily_waste_gain=calc_daily_waste_gain)


def generate_rain_efficiency_chart(days):
    return _charts.generate_rain_efficiency_chart(
        days, load_rainfall=load_rainfall, load_history=load_history,
        tank_capacity_l=settings["tank_capacity_l"])


def generate_svg_chart(rows, value_key, line_color, fill_color, time_unit, **kwargs):
    return _charts.generate_svg_chart(rows, value_key, line_color, fill_color, time_unit, **kwargs)


# ─── Tabela miesięczna ────────────────────────────────────────────


def generate_monthly_table():
    """Generuje tabelę porównawczą miesiąc do miesiąca."""
    rows = load_history(days=365)
    if not rows:
        return '<p style="color:#666;text-align:center;">Brak danych</p>'

    # Grupuj po miesiącu
    months = {}  # "2026-04": {"rain": [pcts], "waste": [pcts]}
    for row in rows:
        try:
            ts = datetime.fromisoformat(row["timestamp"])
            key = ts.strftime("%Y-%m")
        except (ValueError, KeyError):
            continue
        if key not in months:
            months[key] = {"rain": [], "waste": []}
        rp = row.get("rain_pct")
        wp = row.get("waste_pct")
        if rp:
            months[key]["rain"].append(float(rp))
        if wp:
            months[key]["waste"].append(float(wp))

    if not months:
        return '<p style="color:#666;text-align:center;">Brak danych</p>'

    html = '<table class="monthly-table"><thead><tr>'
    html += '<th>Miesiąc</th>'
    html += '<th>Deszczówka śr.</th><th>Deszczówka min/max</th>'
    html += '<th>Szambo śr.</th><th>Szambo min/max</th>'
    html += '</tr></thead><tbody>'

    month_names = {
        "01": "Styczeń", "02": "Luty", "03": "Marzec", "04": "Kwiecień",
        "05": "Maj", "06": "Czerwiec", "07": "Lipiec", "08": "Sierpień",
        "09": "Wrzesień", "10": "Październik", "11": "Listopad", "12": "Grudzień",
    }

    for key in sorted(months.keys(), reverse=True):
        data = months[key]
        year, mon = key.split("-")
        label = f"{month_names.get(mon, mon)} {year}"

        def fmt_stats(values):
            if not values:
                return '<td>—</td><td>—</td>'
            avg = round(sum(values) / len(values))
            mn = round(min(values))
            mx = round(max(values))
            return f'<td>{avg}%</td><td>{mn}% – {mx}%</td>'

        html += f'<tr><td>{label}</td>'
        html += fmt_stats(data["rain"])
        html += fmt_stats(data["waste"])
        html += '</tr>'

    html += '</tbody></table>'
    return html


# ─── Tabela kosztów szamba ────────────────────────────────────────


def generate_pumpout_costs_table():
    """Generuje tabelę kosztów szamba — wywóz na miesiąc/rok, koszt łączny i średni miesięczny."""
    pumpouts = load_pumpouts()
    if not pumpouts:
        return '<p style="color:#666;text-align:center;">Brak danych o wywozach</p>'

    month_names = {
        "01": "Styczeń", "02": "Luty", "03": "Marzec", "04": "Kwiecień",
        "05": "Maj", "06": "Czerwiec", "07": "Lipiec", "08": "Sierpień",
        "09": "Wrzesień", "10": "Październik", "11": "Listopad", "12": "Grudzień",
    }

    # Grupuj wywozy po roku i miesiącu
    yearly = {}   # year -> count
    monthly = {}  # (year, month_str) -> count
    for row in pumpouts:
        try:
            ts = datetime.fromisoformat(row["timestamp"])
        except (ValueError, KeyError):
            continue
        yearly[ts.year] = yearly.get(ts.year, 0) + 1
        key = (ts.year, f"{ts.month:02d}")
        monthly[key] = monthly.get(key, 0) + 1

    years = sorted(yearly.keys(), reverse=True)

    html = '<table class="monthly-table"><thead><tr>'
    html += '<th>Okres</th><th>Wywozy</th><th>Koszt</th><th>Śr. / miesiąc</th>'
    html += '</tr></thead><tbody>'

    for year in years:
        count = yearly[year]
        cost = count * settings["pumpout_cost_pln"]
        # Ile miesięcy z danymi w tym roku
        months_with_data = sum(1 for (y, m) in monthly if y == year)
        avg_monthly = cost / max(months_with_data, 1)
        html += f'<tr style="background:#1a2744;">'
        html += f'<td><strong>{year}</strong></td>'
        html += f'<td><strong>{count}</strong></td>'
        html += f'<td><strong>{cost} zł</strong></td>'
        html += f'<td><strong>{avg_monthly:.0f} zł</strong></td>'
        html += '</tr>'

        # Miesiące w danym roku
        year_months = sorted([(y, m) for (y, m) in monthly if y == year], reverse=True)
        for y, m in year_months:
            mc = monthly[(y, m)]
            mcost = mc * settings["pumpout_cost_pln"]
            label = month_names.get(m, m)
            html += f'<tr><td style="padding-left:20px;">{label}</td>'
            html += f'<td>{mc}</td><td>{mcost} zł</td><td>—</td></tr>'

    # Podsumowanie
    total_count = sum(yearly.values())
    total_cost = total_count * settings["pumpout_cost_pln"]
    total_months = len(set((y, m) for (y, m) in monthly))
    overall_avg = total_cost / max(total_months, 1)
    html += f'<tr style="border-top:2px solid #48cae4;background:#1a2744;">'
    html += f'<td><strong>RAZEM</strong></td>'
    html += f'<td><strong>{total_count}</strong></td>'
    html += f'<td><strong>{total_cost} zł</strong></td>'
    html += f'<td><strong>{overall_avg:.0f} zł</strong></td>'
    html += '</tr>'

    html += '</tbody></table>'
    return html
