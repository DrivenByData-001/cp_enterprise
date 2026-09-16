"""Shared query-count harness for the "avoid N+1" assertions the build
treats as a design constraint rather than a nicety.

Counts every `psycopg.Cursor.execute` call inside the block, whichever
cursor issues it — so a helper that quietly opens its own `db_cursor()` is
counted too, which is exactly the case an N+1 regression usually hides in.

    with count_queries() as counted:
        do_the_thing()
    assert counted.count == expected

Deliberately a context manager rather than the `fn`-taking helper
test_vocabulary_graph.py already has locally: the assertions here compare
two runs of different sizes, which reads better with the work inline.
"""

from contextlib import contextmanager
from dataclasses import dataclass

import psycopg


@dataclass
class QueryCount:
    count: int = 0


@contextmanager
def count_queries():
    counted = QueryCount()
    original = psycopg.Cursor.execute

    def counting_execute(self, *args, **kwargs):
        counted.count += 1
        return original(self, *args, **kwargs)

    psycopg.Cursor.execute = counting_execute
    try:
        yield counted
    finally:
        psycopg.Cursor.execute = original
