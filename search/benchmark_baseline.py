"""The original (pre-optimization) mapping and query design, kept only so
tests/test_search_benchmark.py can measure the improvement against it.
Not imported by the app — search/es_client.py is the real implementation."""

from elasticsearch import Elasticsearch

NAIVE_INDEX_BODY = {
    "mappings": {
        "properties": {
            "user_id": {"type": "integer"},
            "amount": {"type": "float"},
            "category": {"type": "keyword"},
            "date": {"type": "date", "format": "yyyy-MM-dd"},
            "description": {"type": "text"},
        }
    }
}


def ensure_naive_index(es: Elasticsearch, index: str) -> None:
    if not es.indices.exists(index=index):
        es.indices.create(index=index, body=NAIVE_INDEX_BODY)


def index_expense_naive(es: Elasticsearch, index: str, expense_id, user_id, amount,
                         category, date, description) -> None:
    es.index(
        index=index,
        id=expense_id,
        document={
            "user_id": user_id,
            "amount": amount,
            "category": category,
            "date": date,
            "description": description or "",
        },
    )


def build_naive_query(user_id, q="", category="", min_amount=None, max_amount=None,
                       from_date="", to_date=""):
    must = [{"term": {"user_id": user_id}}]
    if q:
        must.append({"match": {"description": q}})
    if category:
        must.append({"term": {"category": category}})
    if min_amount is not None or max_amount is not None:
        rng = {}
        if min_amount is not None:
            rng["gte"] = min_amount
        if max_amount is not None:
            rng["lte"] = max_amount
        must.append({"range": {"amount": rng}})
    if from_date or to_date:
        rng = {}
        if from_date:
            rng["gte"] = from_date
        if to_date:
            rng["lte"] = to_date
        must.append({"range": {"date": rng}})
    return {"bool": {"must": must}}


def search_naive(es: Elasticsearch, index: str, user_id, q="", category="",
                  min_amount=None, max_amount=None, from_date="", to_date="",
                  page=1, size=20) -> dict:
    query = build_naive_query(user_id, q, category, min_amount, max_amount, from_date, to_date)
    resp = es.search(
        index=index,
        query=query,
        sort=[{"date": "desc"}],
        from_=(page - 1) * size,
        size=size,
    )
    return {
        "hits": [h["_source"] for h in resp["hits"]["hits"]],
        "total": resp["hits"]["total"]["value"],
        "page": page,
        "size": size,
    }
