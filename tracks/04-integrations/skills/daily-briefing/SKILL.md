---
name: daily-briefing
description: "Daily financial and technology briefing. Use when the user asks for a morning briefing, market update, daily summary, or what happened today. Reads CoinGecko, Frankfurter, Hacker News, Open-Meteo, and Nager.Date. NOT for real-time trading, investment advice, historical analysis, or financial modeling."
metadata: { "openclaw": { "emoji": "📊", "requires": { "bins": ["curl"] } } }
---
<!--
SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Daily Financial & Technology Briefing

## When to Use

- "Give me today's morning briefing"
- "What's happening in the markets?"
- "Daily update please"
- "Brief me"

## Data Sources

Call these sources in order. Continue to the next source if one fails.

### 1. Crypto Prices (CoinGecko)

```bash
curl -fsS --max-time 15 'https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,solana&vs_currencies=usd&include_24hr_change=true&include_market_cap=true'
```

Require a USD price for Bitcoin, Ethereum, and Solana. Treat missing or
non-object asset entries as unusable data.

### 2. Exchange Rates (Frankfurter)

```bash
curl -fsSL --max-time 15 'https://api.frankfurter.dev/v1/latest?from=USD&to=EUR,GBP,JPY,CNY'
```

Require the response `rates` object to include EUR, GBP, JPY, and CNY.

### 3. Top Technology Stories (Hacker News)

```bash
curl -fsS --max-time 15 'https://hacker-news.firebaseio.com/v0/topstories.json'
curl -fsS --max-time 15 'https://hacker-news.firebaseio.com/v0/item/{ID}.json'
```

Fetch at least five integer story IDs, then select stories relevant to AI,
infrastructure, or developer tools. Require a title before including a story.

### 4. Weather (Open-Meteo)

```bash
curl -fsS --max-time 15 'https://api.open-meteo.com/v1/forecast?latitude=37.39&longitude=-122.08&current=temperature_2m,wind_speed_10m,relative_humidity_2m&temperature_unit=fahrenheit&forecast_days=1'
```

The default location is Santa Clara, California. Require a `current` object with
`temperature_2m` before reporting current conditions.

### 5. Upcoming Holidays (Nager.Date)

```bash
curl -fsS --max-time 15 'https://date.nager.at/api/v3/NextPublicHolidays/US'
```

Require at least one object containing non-empty `date` and `name` fields. Show
the next two usable entries.

## Briefing Format

```text
## Market Snapshot
- BTC: $XX,XXX (▲/▼ X.X%)
- ETH: $X,XXX (▲/▼ X.X%)
- SOL: $XXX (▲/▼ X.X%)
- USD/EUR: X.XXXX | USD/GBP: X.XXXX | USD/JPY: XXX.XX

## Tech Pulse
1. Story title (score: XXX) — one-sentence summary

## Weather (Santa Clara)
- Temperature: XX°F | Wind: X km/h | Humidity: XX%

## Upcoming
- Next holiday: Name (Date)

## Recommendations
1. Actionable item based on available market data
2. Actionable item based on available technology news
3. Actionable item based on available calendar data
```

## Error Handling

If an API fails or returns the wrong shape, identify the unavailable section and
continue. Never fabricate values and never turn incomplete data into investment
advice.
