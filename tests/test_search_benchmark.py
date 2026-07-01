"""Benchmarks the optimized search/es_client.py design (custom analyzer,
multi_match, function_score) against the original naive design kept in
search/benchmark_baseline.py (single text field, standard analyzer, match,
hard sort-by-date). Not part of the app's normal test suite — skipped if
Elasticsearch isn't reachable.

Run with: pytest tests/test_search_benchmark.py -s
"""
import random
import time

import pytest
from elasticsearch import Elasticsearch
from elasticsearch.exceptions import ConnectionError as ESConnectionError

from search.benchmark_baseline import (
    ensure_naive_index,
    index_expense_naive,
    search_naive,
)
from search.es_client import ensure_index, index_expense, search_expenses

ES_URL = "http://localhost:9200"
NAIVE_INDEX = "benchmark_naive_expenses"
OPTIMIZED_INDEX = "benchmark_optimized_expenses"
USER_ID = 1

CATEGORIES = ["Food", "Transport", "Bills", "Health", "Entertainment", "Shopping", "Other"]

RELEVANT_DESCRIPTIONS = [
    "Grocery shopping trip for the week",
    "Weekly grocery shopping at the supermarket",
    "Grocery shopping - vegetables and fruit",
]

DECOY_SHOPPING_DESCRIPTIONS = [
    "Online shopping for shoes",
    "Clothes shopping at the mall",
    "Shopping for new gadgets online",
    "Shopping trip for a birthday gift",
    "Electronics shopping - new headphones",
]

STEM_ONLY_PLURAL_DESCRIPTIONS = [
    "Weekly groceries run",
    "Groceries and vegetables for the house",
    "Bought groceries after work",
]

NOISE_DESCRIPTIONS = [
    "Electricity bill payment", "Mobile recharge", "Doctor visit copay",
    "Pharmacy medicines", "Movie night tickets", "Netflix subscription",
    "Auto rickshaw fare", "Uber ride to office", "Restaurant dinner",
    "Coffee with a friend", "Gym membership renewal", "Book purchase",
]


def _today_minus(days):
    from datetime import date, timedelta
    return (date(2026, 7, 1) - timedelta(days=days)).isoformat()


def build_dataset():
    rng = random.Random(42)
    docs = []
    doc_id = 1

    for desc in RELEVANT_DESCRIPTIONS:
        docs.append((doc_id, USER_ID, rng.uniform(200, 800), "Food", _today_minus(130), desc))
        doc_id += 1

    for i in range(30):
        desc = rng.choice(DECOY_SHOPPING_DESCRIPTIONS)
        docs.append((doc_id, USER_ID, rng.uniform(100, 3000), "Shopping", _today_minus(rng.randint(1, 10)), desc))
        doc_id += 1

    for desc in STEM_ONLY_PLURAL_DESCRIPTIONS:
        docs.append((doc_id, USER_ID, rng.uniform(300, 900), "Food", _today_minus(rng.randint(15, 40)), desc))
        doc_id += 1

    for i in range(200):
        desc = rng.choice(NOISE_DESCRIPTIONS)
        cat = rng.choice(CATEGORIES)
        docs.append((doc_id, USER_ID, rng.uniform(50, 5000), cat, _today_minus(rng.randint(1, 365)), desc))
        doc_id += 1

    return docs


@pytest.fixture(scope="module")
def es():
    client = Elasticsearch(ES_URL)
    try:
        if not client.ping():
            pytest.skip("Elasticsearch is not reachable at http://localhost:9200")
    except ESConnectionError:
        pytest.skip("Elasticsearch is not reachable at http://localhost:9200")
    yield client
    client.indices.delete(index=NAIVE_INDEX, ignore_unavailable=True)
    client.indices.delete(index=OPTIMIZED_INDEX, ignore_unavailable=True)


@pytest.fixture(scope="module")
def seeded(es):
    es.indices.delete(index=NAIVE_INDEX, ignore_unavailable=True)
    es.indices.delete(index=OPTIMIZED_INDEX, ignore_unavailable=True)
    ensure_naive_index(es, NAIVE_INDEX)
    ensure_index(es, index=OPTIMIZED_INDEX)

    dataset = build_dataset()
    for expense_id, user_id, amount, category, date, description in dataset:
        index_expense_naive(es, NAIVE_INDEX, expense_id, user_id, amount, category, date, description)
        index_expense(expense_id, user_id, amount, category, date, description, es=es, index=OPTIMIZED_INDEX)

    es.indices.refresh(index=NAIVE_INDEX)
    es.indices.refresh(index=OPTIMIZED_INDEX)
    return dataset


