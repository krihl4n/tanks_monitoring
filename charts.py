"""Generatory wykresów SVG dla monitoringu zbiorników."""

from collections import defaultdict
from datetime import datetime, timedelta


MONTH_NAMES = ["Sty", "Lut", "Mar", "Kwi", "Maj", "Cze", "Lip", "Sie", "Wrz", "Paź", "Lis", "Gru"]


def generate_rainfall_chart(days, *, load_rainfall, load_history):
    """Generuje wykres SVG z opadami (słupki) i napełnieniem deszczówki (linia)."""
    W, H = 900, 300
    PAD_L, PAD_R, PAD_T, PAD_B = 60, 60, 22, 45

    rain_rows = load_rainfall(days=days)
    tank_rows = load_history(days=days)

    if not rain_rows and not tank_rows:
        return '<svg viewBox="0 0 %d %d"><text x="%d" y="%d" fill="#aaa" text-anchor="middle" font-size="14">Brak danych o opadach</text></svg>' % (W, H, W//2, H//2)

    # Zbierz dane dzienne
    daily_rain = {}  # "2026-04-29": suma mm
    daily_tank = {}  # "2026-04-29": średni %

    for row in rain_rows:
        try:
            ts = datetime.fromisoformat(row["timestamp"])
            key = ts.strftime("%Y-%m-%d")
            daily_rain[key] = daily_rain.get(key, 0) + float(row["precipitation_mm"])
        except (ValueError, KeyError):
            continue

    for row in tank_rows:
        try:
            ts = datetime.fromisoformat(row["timestamp"])
            key = ts.strftime("%Y-%m-%d")
            rp = row.get("rain_pct")
            if rp:
                if key not in daily_tank:
                    daily_tank[key] = []
                daily_tank[key].append(float(rp))
        except (ValueError, KeyError):
            continue

    all_days = sorted(set(list(daily_rain.keys()) + list(daily_tank.keys())))
    if not all_days:
        return '<svg viewBox="0 0 %d %d"><text x="%d" y="%d" fill="#aaa" text-anchor="middle" font-size="14">Brak danych</text></svg>' % (W, H, W//2, H//2)

    chart_w = W - PAD_L - PAD_R
    chart_h = H - PAD_T - PAD_B
    n_days = len(all_days)
    bar_w = max(2, chart_w / max(n_days, 1) * 0.6)

    max_rain = max((daily_rain.get(d, 0) for d in all_days), default=1)
    if max_rain == 0:
        max_rain = 1

    svg_parts = []

    # Siatka Y lewa (opady)
    for i in range(5):
        y = PAD_T + chart_h - (i / 4) * chart_h
        val = round(max_rain * i / 4, 1)
        svg_parts.append(f'<line x1="{PAD_L}" y1="{y:.1f}" x2="{W - PAD_R}" y2="{y:.1f}" stroke="#333" stroke-width="1"/>')
        svg_parts.append(f'<text x="{PAD_L - 8}" y="{y + 4:.1f}" fill="#48cae4" text-anchor="end" font-size="10">{val}mm</text>')

    # Siatka Y prawa (% deszczówki)
    for pct in [0, 25, 50, 75, 100]:
        y = PAD_T + chart_h - (pct / 100) * chart_h
        svg_parts.append(f'<text x="{W - PAD_R + 8}" y="{y + 4:.1f}" fill="#2a9d8f" text-anchor="start" font-size="10">{pct}%</text>')

    # Słupki opadów + linia deszczówki
    line_points = []

    for i, day in enumerate(all_days):
        x = PAD_L + (i + 0.5) / n_days * chart_w
        rain_mm = daily_rain.get(day, 0)
        bar_h = (rain_mm / max_rain) * chart_h
        y_bar = PAD_T + chart_h - bar_h
        svg_parts.append(f'<rect x="{x - bar_w/2:.1f}" y="{y_bar:.1f}" width="{bar_w:.1f}" height="{bar_h:.1f}" fill="#48cae4" opacity="0.5" rx="1"/>')

        tank_vals = daily_tank.get(day)
        if tank_vals:
            avg_pct = sum(tank_vals) / len(tank_vals)
            y_line = PAD_T + chart_h - (avg_pct / 100) * chart_h
            line_points.append(f"{x:.1f},{y_line:.1f}")

        # Etykiety X
        if n_days <= 14 or i % max(1, n_days // 8) == 0:
            dt = datetime.strptime(day, "%Y-%m-%d")
            label = dt.strftime("%d.%m")
            svg_parts.append(f'<text x="{x:.1f}" y="{H - 8:.1f}" fill="#aaa" text-anchor="middle" font-size="10">{label}</text>')

    if line_points:
        polyline = " ".join(line_points)
        svg_parts.append(f'<polyline points="{polyline}" fill="none" stroke="#2a9d8f" stroke-width="2"/>')

    svg = f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">\n' + "\n".join(svg_parts) + "\n</svg>"
    return svg


def generate_seasonal_rainwater_chart(*, load_history, sensor_noise_cm, tank_depth_cm, tank_capacity_l):
    """Generuje wykres SVG sezonowej analizy deszczówki — litry zebrane na miesiąc, rok do roku."""
    rows = load_history(days=730)  # max 2 lata
    if not rows:
        return '<p style="color:#666;text-align:center;">Brak danych</p>'

    # Netto: ostatni - pierwszy pomiar w danym miesiącu (pomijaj niestabilne)
    month_first = {}
    month_last = {}
    for row in rows:
        rp = row.get("rain_pct")
        if not rp:
            continue
        if row.get("rain_unstable") == "1":
            continue
        try:
            ts = datetime.fromisoformat(row["timestamp"])
            pct = float(rp)
        except (ValueError, KeyError):
            continue
        key = (ts.year, ts.month)
        if key not in month_first:
            month_first[key] = pct
        month_last[key] = pct

    monthly_gain = {}
    for key in month_first:
        delta = month_last[key] - month_first[key]
        min_delta_pct = sensor_noise_cm / tank_depth_cm * 100
        if delta > min_delta_pct:
            monthly_gain[key] = delta / 100 * tank_capacity_l

    if not monthly_gain:
        return '<p style="color:#666;text-align:center;">Brak danych o zbieraniu deszczówki</p>'

    years = sorted(set(y for y, m in monthly_gain))
    year_colors = ["#48cae4", "#2a9d8f", "#e9c46a", "#e76f51"]
    month_names_short = ["Sty", "Lut", "Mar", "Kwi", "Maj", "Cze",
                         "Lip", "Sie", "Wrz", "Paź", "Lis", "Gru"]

    W, H = 900, 300
    PAD_L, PAD_R, PAD_T, PAD_B = 60, 20, 30, 50

    max_val = max(monthly_gain.values()) if monthly_gain else 1
    max_val = max(max_val, 100)

    chart_w = W - PAD_L - PAD_R
    chart_h = H - PAD_T - PAD_B
    n_months = 12
    n_years = len(years)
    group_w = chart_w / n_months
    bar_w = max(8, (group_w - 8) / max(n_years, 1))

    svg_parts = []
    # Oś Y
    for i in range(5):
        val = round(max_val * i / 4)
        y = PAD_T + chart_h - (val / max_val * chart_h)
        svg_parts.append(f'<line x1="{PAD_L}" y1="{y:.1f}" x2="{W - PAD_R}" y2="{y:.1f}" stroke="#333" stroke-width="0.5"/>')
        svg_parts.append(f'<text x="{PAD_L - 8}" y="{y + 4:.1f}" fill="#aaa" text-anchor="end" font-size="10">{val:.0f} l</text>')

    # Słupki
    for mi in range(12):
        month_num = mi + 1
        gx = PAD_L + mi * group_w
        svg_parts.append(f'<text x="{gx + group_w / 2:.1f}" y="{H - 10:.1f}" fill="#aaa" text-anchor="middle" font-size="10">{month_names_short[mi]}</text>')

        for yi, year in enumerate(years):
            val = monthly_gain.get((year, month_num), 0)
            if val <= 0:
                continue
            bar_h = val / max_val * chart_h
            bx = gx + 4 + yi * bar_w
            by = PAD_T + chart_h - bar_h
            color = year_colors[yi % len(year_colors)]
            svg_parts.append(f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bar_w - 2:.1f}" height="{bar_h:.1f}" fill="{color}" rx="2" opacity="0.85"/>')

    # Legenda
    for yi, year in enumerate(years):
        lx = PAD_L + yi * 80
        color = year_colors[yi % len(year_colors)]
        svg_parts.append(f'<rect x="{lx}" y="8" width="12" height="12" fill="{color}" rx="2"/>')
        svg_parts.append(f'<text x="{lx + 16}" y="18" fill="#ccc" font-size="11">{year}</text>')

    # Suma
    for yi, year in enumerate(years):
        total = sum(v for (y, m), v in monthly_gain.items() if y == year)
        lx = PAD_L + yi * 80
        svg_parts.append(f'<text x="{lx}" y="{H - 30:.1f}" fill="#aaa" font-size="9">Σ {total:.0f} l</text>')

    svg = f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">\n' + "\n".join(svg_parts) + "\n</svg>"
    return svg


def _generate_hourly_usage_chart(rows, *, tank_capacity_l):
    """Generuje wykres SVG słupkowy zużycia szamba per godzina (widok 24h)."""
    slot_first = {}
    slot_last = {}
    slot_times = {}

    for row in rows:
        wp = row.get("waste_pct")
        if not wp:
            continue
        try:
            ts = datetime.fromisoformat(row["timestamp"])
            pct = float(wp)
        except (ValueError, KeyError):
            continue
        key = ts.strftime("%Y-%m-%dT%H")
        if key not in slot_first:
            slot_first[key] = pct
            slot_times[key] = ts.replace(minute=0, second=0)
        slot_last[key] = pct

    if not slot_first:
        return '<svg viewBox="0 0 900 250"><text x="450" y="125" fill="#aaa" text-anchor="middle" font-size="14">Brak danych</text></svg>'

    all_slots = sorted(slot_times.keys())
    start_dt = slot_times[all_slots[0]]
    end_dt = slot_times[all_slots[-1]]

    ordered_slots = []
    dt = start_dt
    while dt <= end_dt:
        key = dt.strftime("%Y-%m-%dT%H")
        delta = 0.0
        if key in slot_first:
            delta = max(0, slot_last[key] - slot_first[key])
        ordered_slots.append((dt, delta / 100 * tank_capacity_l))
        dt += timedelta(hours=1)

    if not ordered_slots:
        return '<svg viewBox="0 0 900 250"><text x="450" y="125" fill="#aaa" text-anchor="middle" font-size="14">Brak danych</text></svg>'

    W, H = 900, 270
    PAD_L, PAD_R, PAD_T, PAD_B = 55, 20, 22, 45

    n = len(ordered_slots)
    max_val = max((v for _, v in ordered_slots), default=10)
    max_val = max(max_val, 10)

    chart_w = W - PAD_L - PAD_R
    chart_h = H - PAD_T - PAD_B
    bar_w = max(4, chart_w / max(n, 1) - 2)

    svg_parts = []

    # Oś Y
    for i in range(5):
        val = round(max_val * i / 4)
        y = PAD_T + chart_h - (val / max_val * chart_h)
        svg_parts.append(f'<line x1="{PAD_L}" y1="{y:.1f}" x2="{W - PAD_R}" y2="{y:.1f}" stroke="#333" stroke-width="0.5"/>')
        svg_parts.append(f'<text x="{PAD_L - 8}" y="{y + 4:.1f}" fill="#aaa" text-anchor="end" font-size="10">{val:.0f} l</text>')

    spans_midnight = ordered_slots[0][0].date() != ordered_slots[-1][0].date()

    # Słupki
    for i, (dt, val) in enumerate(ordered_slots):
        bar_h = val / max_val * chart_h if val > 0 else 0
        x = PAD_L + i * (chart_w / n) + 1
        y = PAD_T + chart_h - bar_h

        color = "#a67c52"
        opacity = "0.85" if val > 0 else "0.15"
        svg_parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{max(bar_h, 1):.1f}" fill="{color}" rx="2" opacity="{opacity}"/>')

        if bar_h > 15:
            svg_parts.append(f'<text x="{x + bar_w / 2:.1f}" y="{y - 3:.1f}" fill="#ddd" text-anchor="middle" font-size="9">{val:.0f}</text>')

        label_every = max(1, n // 12)
        if i % label_every == 0 or i == n - 1:
            hour_label = dt.strftime("%H:00")
            svg_parts.append(f'<text x="{x + bar_w / 2:.1f}" y="{H - 18:.1f}" fill="#aaa" text-anchor="middle" font-size="9">{hour_label}</text>')
            if spans_midnight:
                date_label = dt.strftime("%d.%m")
                svg_parts.append(f'<text x="{x + bar_w / 2:.1f}" y="{H - 8:.1f}" fill="#666" text-anchor="middle" font-size="8">{date_label}</text>')

    svg = f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">\n' + "\n".join(svg_parts) + "\n</svg>"
    return svg


def _generate_monthly_usage_chart(daily_gain, incomplete_days):
    """Generuje wykres SVG średniego dziennego zużycia szamba per miesiąc (widok roczny)."""
    month_totals = defaultdict(list)

    for day_str, liters in daily_gain.items():
        if day_str in incomplete_days:
            continue
        month_key = day_str[:7]
        month_totals[month_key].append(liters)

    if not month_totals:
        return '<svg viewBox="0 0 900 250"><text x="450" y="125" fill="#aaa" text-anchor="middle" font-size="14">Brak danych</text></svg>'

    sorted_months = sorted(month_totals.keys())
    monthly_avg = {}
    monthly_count = {}
    for m in sorted_months:
        vals = month_totals[m]
        monthly_avg[m] = sum(vals) / len(vals) if vals else 0
        monthly_count[m] = len(vals)

    W, H = 900, 270
    PAD_L, PAD_R, PAD_T, PAD_B = 55, 20, 22, 50

    n = len(sorted_months)
    max_val = max(monthly_avg.values())
    max_val = max(max_val, 50)

    chart_w = W - PAD_L - PAD_R
    chart_h = H - PAD_T - PAD_B
    bar_w = max(10, min(chart_w / max(n, 1) - 6, 60))

    overall_avg = sum(monthly_avg.values()) / n

    svg_parts = []

    # Oś Y
    for i in range(5):
        val = round(max_val * i / 4)
        y = PAD_T + chart_h - (val / max_val * chart_h)
        svg_parts.append(f'<line x1="{PAD_L}" y1="{y:.1f}" x2="{W - PAD_R}" y2="{y:.1f}" stroke="#333" stroke-width="0.5"/>')
        svg_parts.append(f'<text x="{PAD_L - 8}" y="{y + 4:.1f}" fill="#aaa" text-anchor="end" font-size="10">{val:.0f} l</text>')

    # Linia średniej
    avg_y = PAD_T + chart_h - (overall_avg / max_val * chart_h)
    svg_parts.append(f'<line x1="{PAD_L}" y1="{avg_y:.1f}" x2="{W - PAD_R}" y2="{avg_y:.1f}" stroke="#e9c46a" stroke-width="1" stroke-dasharray="6,3"/>')
    svg_parts.append(f'<text x="{W - PAD_R + 2}" y="{avg_y + 4:.1f}" fill="#e9c46a" font-size="9">śr. {overall_avg:.0f} l/d</text>')

    # Słupki
    for i, month in enumerate(sorted_months):
        val = monthly_avg[month]
        bar_h = val / max_val * chart_h if val > 0 else 0
        x = PAD_L + i * (chart_w / n) + (chart_w / n - bar_w) / 2
        y = PAD_T + chart_h - bar_h

        svg_parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{max(bar_h, 1):.1f}" fill="#a67c52" rx="3" opacity="0.85"/>')

        if bar_h > 15:
            svg_parts.append(f'<text x="{x + bar_w / 2:.1f}" y="{y - 4:.1f}" fill="#ccc" text-anchor="middle" font-size="10">{val:.0f} l/d</text>')

        try:
            dt = datetime.strptime(month + "-01", "%Y-%m-%d")
            month_label = MONTH_NAMES[dt.month - 1]
            year_label = dt.strftime("%Y")
        except ValueError:
            month_label = month
            year_label = ""

        svg_parts.append(f'<text x="{x + bar_w / 2:.1f}" y="{H - 22:.1f}" fill="#eee" text-anchor="middle" font-size="10" font-weight="500">{month_label}</text>')
        svg_parts.append(f'<text x="{x + bar_w / 2:.1f}" y="{H - 10:.1f}" fill="#666" text-anchor="middle" font-size="8">{year_label} ({monthly_count[month]}d)</text>')

    svg = f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">\n' + "\n".join(svg_parts) + "\n</svg>"
    return svg


def generate_waste_daily_chart(days, *, load_history, calc_daily_waste_gain, tank_capacity_l):
    """Generuje wykres SVG zużycia szamba.

    Przy days <= 1 (widok 24h): profil godzinowy.
    Przy days <= 90 (7d/30d): słupki dziennego zużycia.
    Przy days > 90 (365d): słupki średniego dziennego zużycia per miesiąc.
    """
    rows = load_history(days=days)
    if not rows:
        return '<svg viewBox="0 0 900 250"><text x="450" y="125" fill="#aaa" text-anchor="middle" font-size="14">Brak danych</text></svg>'

    if days <= 1:
        return _generate_hourly_usage_chart(rows, tank_capacity_l=tank_capacity_l)

    daily_gain = calc_daily_waste_gain(rows)

    if not daily_gain:
        return '<svg viewBox="0 0 900 250"><text x="450" y="125" fill="#aaa" text-anchor="middle" font-size="14">Brak danych o zużyciu</text></svg>'

    # Znajdź pierwszy i ostatni dzień z danymi w CSV — oznacz jako niepełne
    all_rows = load_history(days=9999)
    first_day_ever = None
    if all_rows:
        for r in all_rows:
            try:
                first_day_ever = datetime.fromisoformat(r["timestamp"]).strftime("%Y-%m-%d")
                break
            except (ValueError, KeyError):
                continue
    today = datetime.now().strftime("%Y-%m-%d")
    incomplete_days = {d for d in [first_day_ever, today] if d}

    if days > 90:
        return _generate_monthly_usage_chart(daily_gain, incomplete_days)

    # --- Widok dzienny (7d / 30d) ---
    first_date = datetime.strptime(min(daily_gain.keys()), "%Y-%m-%d").date()
    last_date = datetime.strptime(max(daily_gain.keys()), "%Y-%m-%d").date()
    all_days = []
    d = first_date
    while d <= last_date:
        all_days.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)

    W, H = 900, 290
    PAD_L, PAD_R, PAD_T, PAD_B = 55, 20, 22, 68

    n_days = len(all_days)
    max_val = max(daily_gain.values())
    max_val = max(max_val, 50)

    chart_w = W - PAD_L - PAD_R
    chart_h = H - PAD_T - PAD_B

    max_bar_w = 50
    slot_w = min(chart_w / max(n_days, 1), max_bar_w + 10)
    bar_w = max(4, slot_w - 6)
    used_w = n_days * slot_w
    offset_x = PAD_L + (chart_w - used_w) / 2

    avg_val = sum(daily_gain.values()) / len(daily_gain)

    day_names = ["Pn", "Wt", "Śr", "Cz", "Pt", "So", "Nd"]

    svg_parts = []

    # Oś Y
    for i in range(5):
        val = round(max_val * i / 4)
        y = PAD_T + chart_h - (val / max_val * chart_h)
        svg_parts.append(f'<line x1="{PAD_L}" y1="{y:.1f}" x2="{W - PAD_R}" y2="{y:.1f}" stroke="#333" stroke-width="0.5"/>')
        svg_parts.append(f'<text x="{PAD_L - 8}" y="{y + 4:.1f}" fill="#aaa" text-anchor="end" font-size="10">{val:.0f} l</text>')

    # Linia średniej
    avg_y = PAD_T + chart_h - (avg_val / max_val * chart_h)
    svg_parts.append(f'<line x1="{PAD_L}" y1="{avg_y:.1f}" x2="{W - PAD_R}" y2="{avg_y:.1f}" stroke="#e9c46a" stroke-width="1" stroke-dasharray="6,3"/>')
    svg_parts.append(f'<text x="{W - PAD_R + 2}" y="{avg_y + 4:.1f}" fill="#e9c46a" font-size="9">śr. {avg_val:.0f} l</text>')

    # Słupki
    for i, day in enumerate(all_days):
        val = daily_gain.get(day, 0)
        bar_h = val / max_val * chart_h if val > 0 else 0
        x = offset_x + i * slot_w + (slot_w - bar_w) / 2
        y = PAD_T + chart_h - bar_h

        dt = datetime.strptime(day, "%Y-%m-%d")
        is_weekend = dt.weekday() >= 5
        color = "#e76f51" if is_weekend else "#a67c52"
        if day in incomplete_days:
            opacity = "0.4"
        elif val == 0:
            opacity = "0.15"
        else:
            opacity = "0.85"

        svg_parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{max(bar_h, 1):.1f}" fill="{color}" rx="2" opacity="{opacity}"/>')
        if day in incomplete_days:
            svg_parts.append(f'<text x="{x + bar_w / 2:.1f}" y="{y - 3:.1f}" fill="#666" text-anchor="middle" font-size="8">~</text>')

        # Etykiety X
        chart_bottom = PAD_T + chart_h
        if n_days <= 14 or i % max(1, n_days // 10) == 0:
            label = dt.strftime("%d.%m")
            dn = day_names[dt.weekday()]
            svg_parts.append(f'<text x="{x + bar_w / 2:.1f}" y="{chart_bottom + 15:.1f}" fill="#aaa" text-anchor="middle" font-size="9">{label}</text>')
            svg_parts.append(f'<text x="{x + bar_w / 2:.1f}" y="{chart_bottom + 26:.1f}" fill="#666" text-anchor="middle" font-size="8">{dn}</text>')

    # Legenda
    legend_y = PAD_T + chart_h + 40
    svg_parts.append(f'<rect x="{PAD_L}" y="{legend_y}" width="8" height="8" fill="#a67c52" rx="1"/>')
    svg_parts.append(f'<text x="{PAD_L + 12}" y="{legend_y + 7}" fill="#aaa" font-size="9">dzień roboczy</text>')
    svg_parts.append(f'<rect x="{PAD_L + 95}" y="{legend_y}" width="8" height="8" fill="#e76f51" rx="1"/>')
    svg_parts.append(f'<text x="{PAD_L + 107}" y="{legend_y + 7}" fill="#aaa" font-size="9">weekend</text>')
    svg_parts.append(f'<rect x="{PAD_L + 165}" y="{legend_y}" width="8" height="8" fill="#a67c52" rx="1" opacity="0.4"/>')
    svg_parts.append(f'<text x="{PAD_L + 177}" y="{legend_y + 7}" fill="#aaa" font-size="9">~ niepełny dzień</text>')

    svg = f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">\n' + "\n".join(svg_parts) + "\n</svg>"
    return svg


def generate_waste_weekday_chart(days=90, *, load_history, calc_daily_waste_gain):
    """Generuje wykres SVG średniego zużycia szamba per dzień tygodnia (Pn-Nd)."""
    rows = load_history(days=days)
    if not rows:
        return '<svg viewBox="0 0 900 250"><text x="450" y="125" fill="#aaa" text-anchor="middle" font-size="14">Brak danych</text></svg>'

    daily_gain = calc_daily_waste_gain(rows)

    if not daily_gain:
        return '<svg viewBox="0 0 900 250"><text x="450" y="125" fill="#aaa" text-anchor="middle" font-size="14">Brak danych</text></svg>'

    # Pomiń pierwszy i ostatni dzień (niepełne)
    all_rows_check = load_history(days=9999)
    first_day = None
    if all_rows_check:
        for r in all_rows_check:
            try:
                first_day = datetime.fromisoformat(r["timestamp"]).strftime("%Y-%m-%d")
                break
            except (ValueError, KeyError):
                continue
    today = datetime.now().strftime("%Y-%m-%d")
    skip = {d for d in [first_day, today] if d}

    # Grupuj po dniu tygodnia
    weekday_totals = {i: [] for i in range(7)}
    for day_str, liters in daily_gain.items():
        if day_str in skip:
            continue
        try:
            dt = datetime.strptime(day_str, "%Y-%m-%d")
            weekday_totals[dt.weekday()].append(liters)
        except ValueError:
            continue

    total_days = sum(len(v) for v in weekday_totals.values())
    if total_days < 7:
        return f'<svg viewBox="0 0 900 250"><text x="450" y="125" fill="#aaa" text-anchor="middle" font-size="14">Za mało danych (potrzeba min. 2 tygodni, jest {total_days} dni)</text></svg>'

    day_short = ["Pn", "Wt", "Śr", "Cz", "Pt", "So", "Nd"]

    avgs = []
    counts = []
    for i in range(7):
        vals = weekday_totals[i]
        avgs.append(sum(vals) / len(vals) if vals else 0)
        counts.append(len(vals))

    W, H = 900, 270
    PAD_L, PAD_R, PAD_T, PAD_B = 55, 20, 22, 55
    chart_w = W - PAD_L - PAD_R
    chart_h = H - PAD_T - PAD_B
    max_val = max(avgs) if avgs else 1
    max_val = max(max_val, 50)
    bar_w = chart_w / 7 - 10
    non_zero = [a for a in avgs if a > 0]
    overall_avg = sum(non_zero) / len(non_zero) if non_zero else 0

    svg_parts = []

    # Oś Y
    for i in range(5):
        val = round(max_val * i / 4)
        y = PAD_T + chart_h - (val / max_val * chart_h)
        svg_parts.append(f'<line x1="{PAD_L}" y1="{y:.1f}" x2="{W - PAD_R}" y2="{y:.1f}" stroke="#333" stroke-width="0.5"/>')
        svg_parts.append(f'<text x="{PAD_L - 8}" y="{y + 4:.1f}" fill="#aaa" text-anchor="end" font-size="10">{val:.0f} l</text>')

    # Linia średniej
    if overall_avg > 0:
        avg_y = PAD_T + chart_h - (overall_avg / max_val * chart_h)
        svg_parts.append(f'<line x1="{PAD_L}" y1="{avg_y:.1f}" x2="{W - PAD_R}" y2="{avg_y:.1f}" stroke="#e9c46a" stroke-width="1" stroke-dasharray="6,3"/>')
        svg_parts.append(f'<text x="{W - PAD_R + 2}" y="{avg_y + 4:.1f}" fill="#e9c46a" font-size="9">śr. {overall_avg:.0f} l</text>')

    # Słupki
    for i in range(7):
        val = avgs[i]
        bar_h = val / max_val * chart_h if val > 0 else 0
        x = PAD_L + i * (chart_w / 7) + 5
        y = PAD_T + chart_h - bar_h
        color = "#e76f51" if i >= 5 else "#a67c52"

        if bar_h > 0:
            svg_parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{bar_h:.1f}" fill="{color}" rx="3" opacity="0.85"/>')
            svg_parts.append(f'<text x="{x + bar_w / 2:.1f}" y="{y - 4:.1f}" fill="#ccc" text-anchor="middle" font-size="10">{val:.0f} l</text>')

        svg_parts.append(f'<text x="{x + bar_w / 2:.1f}" y="{H - 20:.1f}" fill="#eee" text-anchor="middle" font-size="11" font-weight="500">{day_short[i]}</text>')
        svg_parts.append(f'<text x="{x + bar_w / 2:.1f}" y="{H - 8:.1f}" fill="#666" text-anchor="middle" font-size="8">({counts[i]} dni)</text>')

    svg = f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">\n' + "\n".join(svg_parts) + "\n</svg>"
    return svg


def generate_rain_efficiency_chart(days, *, load_rainfall, load_history, tank_capacity_l):
    """Generuje wykres SVG korelacji: ile litrów deszczówki zbierasz na mm opadów."""
    rain_rows = load_rainfall(days=days)
    tank_rows = load_history(days=days)

    if not rain_rows or not tank_rows:
        return '<svg viewBox="0 0 900 280"><text x="450" y="140" fill="#aaa" text-anchor="middle" font-size="14">Brak danych</text></svg>'

    # Dzienne opady
    daily_rain = {}
    for row in rain_rows:
        try:
            ts = datetime.fromisoformat(row["timestamp"])
            key = ts.strftime("%Y-%m-%d")
            daily_rain[key] = daily_rain.get(key, 0) + float(row["precipitation_mm"])
        except (ValueError, KeyError):
            continue

    # Dzienne przyrosty deszczówki (pomijaj niestabilne odczyty)
    daily_gain = {}
    prev_pct = None
    for row in tank_rows:
        rp = row.get("rain_pct")
        if not rp or row.get("rain_unstable") == "1":
            prev_pct = None
            continue
        try:
            ts = datetime.fromisoformat(row["timestamp"])
            pct = float(rp)
        except (ValueError, KeyError):
            prev_pct = None
            continue
        if prev_pct is not None:
            delta = pct - prev_pct
            if delta > 0:
                liters = delta / 100 * tank_capacity_l
                key = ts.strftime("%Y-%m-%d")
                daily_gain[key] = daily_gain.get(key, 0) + liters
        prev_pct = pct

    # Połącz: dni z opadami > 0.5 mm i przyrostem > 0
    points = []
    for day in daily_rain:
        mm = daily_rain[day]
        liters = daily_gain.get(day, 0)
        if mm >= 0.5 and liters > 0:
            points.append((mm, liters))

    if len(points) < 3:
        return '<svg viewBox="0 0 900 280"><text x="450" y="140" fill="#aaa" text-anchor="middle" font-size="14">Za mało danych (min. 3 dni z opadami)</text></svg>'

    W, H = 900, 280
    PAD_L, PAD_R, PAD_T, PAD_B = 60, 30, 20, 50

    max_mm = max(p[0] for p in points) * 1.1
    max_l = max(p[1] for p in points) * 1.1
    max_mm = max(max_mm, 5)
    max_l = max(max_l, 100)

    chart_w = W - PAD_L - PAD_R
    chart_h = H - PAD_T - PAD_B

    svg_parts = []

    # Oś Y (litry)
    for i in range(5):
        val = round(max_l * i / 4)
        y = PAD_T + chart_h - (val / max_l * chart_h)
        svg_parts.append(f'<line x1="{PAD_L}" y1="{y:.1f}" x2="{W - PAD_R}" y2="{y:.1f}" stroke="#333" stroke-width="0.5"/>')
        svg_parts.append(f'<text x="{PAD_L - 8}" y="{y + 4:.1f}" fill="#aaa" text-anchor="end" font-size="10">{val:.0f} l</text>')

    # Oś X (mm)
    for i in range(5):
        val = round(max_mm * i / 4, 1)
        x = PAD_L + (val / max_mm * chart_w)
        svg_parts.append(f'<text x="{x:.1f}" y="{H - 10:.1f}" fill="#aaa" text-anchor="middle" font-size="10">{val:.1f} mm</text>')

    # Punkty
    for mm, liters in points:
        x = PAD_L + (mm / max_mm * chart_w)
        y = PAD_T + chart_h - (liters / max_l * chart_h)
        svg_parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="#48cae4" opacity="0.7"/>')

    # Linia trendu (regresja liniowa)
    n = len(points)
    sum_x = sum(p[0] for p in points)
    sum_y = sum(p[1] for p in points)
    sum_xx = sum(p[0] ** 2 for p in points)
    sum_xy = sum(p[0] * p[1] for p in points)
    denom = n * sum_xx - sum_x * sum_x
    if denom != 0:
        a = (n * sum_xy - sum_x * sum_y) / denom
        b = (sum_y - a * sum_x) / n

        x1 = PAD_L
        x2 = PAD_L + chart_w
        mm2 = max_mm
        l1 = max(0, b)
        l2 = max(0, a * mm2 + b)
        y1 = PAD_T + chart_h - (l1 / max_l * chart_h)
        y2 = PAD_T + chart_h - (l2 / max_l * chart_h)
        svg_parts.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="#2a9d8f" stroke-width="2" stroke-dasharray="6,3" opacity="0.8"/>')

        if a > 0:
            svg_parts.append(f'<text x="{W - PAD_R}" y="{PAD_T + 14}" fill="#2a9d8f" text-anchor="end" font-size="11">~{a:.0f} l/mm</text>')

    # Etykiety osi
    svg_parts.append(f'<text x="{PAD_L - 10}" y="{PAD_T - 4}" fill="#888" font-size="9">litry</text>')
    svg_parts.append(f'<text x="{W - PAD_R}" y="{H - 30}" fill="#888" text-anchor="end" font-size="9">opady (mm)</text>')

    svg = f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">\n' + "\n".join(svg_parts) + "\n</svg>"
    return svg


def generate_svg_chart(rows, value_key, line_color, fill_color, time_unit):
    """Generuje wykres SVG po stronie serwera."""
    W, H = 900, 270
    PAD_L, PAD_R, PAD_T, PAD_B = 50, 20, 22, 45

    points = []
    for row in rows:
        val = row.get(value_key)
        if val:
            try:
                ts = datetime.fromisoformat(row["timestamp"])
                points.append((ts, float(val)))
            except (ValueError, KeyError):
                continue

    if not points:
        return '<svg viewBox="0 0 %d %d"><text x="%d" y="%d" fill="#aaa" text-anchor="middle" font-size="14">Brak danych</text></svg>' % (W, H, W//2, H//2)

    t_min = points[0][0].timestamp()
    t_max = points[-1][0].timestamp()
    if t_max == t_min:
        t_max = t_min + 1

    chart_w = W - PAD_L - PAD_R
    chart_h = H - PAD_T - PAD_B

    def tx(t):
        return PAD_L + (t.timestamp() - t_min) / (t_max - t_min) * chart_w

    def ty(v):
        return PAD_T + chart_h - (v / 100.0) * chart_h

    # Buduj ścieżkę linii
    line_parts = []
    for i, (t, v) in enumerate(points):
        cmd = "M" if i == 0 else "L"
        line_parts.append(f"{cmd}{tx(t):.1f},{ty(v):.1f}")
    line_path = "".join(line_parts)

    # Ścieżka wypełnienia
    fill_path = line_path + f"L{tx(points[-1][0]):.1f},{PAD_T + chart_h:.1f}L{tx(points[0][0]):.1f},{PAD_T + chart_h:.1f}Z"

    # Etykiety osi Y
    y_labels = ""
    for pct in [0, 25, 50, 75, 100]:
        y = ty(pct)
        y_labels += f'<line x1="{PAD_L}" y1="{y:.1f}" x2="{W - PAD_R}" y2="{y:.1f}" stroke="#333" stroke-width="1"/>'
        y_labels += f'<text x="{PAD_L - 8}" y="{y + 4:.1f}" fill="#aaa" text-anchor="end" font-size="11">{pct}%</text>'

    # Etykiety osi X
    x_labels = ""
    label_count = 8

    for i in range(label_count + 1):
        frac = i / label_count
        t_val = t_min + frac * (t_max - t_min)
        x = PAD_L + frac * chart_w
        dt = datetime.fromtimestamp(t_val)

        if time_unit == "hour":
            label = dt.strftime("%H:%M")
        elif time_unit == "month":
            label = dt.strftime("%m.%Y")
        else:
            label = dt.strftime("%d.%m")

        x_labels += f'<line x1="{x:.1f}" y1="{PAD_T}" x2="{x:.1f}" y2="{PAD_T + chart_h}" stroke="#333" stroke-width="1"/>'
        x_labels += f'<text x="{x:.1f}" y="{H - 8:.1f}" fill="#aaa" text-anchor="middle" font-size="11">{label}</text>'

    svg = f'''<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">
  {y_labels}
  {x_labels}
  <path d="{fill_path}" fill="{fill_color}" opacity="0.3"/>
  <path d="{line_path}" fill="none" stroke="{line_color}" stroke-width="2"/>
</svg>'''
    return svg
