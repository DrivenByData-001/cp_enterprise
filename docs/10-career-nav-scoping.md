# 10 — Career Navigation System — Scoping Session

## Purpose of this document
Take this to a fresh Claude window. The goal of that session is to fully design the career navigation system — schema, AI functions, MCP tools, UI concepts — and produce a confirmed plan. Do not build anything yet. Return the confirmed plan to the original session for a build handoff doc.

## What this system is
A personal career navigation tool that treats the job market as a multidimensional space. The user (Ranga) is located at a point in that space defined by their skills, experience, and track record. Job roles — current postings, historic postings, and aspirational roles — define the shape of the space. The system helps answer:
- Where am I right now in this space?
- What roles are closest to my current position?
- What moves are realistic vs aspirational?
- What is my trajectory based on where I have been?
- What gaps exist between me and a target role?
- What roles are emerging or declining over time?

This is not a job application tracker. It is a strategic career orientation tool.

## Context — existing infrastructure
This system sits inside an existing personal knowledge base built on Supabase. Do not redesign the foundation. Everything new integrates with what exists.

### Existing Supabase project
- Project ref: `hiupemksetjckfpdquxb`
- URL: `https://hiupemksetjckfpdquxb.supabase.co`
- pgvector is already enabled (vector(1536) dimensions, openai/text-embedding-3-small via OpenRouter)
- Existing tables: `thoughts`, `people`, `projects`, `areas`, `goals`, `decisions`, `media`, `conversations`, `messages`, `participants`, `items`, `time_rules`, `time_blocks`, `events`, `milestones`
- MCP server: `open-brain-mcp` Edge Function, already has tools: `search_thoughts`, `list_thoughts`, `capture_thought`, `thought_stats`, `get_person_briefing`, `get_project_chat_summary`
- New MCP tools plug into the same Edge Function

### Existing relevant tables to link to
- `people` — for contacts at companies, recruiters, referrals
- `goals` — for career goals already captured
- `thoughts` — for career-related observations already captured
- `items` — task manager items, learning goals can reference career targets

## User context
- Name: Ranga
- Background: actuarial / quantitative finance
- Has databases of job postings accumulated over years, some current, some historic
- Wants to track actuarial and adjacent roles (data science, quant, risk, finance)
- Salary data may or may not be attached to postings — should be accommodated but not required
- Will augment job posting data with general salary survey data separately
- Has personal financial goals captured in the goals/thoughts tables
- May extend the space beyond job roles to entrepreneurial opportunities in future

## Key design requirements

### 1. Vector embeddings for semantic positioning
Both the user's career narrative AND job role descriptions must be embedded using the same model (openai/text-embedding-3-small, 1536 dimensions). This enables cosine similarity between "where I am" and "where a role is" — not metaphorically but mathematically.

### 2. Time as a first-class dimension
- Job postings have a `posting_date` — historic postings inform the shape of the space but should be weighted by recency
- The user's own career history has dates — the sequence of roles defines a trajectory vector
- The system should be able to answer "given my direction of travel, what roles are a natural continuation vs a deliberate pivot?"

### 3. Skill graph
- Skills exist as discrete nodes
- Skills link to job roles (required/preferred) and to the user's history (demonstrated/developing)
- Skills have relationships to each other (e.g. Python is related to data science, stochastic calculus is related to actuarial)
- Gap analysis compares user's skill set against a target role's required skills

### 4. Salary data
- Optional field on job roles
- Separate `salary_benchmarks` table for survey data (role title, market, percentiles, year)
- User's own financial goals/targets stored in existing `goals` table

### 5. Actuarial specifics
- Actuarial roles often have exam progression requirements (CT/CM/CS/CP series or equivalent)
- Exam status should be trackable
- This connects to the knowledge mapping side project (separate) but exam metadata belongs here

### 6. MCP tools the AI needs
At minimum:
- `get_career_briefing` — user's current position, trajectory, closest roles
- `get_role_match` — similarity score + gap analysis between user and a specific role
- `find_similar_roles` — roles closest to user's current position or to a target role
- `get_skill_gaps` — what skills does the user need for a given role or direction
- `get_trajectory` — what direction has the user been moving, what does continuation look like

