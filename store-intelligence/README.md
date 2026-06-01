# Apex Store Intelligence

A **web-analytics engine for physical retail.** It turns raw CCTV footage into
the same kind of real-time funnel, conversion, and engagement metrics that Apex
already has for its online channel — closing the offline data blind spot.

```
Raw CCTV clips ─▶ Detection (pipeline/) ─▶ Events (JSON) ─▶ Intelligence API (app/) ─▶ Live Dashboard
```

The **North Star** is the *Offline Store Conversion Rate*. Every component either
makes that number more **accurate** (detection) or more **useful** (API).

---

## Quickstart (5 commands)

```bash
# 1. Clone
git clone <your-private-repo-url> && cd store-intelligence

# 2. Bring up the API + Postgres (no manual steps beyond this)
docker compose up --build

# 3. Read live metrics for the canonical store (valid JSON even with no data yet)
curl localhost:8000/stores/STORE_BLR_002/metrics

# 4. Run the end-to-end LIVE DEMO — synthesises a store, streams events at
#    simulated real time, loads POS. (stdlib only; needs python 3.9+)
python pipeline/replay.py --synth --store STORE_BLR_002 --speed 60

# 5. Watch it update live in the browser
open http://localhost:8000/dashboard      # macOS;  Windows: start  / Linux: xdg-open
```

The API is live at **http://localhost:8000** (interactive docs at `/docs`); the
**live dashboard** is at **http://localhost:8000/dashboard**.

> The demo in step 4 needs no dataset and no GPU — it proves the full
> pipeline → API → dashboard loop. To run the *real* detector against CCTV, see
> the next section.

---

## Running the detection pipeline against the clips

The dataset (clips, `store_layout.json`, `pos_transactions.csv`) is **not** in
this repo. Place it under `./data/` (git-ignored):

```
data/
├── clips/STORE_BLR_002/CAM_ENTRY_01.mp4  ...
├── store_layout.json
└── pos_transactions.csv
```

Install the heavier detection deps (separate from the API; use Python 3.11/3.12):

```bash
pip install -r pipeline/requirements.txt        # YOLOv8 + OpenCV + (optional) VLM
export ANTHROPIC_API_KEY=...                     # optional: enables VLM staff classification
```

Then run the detector, which writes newline-delimited events, self-grades, and
(optionally) streams straight into the running API:

```bash
# Process every store's clips → events.jsonl, then calibrate vs ground truth
bash pipeline/run.sh ./data ./out/events.jsonl http://localhost:8000

# Or replay a saved events file into the API at simulated real time (Part E)
python pipeline/replay.py --events ./out/events.jsonl --pos ./out/pos.json --speed 60
```

Output location: `./out/events.jsonl` (one event per line, matching the schema
in `app/models.py`). Self-evaluation: `python pipeline/calibrate.py --events
./out/events.jsonl --ground-truth ./data/sample_events.jsonl`. See
[docs/DESIGN.md](docs/DESIGN.md) for the full pipeline.

---

## API surface

| Endpoint | Purpose |
|---|---|
| `POST /events/ingest` | Batch ingest (≤500), idempotent by `event_id`, partial success |
| `GET /stores/{id}/metrics` | Unique visitors, conversion rate (±CI), dwell, queue, abandonment |
| `GET /stores/{id}/funnel` | Entry → Zone → Billing → Purchase, session-deduped |
| `GET /stores/{id}/heatmap` | Per-zone visit + dwell, normalised 0–100 |
| `GET /stores/{id}/anomalies` | Queue spike, conversion drop, dead zone + suggested actions |
| `GET /health` | DB status, per-store feed freshness, `STALE_FEED` |

## Live dashboard (Part E)

```bash
# After `docker compose up`, open:
http://localhost:8000/dashboard
```

## Configuration

The app runs with sensible defaults and **needs no env setup**. Every tunable
(storage URL, conversion window, anomaly thresholds, optional VLM key) is
documented in [.env.example](.env.example) and read by
[app/config.py](app/config.py). `docker-compose.yml` wires the Postgres URL and
POS path itself.

## Tests

```bash
pip install -r requirements.txt
pytest            # statement coverage > 70%, edge cases included
```

See [docs/DESIGN.md](docs/DESIGN.md) and [docs/CHOICES.md](docs/CHOICES.md).
