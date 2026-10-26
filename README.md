# Territory Optimizer

[![CI](https://github.com/vrutikadani/territory-optimizer/actions/workflows/ci.yml/badge.svg)](https://github.com/vrutikadani/territory-optimizer/actions/workflows/ci.yml) ![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)

A toolkit for designing sales territories and routing leads: geocoding that survives failures, a naming convention that survives staffing changes, a balance check that suggests fair state moves, and lead-routing rules written as readable code.

> **About this repo.** I redesigned US sales territories and lead routing for a medical device company, using territory-planning software, the Google Maps API and CRM workflows. The production work covered **24 field reps and 12 account managers** and **$7.6M+ in year-to-date territory revenue**. This repo rebuilds the core logic with **synthetic accounts and reps**. No customer, rep or territory data is included.

## The business problem

- Territory labels had rep names baked in ("Temp JORDAN"), so every hire or exit broke reports and workflows.
- Some reps carried far bigger books than others.
- A geocoding job failed halfway through the account list. It had no cache and no checkpoint, so a restart meant paying for every address again, and partial results overwrote good data.
- Leads were routed by rules that lived in people's heads, and manual handoffs were overwritten by automation.

## What's inside

```mermaid
flowchart TB
    subgraph Geocode
      A[Account address] --> N[Normalize]
      N --> C{In cache?}
      C -->|yes| R[Coordinates]
      C -->|no| G[Google API<br/>retry + backoff]
      G -->|fail| Z[ZIP centroid fallback]
      G --> R
      Z --> R
      R --> K[Checkpoint every N rows<br/>atomic write]
    end
    subgraph Design
      R --> B[Books per territory]
      B --> M[Suggest neighbor-state moves<br/>to cut revenue spread]
      M --> L[Name: ID + Name<br/>geography only]
    end
    subgraph Route
      L --> RT[Routing rules 1-6]
    end
```

| Module | What it does |
|---|---|
| `territory/geocode.py` | Cache, retry with backoff, checkpoints, atomic writes, ZIP fallback, never overwrite good data |
| `territory/naming.py` | Builds `ID` ("MS, AL, Memphis, Atlanta") and `Name` ("Mississippi, Alabama, Memphis, Atlanta"); lints old labels for rep names, "temp" and airport codes |
| `territory/balance.py` | Revenue and account counts per territory; greedy neighbor-state moves that shrink the max/min spread |
| `territory/routing.py` | Six routing rules, including "a manual handoff is never overwritten" and "the company follows its contacts" |

## Routing rules

1. A lead a rep handed over by hand is never overwritten.
2. Non-medical specialty MQLs go to inside sales.
3. Med spas with no medical director go to inside sales.
4. Medical specialties and all SQLs / opportunities go to the outside rep who covers that ZIP.
5. No outside coverage in that area: inside sales.
6. The company owner follows its contacts.

## Run it

```bash
pip install pytest
python run_demo.py       # geocoding, naming, balance and routing on synthetic data
python -m pytest -q      # 13 tests
```

Sample balance output:

```
Balance - spread before: 1.75x
   move TN Southeast -> South Central: spread 1.75x -> 1.55x
   move WA Pacific -> Mountain: spread 1.55x -> 1.29x
   move AL Southeast -> South Central: spread 1.29x -> 1.2x
```

Live geocoding (optional): set `GOOGLE_MAPS_KEY` and use `GoogleProvider` with `CachedGeocoder`.

## Lessons from the geocoding failure

- **Cache first.** An address should cost one API call, ever.
- **Checkpoint.** A crash should lose minutes, not the whole run.
- **Never overwrite good data with a failure.**
- **Say how precise a point is.** A ZIP centroid is fine for territory design, not for drive-time routing.

---
Built by [Vrutik Adani](https://www.linkedin.com/in/vrutikadani) · Technical Product Manager, GTM Systems & AI Engineering
