"""
Crypto-overvågningsscript
==========================
Overvåger en liste af kryptovalutaer via CoinGecko's gratis API (ingen nøgle
krævet) og genererer et HTML-dashboard med alerts baseret på en kombination af:
  - Prisbevægelse de seneste 24 timer (%)
  - Volumen ift. gennemsnit
  - RSI (Relative Strength Index)

Kør scriptet manuelt, eller sæt det op til at køre automatisk via GitHub Actions
(se crypto.yml workflow-filen).

Installation:
    pip install requests pandas
"""

import requests
import pandas as pd
from datetime import datetime
import os
import time

# -----------------------------
# KONFIGURATION - ret her
# -----------------------------

# CoinGecko "id" for hver mønt (ikke samme som ticker-symbol!)
# Find flere id'er her: https://api.coingecko.com/api/v3/coins/list
COINS = [
    "bitcoin",
    "ethereum",
    "solana",
    "dogecoin",
    "ripple",
    "cardano",
    "avalanche-2",
]

# Tærskler - crypto bevæger sig generelt mere end aktier, så sat højere
PRICE_CHANGE_THRESHOLD = 5.0     # % ændring på 24t der udløser alert
VOLUME_SPIKE_MULTIPLIER = 1.8    # volumen skal være X gange 7-dages snit
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30

OUTPUT_DIR = "public"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "crypto.html")

COINGECKO_BASE = "https://api.coingecko.com/api/v3"


# -----------------------------
# BEREGNINGER
# -----------------------------

def calculate_rsi(prices, period=14):
    """Beregner RSI for en prisserie (pandas Series)."""
    delta = prices.diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = -delta.where(delta < 0, 0).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def get_market_data(coin_ids):
    """Henter aktuel pris, 24t-ændring og volumen for alle mønter i én forespørgsel."""
    url = f"{COINGECKO_BASE}/coins/markets"
    params = {
        "vs_currency": "usd",
        "ids": ",".join(coin_ids),
        "order": "market_cap_desc",
        "per_page": len(coin_ids),
        "page": 1,
        "sparkline": "false",
    }
    response = requests.get(url, params=params, timeout=15)
    response.raise_for_status()
    return {item["id"]: item for item in response.json()}


def get_historical_prices(coin_id, days=30):
    """Henter historiske priser til RSI-beregning."""
    url = f"{COINGECKO_BASE}/coins/{coin_id}/market_chart"
    params = {"vs_currency": "usd", "days": days, "interval": "daily"}
    response = requests.get(url, params=params, timeout=15)
    response.raise_for_status()
    data = response.json()
    prices = [p[1] for p in data["prices"]]
    return pd.Series(prices)


def get_historical_volume(coin_id, days=8):
    """Henter volumen for de seneste dage til at beregne gennemsnit."""
    url = f"{COINGECKO_BASE}/coins/{coin_id}/market_chart"
    params = {"vs_currency": "usd", "days": days, "interval": "daily"}
    response = requests.get(url, params=params, timeout=15)
    response.raise_for_status()
    data = response.json()
    volumes = [v[1] for v in data["total_volumes"]]
    return volumes


def analyze_coin(coin_id, market_info):
    """Analyserer én mønt og returnerer signaler."""
    try:
        price = market_info["current_price"]
        change_24h = market_info.get("price_change_percentage_24h") or 0
        current_volume = market_info.get("total_volume") or 0

        # RSI kræver historiske priser
        time.sleep(1.5)  # skån CoinGecko's gratis rate-limit
        price_history = get_historical_prices(coin_id)
        rsi_series = calculate_rsi(price_history)
        rsi = rsi_series.iloc[-1] if not rsi_series.empty else 50

        # Volumen ift. 7-dages snit
        time.sleep(1.5)
        volumes = get_historical_volume(coin_id)
        avg_volume = sum(volumes[:-1]) / len(volumes[:-1]) if len(volumes) > 1 else current_volume
        volume_ratio = current_volume / avg_volume if avg_volume > 0 else 0

        # --- Alert-logik: kombination af signaler ---
        signals = []
        if abs(change_24h) >= PRICE_CHANGE_THRESHOLD:
            direction = "op" if change_24h > 0 else "ned"
            signals.append(f"Pris {direction} {abs(change_24h):.1f}% (24t)")

        if volume_ratio >= VOLUME_SPIKE_MULTIPLIER:
            signals.append(f"Volumen {volume_ratio:.1f}x normalt")

        if rsi >= RSI_OVERBOUGHT:
            signals.append(f"RSI overkøbt ({rsi:.0f})")
        elif rsi <= RSI_OVERSOLD:
            signals.append(f"RSI oversolgt ({rsi:.0f})")

        alert_level = "high" if len(signals) >= 2 else ("medium" if len(signals) == 1 else "none")

        return {
            "coin_id": coin_id,
            "symbol": market_info["symbol"].upper(),
            "name": market_info["name"],
            "price": price,
            "change_24h": change_24h,
            "volume_ratio": volume_ratio,
            "rsi": rsi,
            "signals": signals,
            "alert_level": alert_level,
            "error": None,
        }

    except Exception as e:
        return {"coin_id": coin_id, "symbol": coin_id.upper(), "error": str(e)}


