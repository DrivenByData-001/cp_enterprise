"""CV employment grouping and deterministic editorial checks. Never infers a parent."""
from collections import Counter
from datetime import date


CV_PIPELINE_VERSION = 3


def _date(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return date.min


def employment_timeline(episodes):
    """Every known employment, in stable reverse chronology; no inferred dates."""
    jobs = {str(e['id']): e for e in episodes
            if employment_id(e['id'], episodes) == str(e['id'])}
    def key(e):
        start = _date(e.get('start_date'))
        end = _date(e.get('end_date')) if e.get('end_date') else (date.max if start != date.min else date.min)
        return (-end.toordinal(), -start.toordinal(), str(e['id']))
    return sorted(jobs.values(), key=key)


def complete_cv_structure(content, episodes):
    """Complete an edited document without inventing content or omission reasons.

    Explicit user omissions survive. Historic project entries retain their explicit
    ancestry. Unknown/unlinked entries fail validation instead of being dropped.
    Reads of historic/adopted documents never call this function.
    """
    timeline = employment_timeline(episodes)
    jobs = {str(e['id']) for e in timeline}
    omitted = content.get('omitted_experience', [])
    omitted_ids = [o['episode_id'] for o in omitted]
    if len(omitted_ids) != len(set(omitted_ids)) or set(omitted_ids) - jobs:
        raise ValueError('Omissions must identify distinct known employments')
    if any(not o['reason'].strip() for o in omitted):
        raise ValueError('Give a specific reason for each deliberately omitted employment')
    grouped = {}
    seen = set()
    for entry in content.get('experience', []):
        eid = entry['episode_id']
        target = employment_id(eid, episodes)
        if target not in jobs or eid in seen or target in omitted_ids:
            raise ValueError('Experience must identify known employment or linked projects, without duplicates or conflicting omissions')
        seen.add(eid)
        grouped.setdefault(target, []).extend(entry.get('bullets', []))
    return {**content, 'experience': [dict(episode_id=str(e['id']), bullets=grouped.get(str(e['id']), []))
                                      for e in timeline if str(e['id']) not in omitted_ids]}


def employment_id(episode_id, episodes):
    """Follow explicit project ancestry, failing closed on missing parents/cycles."""
    by_id = {str(e['id']): e for e in episodes}
    current, seen = str(episode_id), set()
    while current in by_id and current not in seen:
        seen.add(current)
        episode = by_id[current]
        if episode.get('episode_type') == 'employment':
            return current
        parent = episode.get('parent_episode_id')
        if not parent:
            # Compatibility with older records without an episode_type.
            return current if not episode.get('episode_type') else None
        current = str(parent)
    return None


def review_warnings(content, episodes, claims):
    by_id = {str(e['id']): e for e in episodes}
    entries = {e['episode_id']: e for e in content.get('experience', [])}
    counts = Counter(employment_id(c.get('episode_id'), episodes) for c in claims)
    warnings = []
    for eid, ep in by_id.items():
        label = ep.get('organisation') or ep.get('title') or eid
        if employment_id(eid, episodes) == eid:
            if eid not in entries:
                warnings.append(f'{label}: employment omitted; review the stated reason before using this CV.')
            elif not entries[eid].get('bullets'):
                warnings.append(f'{label}: chronology only; no achievements were included.')
            elif counts[eid] >= 6 and len(entries[eid].get('bullets', [])) < 2:
                warnings.append(f'{label}: thin coverage despite substantial career evidence; review the selection.')
            if not ep.get('start_date'):
                warnings.append(f'{label}: employment dates need review; no dates have been inferred.')
        elif employment_id(eid, episodes) is None:
            warnings.append(f'{label}: no valid employment link; project achievements cannot be placed in the employment timeline.')
    dated = sorted((e for e in employment_timeline(episodes) if e.get('start_date')),
                   key=lambda e: str(e['start_date']))
    for previous, following in zip(dated, dated[1:]):
        if previous.get('end_date'):
            gap = date.fromisoformat(str(following['start_date'])[:10]) - date.fromisoformat(str(previous['end_date'])[:10])
            if gap.days > 120:
                warnings.append(f"Timeline gap between {previous.get('organisation')} and {following.get('organisation')}; confirm it is intentional.")
    texts = [b['text'].strip().casefold() for e in entries.values() for b in e.get('bullets', [])]
    texts += [s['text'].strip().casefold() for s in content.get('skills', [])]
    if any(n > 1 for n in Counter(texts).values()):
        warnings.append('Repeated wording across experience/skills; consolidate duplicated achievements.')
    if any(len(t.split()) > 45 for t in texts):
        warnings.append('Some bullets are unusually long; simplify technical detail and make the contribution clear.')
    if any('fellow' in s['text'].casefold() or 'qualification' in s['text'].casefold() for s in content.get('skills', [])):
        warnings.append('A skill appears to describe a qualification; use the professional qualifications section.')
    return list(dict.fromkeys(warnings))