### 7. UI concept
- Career space visualisation (2D projection of embedding space — roles as dots, user as a distinct marker)
- Role detail panel (requirements, salary, similarity score, gap analysis)
- Trajectory view (career history as a path through the space)
- Skill gap panel (for a selected target role)

## Questions to resolve in the planning session

1. **Schema design** — what tables are needed, what are the exact columns, how do skills link to roles and history
2. **Embedding strategy** — what text gets embedded for a job role (title only? full description? requirements?), what text gets embedded for the user (career summary? each role separately? combined?)
3. **Trajectory calculation** — how is the trajectory vector computed from career history? weighted average of role embeddings? explicit direction vector?
4. **Recency weighting** — how are historic postings weighted vs current ones in similarity searches
5. **Skill graph implementation** — separate skills table with linking tables, or jsonb arrays on roles/history (simpler but less queryable)
6. **Actuarial exam tracking** — separate table or metadata on career_history
7. **Import pipeline** — user has existing job posting databases. What format should they be exported to for ingestion? What does the import script look like?
8. **UI implementation** — the 2D projection requires dimensionality reduction (PCA or UMAP). Where does this run — in the browser, in a Python script, or in an Edge Function?
9. **Entrepreneurial extension** — how to accommodate non-job opportunity nodes in the space later without breaking the schema now

## Proposed starting schema (to validate and refine in planning session)

### career_history
The user's own work history. Each role gets embedded.
```
id, title, organisation, start_date, end_date (nullable = current),
description, achievements, embedding vector(1536),
created_at
```

### job_roles
All collected job postings and reference roles.
```
id, title, organisation, posting_date, source,
description, requirements, salary_min, salary_max, currency,
location, remote_ok, role_type (actuarial/data_science/quant/risk/other),
embedding vector(1536),
is_active boolean,
created_at
```

### skills
Discrete skill nodes.
```
id, name, category (technical/domain/soft),
description, related_skills text[] (names of related skills),
created_at
```

### career_history_skills
Links skills to user's career history entries.
```
career_history_id, skill_id,
proficiency (1-5),
demonstrated_in text (brief evidence)
```

### job_role_skills
Links skills to job roles.
```
job_role_id, skill_id,
requirement_type (required/preferred),
importance (1-5)
```

### salary_benchmarks
Market salary survey data.
```
id, role_title, market (geography/sector),
year, p25, p50, p75, p90, currency,
source, created_at
```

### actuarial_exams
Exam progression tracking.
```
id, exam_code, exam_name, series (CT/CM/CS/CP/IFoA/SOA/CAS),
status (not_started/in_progress/passed/exempted),
passed_date, attempt_count, notes,
created_at
```

## Output expected from planning session
Return a confirmed version of:
1. Final schema (all tables, all columns, all relationships)
2. Embedding strategy (what text, how combined)
3. Trajectory calculation approach
4. Import pipeline format for job postings
5. List of MCP tools with input/output spec
6. UI plan (views, what data each view needs)
7. Any schema changes needed to existing tables

Once confirmed, a build handoff doc will be generated in the same format as docs 01-09.

## Notes
- The knowledge mapping side project (asat to sat) is separate. When complete it adds `knowledge_concepts` table. It connects here at the skill gap analysis level — user's actual retained knowledge vs stated skills.
- Entrepreneurial opportunity nodes are future scope. Design the schema to accommodate them without breaking changes — likely via a `node_type` field on `job_roles` or a separate `opportunity_nodes` table.
- All embedding calls use OpenRouter with model `openai/text-embedding-3-small` (1536 dimensions). Do not change the model without rebuilding all existing embeddings.
- The 2D projection for UI visualisation is a separate computation step, not stored in the DB. Likely a Python script using sklearn PCA or umap-learn, outputs x/y coordinates stored temporarily or computed on demand.