# -----------------------------
# DASHBOARD (HTML)
# -----------------------------

def generate_html(results):
    timestamp = datetime.now().strftime("%d-%m-%Y %H:%M:%S")

    rows_html = ""
    order = {"high": 0, "medium": 1, "none": 2}
    results_sorted = sorted(results, key=lambda r: order.get(r.get("alert_level", "none"), 3))

    for r in results_sorted:
        if r.get("error"):
            rows_html += f"""
            <tr class="error-row">
                <td>{r['symbol']}</td>
                <td colspan="5">Fejl: {r['error']}</td>
            </tr>"""
            continue

        alert_class = {
            "high": "alert-high",
            "medium": "alert-medium",
            "none": "alert-none",
        }[r["alert_level"]]

        change_class = "positive" if r["change_24h"] >= 0 else "negative"
        signals_text = ", ".join(r["signals"]) if r["signals"] else "—"
        price_str = f"${r['price']:,.4f}" if r["price"] < 1 else f"${r['price']:,.2f}"

        rows_html += f"""
        <tr class="{alert_class}">
            <td class="coin"><span class="symbol">{r['symbol']}</span><span class="name">{r['name']}</span></td>
            <td>{price_str}</td>
            <td class="{change_class}">{r['change_24h']:+.2f}%</td>
            <td>{r['volume_ratio']:.1f}x</td>
            <td>{r['rsi']:.0f}</td>
            <td class="signals">{signals_text}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="da">
<head>
<meta charset="UTF-8">
<title>Crypto-overvågning</title>
<style>
    body {{
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        background: #0f1117;
        color: #e5e7eb;
        max-width: 900px;
        margin: 40px auto;
        padding: 0 20px;
    }}
    h1 {{ font-size: 22px; margin-bottom: 4px; }}
    .timestamp {{ color: #9ca3af; font-size: 13px; margin-bottom: 24px; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th {{
        text-align: left;
        padding: 10px 12px;
        border-bottom: 2px solid #374151;
        color: #9ca3af;
        font-size: 12px;
        text-transform: uppercase;
    }}
    td {{ padding: 12px; border-bottom: 1px solid #1f2937; font-size: 14px; }}
    .coin {{ display: flex; flex-direction: column; }}
    .symbol {{ font-weight: 600; }}
    .name {{ font-size: 12px; color: #9ca3af; }}
    .positive {{ color: #34d399; }}
    .negative {{ color: #f87171; }}
    .signals {{ font-size: 13px; color: #d1d5db; }}
    .alert-high {{ background: rgba(248, 113, 113, 0.08); border-left: 3px solid #f87171; }}
    .alert-medium {{ background: rgba(251, 191, 36, 0.06); border-left: 3px solid #fbbf24; }}
    .alert-none {{ border-left: 3px solid transparent; }}
    .error-row {{ color: #6b7280; font-style: italic; }}
    .legend {{ margin-top: 20px; font-size: 12px; color: #6b7280; }}
    .nav {{ margin-bottom: 20px; font-size: 13px; }}
    .nav a {{ color: #93c5fd; text-decoration: none; margin-right: 16px; }}
</style>
</head>
<body>
    <div class="nav"><a href="index.html">← Aktie-dashboard</a></div>
    <h1>🪙 Crypto-overvågning</h1>
    <div class="timestamp">Sidst opdateret: {timestamp}</div>
    <table>
        <tr>
            <th>Mønt</th>
            <th>Pris</th>
            <th>Ændring (24t)</th>
            <th>Volumen</th>
            <th>RSI</th>
            <th>Signaler</th>
        </tr>
        {rows_html}
    </table>
    <div class="legend">
        🔴 Høj alert (2+ signaler) · 🟡 Medium (1 signal) · Ingen kant = intet signal<br>
        Tærskler: prisændring ≥ {PRICE_CHANGE_THRESHOLD}% (24t) · volumen ≥ {VOLUME_SPIKE_MULTIPLIER}x snit · RSI ≥ {RSI_OVERBOUGHT} eller ≤ {RSI_OVERSOLD}<br>
        Data fra CoinGecko · Crypto handler 24/7, så "24t-ændring" er altid et rullende døgn, ikke en handelsdag
    </div>
</body>
</html>"""

    return html


def main():
    print(f"Henter markedsdata for {len(COINS)} mønter...")
    market_data = get_market_data(COINS)

    results = []
    for coin_id in COINS:
        info = market_data.get(coin_id)
        if not info:
            results.append({"coin_id": coin_id, "symbol": coin_id.upper(), "error": "Ikke fundet på CoinGecko"})
            continue
        print(f"  Analyserer {coin_id}...")
        results.append(analyze_coin(coin_id, info))

    html = generate_html(results)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Dashboard genereret: {os.path.abspath(OUTPUT_FILE)}")

    for r in results:
        if r.get("error"):
            print(f"  {r['symbol']}: FEJL - {r['error']}")
        elif r["alert_level"] != "none":
            print(f"  ⚠️  {r['symbol']}: {', '.join(r['signals'])}")


if __name__ == "__main__":
    main()
