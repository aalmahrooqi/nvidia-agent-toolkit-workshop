---
name: nasa-apod
description: "NASA Astronomy Picture of the Day. Use when the user asks about space, astronomy, NASA photos, today's space image, or an APOD from a specific date. Uses NASA's public DEMO_KEY inside the agent workspace. NOT for live telescope feeds, mission-control data, launch schedules, or real-time ISS tracking."
metadata: { "openclaw": { "emoji": "🔭", "requires": { "bins": ["curl"] } } }
---
<!--
SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# NASA Astronomy Picture of the Day

## When to Use

- "What's today's astronomy picture?"
- "Show me a NASA space photo"
- "What was the APOD on my birthday?"
- "Show me the NASA APOD from Christmas 2025"

## API Endpoint

Base: `https://api.nasa.gov/planetary/apod`

This deployed skill uses NASA's public, rate-limited `DEMO_KEY`. A key stored in
the notebook host's `.env` is not automatically available inside the agent
workspace. An operator can configure separate, supported credential injection
outside this lesson when higher limits are required.

Some environments route this API more reliably over IPv4, so these bounded
examples use `curl -4` and a 20-second transfer timeout.

### Today's Picture

```bash
curl -4 -fsS --max-time 20 'https://api.nasa.gov/planetary/apod?api_key=DEMO_KEY'
```

### Specific Date

```bash
curl -4 -fsS --max-time 20 'https://api.nasa.gov/planetary/apod?api_key=DEMO_KEY&date=2025-12-25'
```

### Date Range

```bash
curl -4 -fsS --max-time 20 'https://api.nasa.gov/planetary/apod?api_key=DEMO_KEY&start_date=2025-12-20&end_date=2025-12-25'
```

## Response Fields

| Field | Description |
|-------|-------------|
| `title` | Image title |
| `date` | Publication date (`YYYY-MM-DD`) |
| `explanation` | Detailed description |
| `url` | Standard-resolution media URL |
| `hdurl` | High-definition image URL when available |
| `media_type` | `image` or `video` |
| `copyright` | Photographer credit when available |

## Presenting Results

Always include:

1. The title and date.
2. A 2-3 sentence summary of the explanation.
3. The HTTPS media URL and the HD URL when one is present.
4. Copyright credit when present.

For video entries, identify the media as video and provide its HTTPS URL. Never
invent an `hdurl` for a video response.

## Error Handling and Rate Limits

Reject error documents and responses that lack the required APOD fields. Do not
fabricate missing data. `DEMO_KEY` is intentionally rate-limited; if NASA returns
a rate-limit error, explain the limitation and suggest trying again later.
