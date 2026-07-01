import os

ELASTICSEARCH_URL = os.environ.get("ELASTICSEARCH_URL", "http://localhost:9200")
EXPENSES_INDEX = os.environ.get("EXPENSES_INDEX", "expenses")
