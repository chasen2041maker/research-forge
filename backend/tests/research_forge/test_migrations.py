"""Migration contracts: revisions are static snapshots and can upgrade an empty database."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect


command = pytest.importorskip("alembic.command")
Config = pytest.importorskip("alembic.config").Config


def test_alembic_upgrade_and_downgrade_preserve_revision_boundaries(tmp_path: Path) -> None:
    database = tmp_path / "research-forge.db"
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database.as_posix()}")

    command.upgrade(config, "head")
    inspector = inspect(create_engine(f"sqlite+pysqlite:///{database.as_posix()}"))
    assert "rf_approvals" in inspector.get_table_names()
    assert "resume_from_attempt_id" in {column["name"] for column in inspector.get_columns("rf_attempts")}
    assert "original_spec_json" in {column["name"] for column in inspector.get_columns("rf_missions")}
    assert "version" in {column["name"] for column in inspector.get_columns("rf_tasks")}
    assert "version" in {column["name"] for column in inspector.get_columns("rf_operations")}
    assert "version" in {column["name"] for column in inspector.get_columns("rf_approvals")}
    assert {"patch_sha256", "patch_size_bytes", "patch_media_type"} <= {
        column["name"] for column in inspector.get_columns("rf_approvals")
    }
    assert {"uq_rf_tasks_mission_type"} <= {
        constraint["name"] for constraint in inspector.get_unique_constraints("rf_tasks")
    }
    assert {"uq_rf_attempts_task_number"} <= {
        constraint["name"] for constraint in inspector.get_unique_constraints("rf_attempts")
    }
    assert {"uq_rf_approvals_attempt_action"} <= {
        constraint["name"] for constraint in inspector.get_unique_constraints("rf_approvals")
    }

    command.downgrade(config, "base")
    inspector = inspect(create_engine(f"sqlite+pysqlite:///{database.as_posix()}"))
    assert "rf_missions" not in inspector.get_table_names()
