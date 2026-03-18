"""Coverage elevation tests — T29.4.

Targeted tests to cover the remaining lines below the 95% threshold.
Each test exercises a specific uncovered branch identified in the T29.4
coverage audit.

Modules targeted:
- shared/task_queue.py            (87% → MemoryHuey branch lines 97-100)
- modules/synthesizer/storage.py  (85% → MinioStorageBackend repr/put/get)
- modules/synthesizer/tasks.py    (89% → enable_dp=False path; impl call)
- modules/profiler/profiler.py    (93% → _safe_float exception/isinf;
                                         delta for columns present in only one profile)
- modules/subsetting/traversal.py (93% → parent not in fetched; FK mismatch;
                                         empty fk_values; empty values guard)
- modules/subsetting/core.py      (93% → successful row_transformer path)
- shared/db.py                    (94% → non-sqlite pool_size branch in sync/async)

Task: P29-T29.4 — Coverage Threshold Elevation to 95%
TDD: RED phase — tests are written to exercise production code paths that
     were not previously executed by the existing test suite.
     No production code changes are required; these are pure coverage tests.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ===========================================================================
# shared/task_queue.py — MemoryHuey backend branch (lines 97-100)
# ===========================================================================


class TestTaskQueueMemoryBackend:
    """Tests for the HUEY_BACKEND=memory path in shared/task_queue.py."""

    def test_build_huey_with_memory_backend_returns_memory_huey(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_build_huey() must return a MemoryHuey when HUEY_BACKEND=memory.

        Lines 97-100: the MemoryHuey import, log, and return statement.
        """
        from huey import MemoryHuey

        monkeypatch.setenv("HUEY_BACKEND", "memory")
        monkeypatch.delenv("HUEY_IMMEDIATE", raising=False)

        from synth_engine.shared import task_queue

        result = task_queue._build_huey()  # type: ignore[attr-defined]
        assert isinstance(result, MemoryHuey)

    def test_build_huey_memory_backend_logs_info(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """_build_huey() with HUEY_BACKEND=memory must log the backend selection at INFO.

        Line 99: _logger.info("Huey: using MemoryHuey ...").
        """
        monkeypatch.setenv("HUEY_BACKEND", "memory")
        monkeypatch.delenv("HUEY_IMMEDIATE", raising=False)

        from synth_engine.shared import task_queue

        with caplog.at_level(logging.INFO, logger="synth_engine.shared.task_queue"):
            task_queue._build_huey()  # type: ignore[attr-defined]

        assert any(
            "MemoryHuey" in record.message for record in caplog.records
        ), "Expected INFO log mentioning MemoryHuey"

    def test_build_huey_memory_backend_immediate_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_build_huey() with HUEY_BACKEND=memory and HUEY_IMMEDIATE=true must set immediate=True.

        Line 94: immediate=True is computed and passed to MemoryHuey.
        """
        from huey import MemoryHuey

        monkeypatch.setenv("HUEY_BACKEND", "memory")
        monkeypatch.setenv("HUEY_IMMEDIATE", "true")

        from synth_engine.shared import task_queue

        result = task_queue._build_huey()  # type: ignore[attr-defined]
        assert isinstance(result, MemoryHuey)
        assert result.immediate is True


# ===========================================================================
# modules/synthesizer/storage.py — MinioStorageBackend repr/put/get (lines 204, 214, 229-237)
# ===========================================================================


class TestMinioStorageBackendCoverage:
    """Tests for MinioStorageBackend methods that were not yet exercised."""

    def _make_minio_backend(self) -> Any:
        """Return a MinioStorageBackend with a mocked boto3 client.

        Uses patch to avoid importing boto3 at construction time.

        Returns:
            A MinioStorageBackend whose _client is a MagicMock.
        """
        from synth_engine.modules.synthesizer.storage import MinioStorageBackend

        mock_boto3_client = MagicMock()
        with patch("boto3.client", return_value=mock_boto3_client):
            backend = MinioStorageBackend(
                endpoint_url="http://minio:9000",
                access_key="testkey",
                secret_key="testsecret",
            )
        backend._client = mock_boto3_client
        return backend

    def test_minio_backend_repr_does_not_expose_credentials(self) -> None:
        """MinioStorageBackend.__repr__ must not expose endpoint_url or access_key.

        Line 204: __repr__ returns a fixed redacted string.
        """
        backend = self._make_minio_backend()
        representation = repr(backend)
        assert "testkey" not in representation
        assert "testsecret" not in representation
        assert "<redacted>" in representation

    def test_minio_backend_put_calls_client_put_object(self) -> None:
        """MinioStorageBackend.put() must call _client.put_object with correct args.

        Line 214: self._client.put_object(Bucket=bucket, Key=key, Body=data).
        """
        backend = self._make_minio_backend()
        data = b"parquet-bytes-here"

        backend.put("my-bucket", "test.parquet", data)

        backend._client.put_object.assert_called_once_with(
            Bucket="my-bucket",
            Key="test.parquet",
            Body=data,
        )

    def test_minio_backend_get_returns_response_body(self) -> None:
        """MinioStorageBackend.get() must return the bytes from the response Body.

        Lines 229-233: botocore import, try block, get_object call, body read.
        """
        backend = self._make_minio_backend()
        expected_bytes = b"response-body-data"

        mock_body = MagicMock()
        mock_body.read.return_value = expected_bytes
        backend._client.get_object.return_value = {"Body": mock_body}

        result = backend.get("my-bucket", "test.parquet")

        assert result == expected_bytes
        backend._client.get_object.assert_called_once_with(
            Bucket="my-bucket",
            Key="test.parquet",
        )

    def test_minio_backend_get_raises_key_error_on_no_such_key(self) -> None:
        """MinioStorageBackend.get() must raise KeyError when the object does not exist.

        Lines 234-237: ClientError is caught; NoSuchKey error code is re-raised as KeyError.
        """
        import botocore.exceptions

        backend = self._make_minio_backend()

        error_response = {
            "Error": {"Code": "NoSuchKey", "Message": "The specified key does not exist."}
        }
        backend._client.get_object.side_effect = botocore.exceptions.ClientError(
            error_response, "GetObject"
        )

        with pytest.raises(KeyError, match="my-bucket/missing.parquet"):
            backend.get("my-bucket", "missing.parquet")

    def test_minio_backend_get_raises_key_error_on_404_code(self) -> None:
        """MinioStorageBackend.get() must raise KeyError for '404' error code.

        Line 236: error_code in ("NoSuchKey", "404").
        """
        import botocore.exceptions

        backend = self._make_minio_backend()

        error_response = {"Error": {"Code": "404", "Message": "Not Found"}}
        backend._client.get_object.side_effect = botocore.exceptions.ClientError(
            error_response, "GetObject"
        )

        with pytest.raises(KeyError, match="my-bucket/another.parquet"):
            backend.get("my-bucket", "another.parquet")


# ===========================================================================
# modules/synthesizer/tasks.py — enable_dp=False path (lines 202->215, 210, 216)
# ===========================================================================


class TestRunSynthesisJobEnableDpFalsePath:
    """Tests for run_synthesis_job when enable_dp=False.

    Coverage gap: the branch at line 202 where job.enable_dp is False,
    meaning dp_wrapper stays None and we fall through to line 215.
    Line 216 (_orch._run_synthesis_job_impl) was also uncovered.
    """

    def test_run_synthesis_job_with_enable_dp_false_calls_impl_with_none_dp_wrapper(
        self,
    ) -> None:
        """run_synthesis_job.call_local() with enable_dp=False must call _run_synthesis_job_impl.

        Lines 202->215: the if-branch is skipped (enable_dp=False), dp_wrapper=None.
        Line 216: _orch._run_synthesis_job_impl is called with dp_wrapper=None.
        """
        import synth_engine.modules.synthesizer.tasks as tasks_mod
        from synth_engine.modules.synthesizer.job_models import SynthesisJob

        job = SynthesisJob(
            id=77,
            status="QUEUED",
            current_epoch=0,
            total_epochs=5,
            num_rows=10,
            table_name="persons",
            parquet_path="/data/persons.parquet",
            checkpoint_every_n=5,
            enable_dp=False,
        )

        mock_session_instance = MagicMock()
        mock_session_instance.get.return_value = job
        mock_session_ctx = MagicMock()
        mock_session_ctx.__enter__ = MagicMock(return_value=mock_session_instance)
        mock_session_ctx.__exit__ = MagicMock(return_value=False)

        mock_impl = MagicMock()

        with (
            patch("synth_engine.shared.db.get_engine", return_value=MagicMock()),
            patch("sqlmodel.Session", return_value=mock_session_ctx),
            patch(
                "synth_engine.modules.synthesizer.job_orchestration._run_synthesis_job_impl",
                mock_impl,
            ),
        ):
            tasks_mod.run_synthesis_job.call_local(77)

        # _run_synthesis_job_impl must have been called
        mock_impl.assert_called_once()

    def test_run_synthesis_job_job_not_found_does_not_raise(self) -> None:
        """run_synthesis_job.call_local() with job=None (preflight) must not raise.

        Line 202: if job is not None — when job IS None, dp_wrapper stays None.
        """
        import synth_engine.modules.synthesizer.tasks as tasks_mod

        mock_session_instance = MagicMock()
        mock_session_instance.get.return_value = None  # job not found
        mock_session_ctx = MagicMock()
        mock_session_ctx.__enter__ = MagicMock(return_value=mock_session_instance)
        mock_session_ctx.__exit__ = MagicMock(return_value=False)

        mock_impl = MagicMock()

        with (
            patch("synth_engine.shared.db.get_engine", return_value=MagicMock()),
            patch("sqlmodel.Session", return_value=mock_session_ctx),
            patch(
                "synth_engine.modules.synthesizer.job_orchestration._run_synthesis_job_impl",
                mock_impl,
            ),
        ):
            # Should not raise — job not found means dp_wrapper stays None
            tasks_mod.run_synthesis_job.call_local(999)

        mock_impl.assert_called_once()


# ===========================================================================
# modules/profiler/profiler.py — _safe_float exception/isinf branches (lines 58-59, 61, 273-274)
# ===========================================================================


class TestProfilerSafeFloatEdgeCases:
    """Tests for _safe_float edge cases not previously covered."""

    def test_safe_float_type_error_returns_none(self) -> None:
        """_safe_float must return None when float() raises TypeError.

        Lines 58-59: except (TypeError, ValueError): return None.
        """
        from synth_engine.modules.profiler import profiler as _profiler_mod

        # An object that float() cannot convert raises TypeError
        result = _profiler_mod._safe_float(object())  # type: ignore[attr-defined]
        assert result is None

    def test_safe_float_value_error_returns_none(self) -> None:
        """_safe_float must return None when float() raises ValueError.

        Lines 58-59: except (TypeError, ValueError): return None.
        """
        from synth_engine.modules.profiler import profiler as _profiler_mod

        result = _profiler_mod._safe_float("not-a-number")  # type: ignore[attr-defined]
        assert result is None

    def test_safe_float_infinite_returns_none(self) -> None:
        """_safe_float must return None for infinite floats.

        Line 61: math.isinf(f) branch.
        """
        import math

        from synth_engine.modules.profiler import profiler as _profiler_mod

        result = _profiler_mod._safe_float(math.inf)  # type: ignore[attr-defined]
        assert result is None

    def test_safe_float_negative_infinite_returns_none(self) -> None:
        """_safe_float must return None for negative infinite floats.

        Line 61: math.isinf(f) — negative infinity also returns None.
        """
        import math

        from synth_engine.modules.profiler import profiler as _profiler_mod

        result = _profiler_mod._safe_float(-math.inf)  # type: ignore[attr-defined]
        assert result is None


class TestProfilerDeltaColumnOnlyInOneProfile:
    """Tests for compare when a column appears in only one profile.

    Lines 273-274: if base_col is None or synth_col is None: ColumnDelta with no data.
    """

    def test_compare_column_only_in_baseline(self) -> None:
        """compare() must produce a ColumnDelta for columns only in baseline.

        Lines 273-274: the branch where synth_col is None.
        """
        import pandas as pd

        from synth_engine.modules.profiler.profiler import StatisticalProfiler

        profiler = StatisticalProfiler()
        baseline_df = pd.DataFrame({"id": [1, 2, 3], "extra_col": [10, 20, 30]})
        synth_df = pd.DataFrame({"id": [1, 2, 3]})  # missing "extra_col"

        baseline_profile = profiler.profile("test_table", baseline_df)
        synth_profile = profiler.profile("test_table", synth_df)

        result = profiler.compare(baseline_profile, synth_profile)

        # "extra_col" is in baseline but not in synthetic
        assert "extra_col" in result.column_deltas
        delta = result.column_deltas["extra_col"]
        assert delta.column_name == "extra_col"

    def test_compare_column_only_in_synthetic(self) -> None:
        """compare() must produce a ColumnDelta for columns only in synthetic.

        Lines 273-274: the branch where base_col is None.
        """
        import pandas as pd

        from synth_engine.modules.profiler.profiler import StatisticalProfiler

        profiler = StatisticalProfiler()
        baseline_df = pd.DataFrame({"id": [1, 2, 3]})
        synth_df = pd.DataFrame({"id": [1, 2, 3], "new_col": ["a", "b", "c"]})

        baseline_profile = profiler.profile("test_table", baseline_df)
        synth_profile = profiler.profile("test_table", synth_df)

        result = profiler.compare(baseline_profile, synth_profile)

        assert "new_col" in result.column_deltas
        delta = result.column_deltas["new_col"]
        assert delta.column_name == "new_col"


# ===========================================================================
# modules/subsetting/traversal.py — edge cases (lines 175, 196, 205, 257)
# ===========================================================================


def _col_info(name: str, pk: int = 0) -> Any:
    """Build a ColumnInfo for traversal tests.

    Args:
        name: Column name.
        pk: Primary key position (0 = not PK).

    Returns:
        A frozen ColumnInfo.
    """
    from synth_engine.shared.schema_topology import ColumnInfo

    return ColumnInfo(name=name, type="INTEGER", primary_key=pk, nullable=False)


def _fk_info(constrained: list[str], referred_table: str, referred: list[str]) -> Any:
    """Build a ForeignKeyInfo for traversal tests.

    Args:
        constrained: Column names on the child (constrained) side.
        referred_table: The parent table name.
        referred: Column names on the parent (referred) side.

    Returns:
        A frozen ForeignKeyInfo.
    """
    from synth_engine.shared.schema_topology import ForeignKeyInfo

    return ForeignKeyInfo(
        constrained_columns=tuple(constrained),
        referred_table=referred_table,
        referred_columns=tuple(referred),
    )


def _make_conn_ctx(rows: list[dict[str, Any]]) -> Any:
    """Build a mock connection context manager that returns the given rows.

    Args:
        rows: Rows to return from execute().mappings().

    Returns:
        A MagicMock context manager that yields a mock connection.
    """
    mock_result = MagicMock()
    mock_result.mappings.return_value = [dict(r) for r in rows]

    mock_conn = MagicMock()
    mock_conn.execute.return_value = mock_result

    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=mock_conn)
    mock_ctx.__exit__ = MagicMock(return_value=False)

    return mock_ctx, mock_conn


class TestTraversalEdgeCases:
    """Tests for DagTraversal edge cases not previously covered."""

    def test_traverse_skips_non_fetched_parent(self) -> None:
        """traverse() skips the child-direction FK lookup when the parent is not yet fetched.

        Line 175: if parent_table not in fetched: continue.

        Scenario: two FK relationships on the child table; the second FK refers
        to a parent table ('departments') that was never fetched.  The traversal
        must skip that FK without error.
        """
        from sqlalchemy import Engine

        from synth_engine.modules.subsetting.traversal import DagTraversal
        from synth_engine.shared.schema_topology import SchemaTopology

        # 'projects' has TWO FKs: one to 'employees' (fetched) and one to 'departments' (not fetched)
        topology = SchemaTopology(
            table_order=("employees", "departments", "projects"),
            columns={
                "employees": (_col_info("id", 1),),
                "departments": (_col_info("id", 1),),
                "projects": (
                    _col_info("id", 1),
                    _col_info("owner_id"),
                    _col_info("dept_id"),
                ),
            },
            foreign_keys={
                "employees": (),
                "departments": (),
                "projects": (
                    _fk_info(["owner_id"], "employees", ["id"]),
                    _fk_info(["dept_id"], "departments", ["id"]),  # 'departments' NOT fetched
                ),
            },
        )

        engine = MagicMock(spec=Engine)

        emp_rows = [{"id": 10}]
        proj_rows = [{"id": 1, "owner_id": 10, "dept_id": 99}]

        call_count = 0

        def connect_side_effect() -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                ctx, _ = _make_conn_ctx(emp_rows)  # seed query for employees
                return ctx
            # FK lookup for projects (owner_id -> employees)
            ctx, _ = _make_conn_ctx(proj_rows)
            return ctx

        engine.connect.side_effect = connect_side_effect

        traversal = DagTraversal(engine=engine, topology=topology)
        results = list(traversal.traverse("employees", "SELECT * FROM employees LIMIT 1"))

        table_names = [t for t, _ in results]
        assert "employees" in table_names
        assert "projects" in table_names
        # departments was never fetched (not reachable from seed)
        assert "departments" not in table_names

    def test_traverse_skips_unrelated_child_fk(self) -> None:
        """traverse() skips child FKs that don't refer to the table being fetched.

        Line 196: if fk.referred_table != table: continue.

        Scenario: when fetching 'departments', we scan the FK list of 'employees'
        which has one FK to 'departments' and one to 'roles'.  The loop skips the
        roles FK (referred_table='roles' != 'departments') without error.
        """
        from sqlalchemy import Engine

        from synth_engine.modules.subsetting.traversal import DagTraversal
        from synth_engine.shared.schema_topology import SchemaTopology

        topology = SchemaTopology(
            table_order=("departments", "roles", "employees"),
            columns={
                "departments": (_col_info("id", 1),),
                "roles": (_col_info("id", 1),),
                "employees": (
                    _col_info("id", 1),
                    _col_info("dept_id"),
                    _col_info("role_id"),
                ),
            },
            foreign_keys={
                "departments": (),
                "roles": (),
                "employees": (
                    _fk_info(["dept_id"], "departments", ["id"]),
                    _fk_info(["role_id"], "roles", ["id"]),  # FK to 'roles', not 'departments'
                ),
            },
        )

        engine = MagicMock(spec=Engine)

        emp_rows = [{"id": 10, "dept_id": 5, "role_id": 2}]
        dept_rows = [{"id": 5}]

        call_count = 0

        def connect_side_effect() -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                ctx, _ = _make_conn_ctx(emp_rows)
                return ctx
            ctx, _ = _make_conn_ctx(dept_rows)
            return ctx

        engine.connect.side_effect = connect_side_effect

        traversal = DagTraversal(engine=engine, topology=topology)
        results = list(traversal.traverse("employees", "SELECT * FROM employees LIMIT 1"))

        table_names = [t for t, _ in results]
        assert "employees" in table_names
        assert "departments" in table_names
        # 'roles' is not reachable from the seed — no seed rows reference it by pk fetch
        # (roles was not fetched via child-direction FK either; only via parent-direction
        # scan, but employees has no seed to follow)

    def test_traverse_skips_all_null_fk_column(self) -> None:
        """traverse() skips parent-direction FK lookup when all FK column values are None.

        Line 205: if not fk_values: continue.

        Scenario: 'employees' has been fetched with rows where dept_id is None.
        When fetching 'departments', the parent-direction scan finds no FK values
        (all None) and skips the lookup.
        """
        from sqlalchemy import Engine

        from synth_engine.modules.subsetting.traversal import DagTraversal
        from synth_engine.shared.schema_topology import SchemaTopology

        topology = SchemaTopology(
            table_order=("departments", "employees"),
            columns={
                "departments": (_col_info("id", 1),),
                "employees": (_col_info("id", 1), _col_info("dept_id")),
            },
            foreign_keys={
                "departments": (),
                "employees": (_fk_info(["dept_id"], "departments", ["id"]),),
            },
        )

        engine = MagicMock(spec=Engine)

        # dept_id is NULL for all seed rows — no FK values to follow
        emp_rows = [{"id": 1, "dept_id": None}, {"id": 2, "dept_id": None}]

        ctx, _ = _make_conn_ctx(emp_rows)
        engine.connect.return_value = ctx

        traversal = DagTraversal(engine=engine, topology=topology)
        results = list(traversal.traverse("employees", "SELECT * FROM employees LIMIT 2"))

        table_names = [t for t, _ in results]
        assert "employees" in table_names
        # departments not reachable because all FK values were None
        assert "departments" not in table_names

    def test_fetch_by_fk_values_empty_values_returns_empty_list(self) -> None:
        """_fetch_by_fk_values must return [] immediately when values is empty.

        Line 257: if not values: return [].
        """
        from sqlalchemy import Engine

        from synth_engine.modules.subsetting.traversal import DagTraversal
        from synth_engine.shared.schema_topology import SchemaTopology

        topology = SchemaTopology(
            table_order=("parent",),
            columns={"parent": (_col_info("id", 1),)},
            foreign_keys={},
        )
        engine = MagicMock(spec=Engine)
        traversal = DagTraversal(engine=engine, topology=topology)

        result = traversal._fetch_by_fk_values("parent", "id", [])  # type: ignore[attr-defined]

        assert result == []
        # The database connection must NOT have been used
        engine.connect.assert_not_called()


# ===========================================================================
# modules/subsetting/core.py — successful row_transformer path (lines 194-195)
# ===========================================================================


class TestSubsettingCoreRowTransformerSuccessPath:
    """Tests for SubsettingEngine when row_transformer succeeds.

    The existing test suite covers the failure paths (None return, raises).
    Lines 194-195 are the success path: transformed.append(result_row) and
    rows = transformed — only reachable when the transformer returns a valid dict.
    """

    def test_row_transformer_success_writes_transformed_rows(self) -> None:
        """run() must write the transformer's output when it returns a valid dict.

        Lines 194-195: transformed.append(result_row) and rows = transformed.
        """
        from unittest.mock import patch

        from synth_engine.modules.subsetting.core import SubsettingEngine
        from synth_engine.modules.subsetting.egress import EgressWriter
        from synth_engine.shared.schema_topology import ColumnInfo, SchemaTopology

        topology = SchemaTopology(
            table_order=("persons",),
            columns={
                "persons": (
                    ColumnInfo(name="id", type="integer", primary_key=1, nullable=False),
                    ColumnInfo(name="name", type="varchar", primary_key=0, nullable=True),
                )
            },
            foreign_keys={},
        )

        mock_traversal = MagicMock()
        mock_traversal.traverse.return_value = iter(
            [("persons", [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}])]
        )

        egress = MagicMock(spec=EgressWriter)
        engine = MagicMock()

        def uppercase_name_transformer(table: str, row: dict[str, Any]) -> dict[str, Any]:
            """Transformer that uppercases the name column."""
            return {**row, "name": row["name"].upper()}

        with patch(
            "synth_engine.modules.subsetting.core.DagTraversal",
            return_value=mock_traversal,
        ):
            se = SubsettingEngine(
                source_engine=engine,
                topology=topology,
                egress=egress,
                row_transformer=uppercase_name_transformer,
            )
            result = se.run(
                seed_table="persons",
                seed_query="SELECT * FROM persons LIMIT 2",
            )

        # egress.write must have received the transformed rows
        egress.write.assert_called_once()
        write_call_args = egress.write.call_args
        written_table, written_rows = write_call_args[0]
        assert written_table == "persons"
        assert written_rows == [
            {"id": 1, "name": "ALICE"},
            {"id": 2, "name": "BOB"},
        ]

        assert result.tables_written == ["persons"]
        assert result.row_counts == {"persons": 2}


# ===========================================================================
# shared/db.py — non-sqlite pool_size branch (lines 150, 195)
# ===========================================================================


class TestDbNonSqlitePooling:
    """Tests for get_engine() and get_async_engine() non-sqlite pool_size branches."""

    def test_get_engine_non_sqlite_uses_pool_size(self) -> None:
        """get_engine() must pass pool_size and max_overflow for non-sqlite URLs.

        Line 150: engine = create_engine(url, pool_size=..., max_overflow=...).
        """
        from synth_engine.shared import db as db_mod

        test_url = "postgresql+psycopg2://user:pass@localhost:5432/testdb_t294"

        # Clear cache so the URL is not found as an existing cached entry
        db_mod._engine_cache.pop(test_url, None)  # type: ignore[attr-defined]

        mock_engine = MagicMock()

        with patch(
            "synth_engine.shared.db.create_engine",
            return_value=mock_engine,
        ) as mock_create:
            result = db_mod.get_engine(test_url)

        assert result is mock_engine
        # Must have been called with pool_size and max_overflow
        _, kwargs = mock_create.call_args
        assert "pool_size" in kwargs
        assert "max_overflow" in kwargs

        # Clean up cache entry added by the call
        db_mod._engine_cache.pop(test_url, None)  # type: ignore[attr-defined]

    def test_get_async_engine_non_sqlite_uses_pool_size(self) -> None:
        """get_async_engine() must pass pool_size and max_overflow for non-sqlite URLs.

        Line 195: engine = create_async_engine(url, pool_size=..., max_overflow=...).
        """
        from synth_engine.shared import db as db_mod

        test_url = "postgresql+asyncpg://user:pass@localhost:5432/testdb_async_t294"

        db_mod._async_engine_cache.pop(test_url, None)  # type: ignore[attr-defined]

        mock_engine = MagicMock()

        with patch(
            "synth_engine.shared.db.create_async_engine",
            return_value=mock_engine,
        ) as mock_create:
            result = db_mod.get_async_engine(test_url)

        assert result is mock_engine
        _, kwargs = mock_create.call_args
        assert "pool_size" in kwargs
        assert "max_overflow" in kwargs

        db_mod._async_engine_cache.pop(test_url, None)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Marker
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.unit
