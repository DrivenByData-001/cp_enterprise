#!/usr/bin/env python3
"""Analyze cluster quality and merges from production bootstrap."""
import sys
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.db import db_cursor
from app.concept_linking import normalize_name
from app.vocabulary_bootstrap import cluster_key_for

with db_cursor() as cur:
    # Get unresolved observations
    cur.execute("SELECT id, surface_form FROM jobber.role_skill_observation WHERE canonical_concept_id IS NULL")
    unresolved = cur.fetchall()
    
    # Group by normalized form
    surface_forms = defaultdict(list)
    for row in unresolved:
        normalized = normalize_name(row["surface_form"])
        if normalized:
            surface_forms[normalized].append(row["surface_form"])
    
    # Cluster them
    clusters = defaultdict(lambda: defaultdict(int))  # cluster_key -> surface_form -> count
    for normalized, originals in surface_forms.items():
        key = cluster_key_for(normalized)
        for orig in originals:
            clusters[key][orig] += 1
    
    print(f"Total unresolved observations: {len(unresolved)}")
    print(f"Distinct normalized surface forms: {len(surface_forms)}")
    print(f"Distinct cluster keys: {len(clusters)}")
    print(f"Total merges: {sum(1 for key in clusters if len(clusters[key]) > 1)}")
    
    print(f"\n=== Merges (clusters with 2+ forms) ===")
    merges = []
    for key in sorted(clusters.keys()):
        forms = clusters[key]
        if len(forms) > 1:
            total_occurrences = sum(forms.values())
            merges.append((key, forms, total_occurrences))
    
    print(f"Total merges: {len(merges)}\n")
    for key, forms, total_occ in sorted(merges, key=lambda x: -x[2])[:50]:
        forms_list = [f'"{f}" ({forms[f]} occ)' for f in sorted(forms.keys())]
        print(f"  [{total_occ:3d} total] {key}")
        for form in forms_list:
            print(f"      -> {form}")
    
    # Frequency stats
    print(f"\n=== Cluster size distribution ===")
    cluster_sizes = defaultdict(int)
    for key in clusters.keys():
        cluster_sizes[len(clusters[key])] += 1
    for size in sorted(cluster_sizes.keys()):
        print(f"  {size} forms/cluster: {cluster_sizes[size]:4d} clusters")
    
    # High-frequency terms analysis
    print(f"\n=== Top 30 highest-frequency unique surface forms ===")
    term_freq = defaultdict(int)
    for row in unresolved:
        term_freq[row["surface_form"]] += 1
    
    for term, count in sorted(term_freq.items(), key=lambda x: -x[1])[:30]:
        cluster_key = cluster_key_for(normalize_name(term))
        print(f"  [{count:3d} observations] {term:60s} -> cluster: {cluster_key}")
