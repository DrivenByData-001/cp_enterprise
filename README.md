# Career Navigator

A personal career navigation tool. Treats the job market as a multidimensional
space: your career narrative and captured job postings are embedded into the
same vector space, so "closest roles to me" and "gap to a target role" become
similarity computations, not guesswork.

This is v1 — a thin, working slice: **capture → decompose → embed → compare**.
Local-only, no cloud, no API keys required.

## How it fits together

- **Extraction is manual, on purpose.** You paste `prompts/extract_job_posting.md`
  plus a job posting URL into Claude or ChatGPT. It fetches the page, extracts
  structured data, and flags anything it couldn't get cleanly
  (`extraction_status` / `notes_for_user`) so you know when to grab it by hand
  instead.
- **Embedding is automatic and local.** No API key needed — the backend embeds
  text with a small local model (`BAAI/bge-small-en-v1.5` via `fastembed`,
  downloaded once on first use, runs on CPU).
- **Everything else is mechanical**: import the JSON, see it ranked by
  similarity to your profile, see it on a 2D map.

## Stack

- **Backend**: Python (FastAPI) + SQLite (`backend/data/career_nav.db`, gitignored)
- **Frontend**: React + Vite
- **Embeddings**: local, via `fastembed` (no external API)

## Running it

```bash
# backend
cd backend
pip install -r requirements.txt
python3 -m uvicorn app.main:app --reload --port 8000

# frontend (separate terminal)
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. The first import will download the embedding
model (~130MB, one-time, needs internet access to huggingface.co).

## Using it

1. **Profile** — write a short narrative of where you are right now. Save it;
   it gets embedded and re-embedded every time you update it (each save keeps
   a timestamped snapshot).
2. **Import** — paste `prompts/extract_job_posting.md` + a job URL into
   Claude/ChatGPT, paste the resulting JSON into the Import page (or drop
   multiple `.json` files at once to bulk-import a folder you've already
   captured).
3. **Dashboard** — roles ranked by similarity to your current profile,
   filterable by career track.
4. **Space** — a 2D PCA projection of the same embeddings: roles as dots,
   you as a diamond marker. Click a dot to open its detail page.

## Schema (3 tables)

- `job_roles` — decomposed posting fields + AI analysis scores + full raw
  JSON payload + embedding.
- `job_role_skills` — one row per skill mention (name, category, importance,
  requirement_type), FK to `job_roles`.
- `profile_snapshots` — your narrative text + embedding, timestamped and
  versioned (`is_current` flag marks the active one).

## Deliberately out of scope for v1

These are real parts of the bigger idea, deferred until the thin slice earns
its keep:

- `career_history` as discrete past roles + trajectory vector math
- A real skill graph (relationships between skills) + gap analysis
- `salary_benchmarks` market survey data
- Actuarial exam tracking
- Entrepreneurial "opportunity nodes"
- Any AI chat / MCP interface
- Any cloud sync

### Note for future devs: time-evolution view (deferred)

`job_roles.posting_date` and `.captured_at` are already tracked and shown
everywhere, and historic postings (paste-from-file, no URL) are a first-class
extraction path — see `prompts/extract_job_posting.md`. What's *not* built yet
is doing anything with that time dimension on the **Space** view.

The intended design, discussed but deliberately deferred: a date-range filter
(slider) under the scatter in `frontend/src/pages/Space.tsx`, defaulting to
all-time, that re-requests `GET /api/space` scoped to that window so the PCA
projection recomputes over only the roles active in it — letting you scrub
from oldest to newest captures and watch the shape of the space shift. Backend
side, `backend/app/routes/space.py` would need optional `from`/`to` query
params filtering the `job_roles` selected before the PCA fit. Held off until
there's enough historic data loaded in to actually make the view meaningful.