def test_stemming_recall_singular_query_matches_plural_docs(es, seeded):
    """Query 'grocery' (singular) should find docs that only ever say 'groceries' (plural)."""
    naive = search_naive(es, NAIVE_INDEX, USER_ID, q="grocery", size=50)
    optimized = search_expenses(USER_ID, q="grocery", size=50, es=es, index=OPTIMIZED_INDEX)

    naive_hits = {h["description"] for h in naive["hits"]}
    optimized_hits = {h["description"] for h in optimized["hits"]}

    plural_only = set(STEM_ONLY_PLURAL_DESCRIPTIONS)
    naive_found = plural_only & naive_hits
    optimized_found = plural_only & optimized_hits

    print(f"\n[stemming] naive found {len(naive_found)}/3 plural-only docs: {naive_found}")
    print(f"[stemming] optimized found {len(optimized_found)}/3 plural-only docs: {optimized_found}")

    assert len(optimized_found) == 3, "optimized (stemmed) search should find all 'groceries' docs for query 'grocery'"
    assert len(naive_found) < len(optimized_found), "naive (unstemmed) search should miss at least some plural-only docs"


def test_relevance_ranking_beats_hard_date_sort(es, seeded):
    """A highly relevant but older doc should outrank many recent-but-less-relevant docs."""
    naive = search_naive(es, NAIVE_INDEX, USER_ID, q="grocery shopping", size=5)
    optimized = search_expenses(USER_ID, q="grocery shopping", size=5, es=es, index=OPTIMIZED_INDEX)

    def relevant_count(hits):
        return sum(1 for h in hits["hits"] if "grocery" in h["description"].lower())

    naive_relevant = relevant_count(naive)
    optimized_relevant = relevant_count(optimized)

    print(f"\n[ranking] naive top-5 relevant (contains 'grocery'): {naive_relevant}/5")
    print(f"[ranking] optimized top-5 relevant (contains 'grocery'): {optimized_relevant}/5")
    print(f"[ranking] naive top-5 descriptions: {[h['description'] for h in naive['hits']]}")
    print(f"[ranking] optimized top-5 descriptions: {[h['description'] for h in optimized['hits']]}")

    assert optimized_relevant > naive_relevant, (
        "optimized function_score ranking should surface more truly relevant docs in top-5 "
        "than naive's hard sort-by-date, which ignores text relevance entirely"
    )


def test_latency_comparable_at_this_scale(es, seeded):
    """Latency should not regress meaningfully — function_score adds real but small overhead."""
    queries = [
        {"q": "grocery shopping"},
        {"category": "Shopping"},
        {"min_amount": 100, "max_amount": 1000},
        {"from_date": _today_minus(30), "to_date": _today_minus(1)},
        {"q": "bill"},
    ]
    iterations = 30

    def timed(fn):
        start = time.perf_counter()
        for _ in range(iterations):
            fn()
        return (time.perf_counter() - start) / iterations

    naive_times, optimized_times = [], []
    for params in queries:
        naive_times.append(timed(lambda p=params: search_naive(es, NAIVE_INDEX, USER_ID, size=20, **p)))
        optimized_times.append(timed(lambda p=params: search_expenses(USER_ID, size=20, es=es, index=OPTIMIZED_INDEX, **p)))

    naive_mean_ms = sum(naive_times) / len(naive_times) * 1000
    optimized_mean_ms = sum(optimized_times) / len(optimized_times) * 1000

    print(f"\n[latency] naive mean: {naive_mean_ms:.2f} ms/query")
    print(f"[latency] optimized mean: {optimized_mean_ms:.2f} ms/query")
    print(f"[latency] delta: {optimized_mean_ms - naive_mean_ms:+.2f} ms ({(optimized_mean_ms / naive_mean_ms - 1) * 100:+.1f}%)")

    assert optimized_mean_ms < naive_mean_ms * 3, (
        "optimized query should not be dramatically slower than naive at this corpus size"
    )
