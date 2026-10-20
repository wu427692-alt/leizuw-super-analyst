"""Additive cursor upgrades preserve old state and tolerate parallel startup."""

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from sqlalchemy import create_engine, inspect

from src.storage import DatabaseManager


def test_checkpoint_upgrades_are_idempotent_and_preserve_existing_rows(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}", connect_args={"timeout": 10})
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE research_report_sync_states(source_key TEXT PRIMARY KEY, completed_windows INTEGER)")
        connection.exec_driver_sql("INSERT INTO research_report_sync_states VALUES ('tushare.research_report', 17)")
        connection.exec_driver_sql("CREATE TABLE zsxq_sync_states(group_id TEXT PRIMARY KEY, last_topic_id TEXT)")
        connection.exec_driver_sql("INSERT INTO zsxq_sync_states VALUES ('g1', 'original-topic')")
    db = SimpleNamespace(_is_sqlite_engine=True, _engine=engine)

    def upgrade(_):
        DatabaseManager._ensure_research_report_cursor_column(db)
        DatabaseManager._ensure_zsxq_checkpoint_columns(db)

    try:
        with ThreadPoolExecutor(max_workers=3) as executor:
            list(executor.map(upgrade, range(6)))
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT completed_windows, cursor_offset FROM research_report_sync_states").one() == (17, 0)
            assert connection.exec_driver_sql("SELECT last_topic_id, history_checkpoint_json, incremental_checkpoint_json FROM zsxq_sync_states").one() == ('original-topic', '{}', '{}')
            assert len(inspect(connection).get_columns("zsxq_sync_states")) == 4
    finally:
        engine.dispose()
