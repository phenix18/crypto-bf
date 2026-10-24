import os
import sys


SERVER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)

import database
from database import _adapt_sql_for_postgres


def test_postgres_adapter_escapes_like_percent_literals():
    query = _adapt_sql_for_postgres(
        "SELECT * FROM signals WHERE content LIKE '%@%' AND title LIKE '%source%' AND id = ?"
    )

    assert "content LIKE '%%@%%'" in query
    assert "title LIKE '%%source%%'" in query
    assert query.endswith("id = %s")


def test_postgres_adapter_preserves_existing_escaped_percent_literals():
    query = _adapt_sql_for_postgres("SELECT * FROM signals WHERE content LIKE '%%引用%%'")

    assert "LIKE '%%引用%%'" in query



# DB_PATH is documented relative to the repository root, but the API has to be
# launched from service/server, so the value must not be resolved against the CWD.

def test_relative_db_path_is_anchored_at_project_root():
    resolved = database._resolve_sqlite_path("service/server/data/clawtrader.db")
    expected = os.path.join(
        database._PROJECT_ROOT, "service", "server", "data", "clawtrader.db"
    )
    assert resolved == os.path.abspath(expected)


def test_absolute_db_path_is_left_alone():
    assert database._resolve_sqlite_path("/tmp/ai-trader.db") == "/tmp/ai-trader.db"


def test_empty_db_path_falls_back_to_the_default():
    assert database._resolve_sqlite_path("") == database._DEFAULT_SQLITE_DB_PATH
