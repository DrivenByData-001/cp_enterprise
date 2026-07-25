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
2. **Import** — paste `prompts/extract_job_posting.md` + a job URL (or the
   pasted page text, for sites that block fetching) into Claude/ChatGPT,
   paste the resulting JSON into the Import page (or drop multiple `.json`
   files at once to bulk-import a folder you've already captured).
3. **Dashboard** — roles ranked by similarity to your current profile,
   filterable by career track.
4. **Space** — a 3D PCA starfield of every captured role, target, and your
   profile, positioned by embedding similarity. Roles are colored by career
   track; your profile is a warm pulsing "sun"; targets are larger stars
   (white = real, violet = imagined, red = flagged as unreachable). Drag to
   rotate, scroll to zoom, click a star to open it.
5. **Targets** — a role you're navigating towards, real or imagined. Give
   `prompts/decompose_target_role.md` (plus any supporting material you've
   gathered — postings, profiles, articles) to Claude/ChatGPT, paste the
   resulting JSON into the Add Target page. Each target gets: typical tasks,
   a skill breakdown with concrete examples (what "people management" means
   *in this specific role*), technical subjects worth studying, an honest
   feasibility note, and — for imagined roles — which real roles it was
   grounded in. The target's detail page also shows a path: your current
   alignment percentage plus the closest captured postings, ranked as
   stepping stones toward it.
6. **Editing** — any posting or target can be edited from its detail page.
   This reuses the same paste-JSON flow as import/add: go back to the AI
   with more material, get a fresher/fuller JSON, and paste it in. It's a
   full overwrite of that entry's fields (and re-embeds it), not a merge.

## Schema (3 tables)

- `job_roles` — postings *and* targets share this table, distinguished by
  `node_type` (`posting` / `target_real` / `target_imagined`). Postings use
  the original decomposed fields + AI analysis scores; targets additionally
  use `typical_tasks`, `skill_decomposition`, `technical_subjects`,
  `grounding_note`, `feasibility_note`, `is_plausible`. Both carry the full
  raw JSON payload + embedding.
- `job_role_skills` — one row per skill mention (name, category, importance,
  requirement_type), FK to `job_roles`. Applies to postings and targets alike.
- `profile_snapshots` — your narrative text + embedding, timestamped and
  versioned (`is_current` flag marks the active one).

## Deliberately out of scope for now

These are real parts of the bigger idea, deferred until what's built earns
its keep:

- `career_history` as discrete past roles + trajectory vector math (the
  Space view shows where you are, not the path you walked to get there)
- A real skill graph (explicit relationships between skills, e.g. "Python"
  relates to "data science") + gap analysis against your own stated skill
  set — today, alignment is profile-narrative-to-role embedding similarity,
  not a skill-by-skill diff
- `salary_benchmarks` market survey data
- Actuarial exam tracking
- Entrepreneurial "opportunity nodes" (though `node_type` is designed to
  extend to a third kind later without a schema break)
- Any AI chat / MCP interface — decomposition and extraction both stay a
  manual copy-paste loop with Claude/ChatGPT, on purpose
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
