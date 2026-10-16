"""CV employment grouping and deterministic editorial checks. Never infers a parent."""
from collections import Counter
from datetime import date


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
        if ep.get('episode_type') == 'employment':
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
    dated = sorted((e for e in episodes if e.get('episode_type') == 'employment' and e.get('start_date')),
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
