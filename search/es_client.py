from elasticsearch import Elasticsearch

from search.config import ELASTICSEARCH_URL, EXPENSES_INDEX

TEXT_ANALYZER = "expense_text_analyzer"

INDEX_BODY = {
    "settings": {
        "analysis": {
            "filter": {
                "expense_stop": {"type": "stop", "stopwords": "_english_"},
                "expense_stemmer": {"type": "stemmer", "language": "english"},
            },
            "analyzer": {
                TEXT_ANALYZER: {
                    "type": "custom",
                    "tokenizer": "standard",
                    "filter": ["lowercase", "asciifolding", "expense_stop", "expense_stemmer"],
                }
            },
        }
    },
    "mappings": {
        "properties": {
            "user_id": {"type": "keyword"},
            "amount": {"type": "scaled_float", "scaling_factor": 100},
            "category": {
                "type": "keyword",
                "fields": {"text": {"type": "text", "analyzer": TEXT_ANALYZER}},
            },
            "date": {"type": "date", "format": "yyyy-MM-dd"},
            "description": {
                "type": "text",
                "analyzer": TEXT_ANALYZER,
                "fields": {"keyword": {"type": "keyword", "ignore_above": 256}},
            },
        }
    },
}


def get_es_client() -> Elasticsearch:
    return Elasticsearch(ELASTICSEARCH_URL)


def ensure_index(es: Elasticsearch | None = None, index: str = EXPENSES_INDEX) -> None:
    es = es or get_es_client()
    if not es.indices.exists(index=index):
        es.indices.create(index=index, body=INDEX_BODY)


def index_expense(expense_id, user_id, amount, category, date, description,
                   es: Elasticsearch | None = None, index: str = EXPENSES_INDEX) -> None:
    es = es or get_es_client()
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


def build_query(user_id, q="", category="", min_amount=None, max_amount=None,
                 from_date="", to_date=""):
    filters = [{"term": {"user_id": user_id}}]
    if category:
        filters.append({"term": {"category": category}})
    if min_amount is not None or max_amount is not None:
        rng = {}
        if min_amount is not None:
            rng["gte"] = min_amount
        if max_amount is not None:
            rng["lte"] = max_amount
        filters.append({"range": {"amount": rng}})
    if from_date or to_date:
        rng = {}
        if from_date:
            rng["gte"] = from_date
        if to_date:
            rng["lte"] = to_date
        filters.append({"range": {"date": rng}})

    if q:
        must = [{
            "multi_match": {
                "query": q,
                "fields": ["description^3", "category.text"],
                "type": "best_fields",
                "fuzziness": "AUTO",
            }
        }]
    else:
        must = [{"match_all": {}}]

    base_query = {"bool": {"must": must, "filter": filters}}

    return {
        "function_score": {
            "query": base_query,
            "functions": [
                {
                    "gauss": {"date": {"origin": "now", "scale": "180d", "decay": 0.3}},
                    "weight": 4,
                }
            ],
            "score_mode": "sum",
            "boost_mode": "sum",
        }
    }


def search_expenses(user_id, q="", category="", min_amount=None, max_amount=None,
                     from_date="", to_date="", page=1, size=20,
                     es: Elasticsearch | None = None, index: str = EXPENSES_INDEX) -> dict:
    es = es or get_es_client()
    query = build_query(user_id, q, category, min_amount, max_amount, from_date, to_date)
    resp = es.search(
        index=index,
        query=query,
        from_=(page - 1) * size,
        size=size,
    )
    return {
        "hits": [h["_source"] for h in resp["hits"]["hits"]],
        "total": resp["hits"]["total"]["value"],
        "page": page,
        "size": size,
    }
