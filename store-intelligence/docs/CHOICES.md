# CHOICES.md — Three engineering decisions

Each decision lists the options I considered, what the AI assistant suggested,
and what I actually chose and why. The reasoning is anchored to *this* dataset
(face-blurred 1080p/15fps retail CCTV, 5 stores, ~1 hour each) and to the North
Star (conversion-rate accuracy and usefulness) — not generic best practice.

---

## Decision 1 — Detection & Re-ID model

**Options considered**
1. YOLOv8 (person class) + ByteTrack + OSNet appearance re-ID.
2. YOLOv8 + ByteTrack + **trajectory & spatio-temporal gating** re-ID (chosen).
3. RT-DETR + DeepSORT.
4. A VLM doing end-to-end "count the people" per frame.

**What the AI suggested:** the assistant recommended option (1) — YOLOv8 plus an
OSNet/torchreid appearance embedding — as the "standard high-accuracy" stack,
and floated option (4) as a quick prototype.

**What I chose and why:** **YOLOv8n/s for detection + ByteTrack for tracking + a
custom trajectory/spatio-temporal re-ID layer**, with appearance only as a weak
tie-breaker.

- **Detection:** YOLOv8 is a defensible default — well-pretrained on COCO
  `person`, robust across the natural/fluorescent/mixed lighting in the clips,
  and fast enough for 15fps. I keep a low confidence floor and *emit* low-conf
  detections (the API decides what to trust), which is what the brief asks for.
- **Why I rejected appearance re-ID (the override):** the footage has
  **full-face blur** and **masked branding**, and shoppers wear visually similar
  clothing under changing light. OSNet embeddings lean on exactly the appearance
  signal that is degraded here, while adding a heavy model and GPU dependency.
  For the two jobs that actually move the conversion metric — *not* double-
  counting re-entries and *not* double-counting the entry∩floor camera overlap —
  **time, door geometry, and direction** are stronger, cheaper signals than a
  fragile embedding.
- **Why I rejected the VLM-per-frame counter:** at 15fps × 20min × 3 cams × 5
  stores it is ~810k frames — VLM cost and latency are prohibitive, and it gives
  no stable `visitor_id` for tracking. I *do* use a VLM, but surgically (see
  staff classification below), not as the counting engine.
- **Honest failure mode (ready for the follow-up video):** trajectory gating
  breaks when one customer exits and a *different* customer enters from the same
  direction within ~3 seconds — the gate may stitch them into one id. I mitigate
  with a direction + minimum-gap rule and flag low-confidence stitches rather
  than asserting certainty.

**VLM for staff classification — prompt & evaluation.** Staff must be excluded
from customer metrics. I crop each track's median frame and ask a vision model:
*"This is a cropped image from retail store CCTV (faces blurred). Is this person
a STAFF member (store uniform / lanyard / behind a counter) or a CUSTOMER?
Answer with a JSON object {\"role\": \"STAFF\"|\"CUSTOMER\", \"confidence\":
0-1, \"reason\": \"...\"}."* I evaluate it against the uniform cues visible in
the clips and **fall back to a uniform-colour histogram heuristic** when the
VLM confidence is low or the crop is occluded. Where the VLM disagrees with the
heuristic at high confidence, I trust the VLM; otherwise the cheap heuristic
wins. This keeps cost bounded (one call per track, not per frame) and is honest
about when it is guessing.

---

## Decision 2 — Event schema design

**Options considered**
1. A thin schema (just `entry`/`exit` counts).
2. The **rich per-event behavioural schema** the brief specifies, stored as an
   immutable log (chosen).
3. Pre-aggregated rows (one row per store per minute).

**What the AI suggested:** the assistant initially proposed pre-aggregating into
per-minute rollups "for query speed."

**What I chose and why:** the **full behavioural event** as the atomic record,
appended to an immutable log, with metrics derived as projections.

- Pre-aggregation (option 3) was rejected because it **destroys the session**.
  Conversion, funnel and re-entry de-duplication all require reconstructing an
  individual visitor's ordered journey (`session_seq`), which a per-minute
  rollup cannot reproduce. Aggregates can always be rebuilt from events; events
  can never be rebuilt from aggregates.
- I kept `metadata` **open/extensible** (`extra: allow`) so heterogeneous
  producers and future event fields don't break ingestion — forward
  compatibility matters when the detector evolves.
- I made `confidence` a required, un-suppressed field so uncertainty flows from
  pixels to the API edge.
- I treat `visitor_id` as *one physical person within a clip*: assigned at
  `ENTRY`, **reused** on `REENTRY`. This single rule is what lets the funnel
  count a returning shopper once — the explicit anti-inflation requirement.

---

## Decision 3 — API architecture: read-time projections + Postgres-with-SQLite-tests

**Options considered**
1. Mutating counters on write (fast reads, fragile correctness).
2. **Read-time projections over the event log** (chosen).
3. A streaming engine (Kafka + materialised views) up front.

**What the AI suggested:** the assistant suggested maintaining incremental
counters on ingest for O(1) reads, and separately suggested introducing Kafka
early "because the brief mentions an event stream."

**What I chose and why:** **read-time projections** backed by Postgres in the
shipped stack, with the repository abstracted so **tests run on SQLite**.

- Write-time counters (option 1) couple correctness to ingest order and make
  idempotency and reprocessing hard — a duplicate or a late event corrupts the
  counter. Projections recompute from the immutable truth, so idempotent replay
  and detector re-runs are safe by construction.
- Kafka up front (option 3) is premature for a 1-hour, 5-store dataset; it would
  add operational weight without improving any graded number. I document it as
  the **next** step for 40 live stores rather than building it now — and the
  event-sourced design means dropping Kafka in front of `ingest` later is
  additive, not a rewrite.
- **The override worth noting:** I declined the early-Kafka suggestion on
  altitude grounds (solve today's problem well; leave a clean seam for scale),
  and I declined write-time counters on correctness grounds. Postgres gives the
  real "database unavailable → 503" demonstration the brief wants; the SQLite
  test path keeps the >70% coverage gate hermetic and fast.

**What would change my mind (scale):** at 40 stores streaming continuously,
read-time recomputation of multi-day baselines (the 7-day conversion anomaly)
becomes the first bottleneck. At that point I would precompute daily conversion
rollups *as a projection of the same log* and keep "today" on the live path —
without abandoning event sourcing.
