"""Data models for the Statistical Profiler -- T4.2a.

Defines frozen dataclasses for:
- ColumnProfile: per-column statistics snapshot
- TableProfile: full table statistics snapshot
- ColumnDelta: per-column drift between two profiles
- ProfileDelta: comparison result between a baseline and synthetic profile
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ColumnProfile:
    """Immutable snapshot of one column's statistical properties.

    Numeric columns carry mean, stddev, min, max, and quartile fields.
    Categorical columns carry value_counts and cardinality.
    Both types carry null_count, null_rate, and dtype.

    Attributes:
        name: Column name.
        dtype: Pandas dtype string (e.g. ``"int64"``, ``"object"``).
        null_count: Number of null/NaN values.
        null_rate: Fraction of rows that are null (0.0-1.0).
        is_numeric: ``True`` when the column was profiled as numeric (integer
            or float dtype).  ``False`` for categorical columns.  Used by
            :meth:`~synth_engine.modules.profiler.profiler.StatisticalProfiler.compare`
            to classify all-null numeric columns correctly even when
            ``mean`` is ``None``.
        mean: Arithmetic mean; ``None`` for categorical or all-null columns.
        stddev: Sample standard deviation (ddof=1); ``None`` for categorical
            or all-null columns.
        min: Minimum value; ``None`` for categorical or all-null columns.
        max: Maximum value; ``None`` for categorical or all-null columns.
        q25: 25th percentile; ``None`` for categorical or all-null columns.
        q50: 50th percentile (median); ``None`` for categorical or all-null
            columns.
        q75: 75th percentile; ``None`` for categorical or all-null columns.
        value_counts: Mapping of category -> frequency; ``None`` for numeric
            columns.
        cardinality: Number of distinct non-null values; ``None`` for numeric
            columns.
    """

    name: str
    dtype: str
    null_count: int
    null_rate: float
    is_numeric: bool = False
    mean: float | None = None
    stddev: float | None = None
    min: float | None = None
    max: float | None = None
    q25: float | None = None
    q50: float | None = None
    q75: float | None = None
    value_counts: dict[str, int] | None = None
    cardinality: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary suitable for JSON export.

        Returns:
            Dictionary representation with all fields.  ``None`` values are
            preserved so that ``from_dict`` can restore them faithfully.
        """
        return {
            "name": self.name,
            "dtype": self.dtype,
            "null_count": self.null_count,
            "null_rate": self.null_rate,
            "is_numeric": self.is_numeric,
            "mean": self.mean,
            "stddev": self.stddev,
            "min": self.min,
            "max": self.max,
            "q25": self.q25,
            "q50": self.q50,
            "q75": self.q75,
            "value_counts": self.value_counts,
            "cardinality": self.cardinality,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ColumnProfile:
        """Deserialise from a plain dictionary.

        Args:
            data: Dictionary as produced by :meth:`to_dict`.

        Returns:
            A new :class:`ColumnProfile` instance.
        """
        return cls(
            name=data["name"],
            dtype=data["dtype"],
            null_count=data["null_count"],
            null_rate=data["null_rate"],
            is_numeric=data.get("is_numeric", False),
            mean=data.get("mean"),
            stddev=data.get("stddev"),
            min=data.get("min"),
            max=data.get("max"),
            q25=data.get("q25"),
            q50=data.get("q50"),
            q75=data.get("q75"),
            value_counts=data.get("value_counts"),
            cardinality=data.get("cardinality"),
        )


@dataclass(frozen=True)
class TableProfile:
    """Immutable snapshot of an entire table's statistical properties.

    Attributes:
        table_name: Name of the source table.
        row_count: Total number of rows in the DataFrame at profile time.
        columns: Mapping of column name -> :class:`ColumnProfile`.
        covariance_matrix: Nested dict
            ``{col_a: {col_b: cov_value, ...}, ...}`` for all numeric
            column pairs.  Empty dict when fewer than two numeric columns
            exist.
    """

    table_name: str
    row_count: int
    columns: dict[str, ColumnProfile]
    covariance_matrix: dict[str, dict[str, float]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary suitable for JSON export.

        Returns:
            Dictionary representation including all column profiles and the
            covariance matrix.
        """
        return {
            "table_name": self.table_name,
            "row_count": self.row_count,
            "columns": {name: col.to_dict() for name, col in self.columns.items()},
            "covariance_matrix": self.covariance_matrix,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TableProfile:
        """Deserialise from a plain dictionary.

        Args:
            data: Dictionary as produced by :meth:`to_dict`.

        Returns:
            A new :class:`TableProfile` instance.
        """
        columns = {
            name: ColumnProfile.from_dict(col_data) for name, col_data in data["columns"].items()
        }
        return cls(
            table_name=data["table_name"],
            row_count=data["row_count"],
            columns=columns,
            covariance_matrix=data.get("covariance_matrix", {}),
        )


@dataclass(frozen=True)
class ColumnDelta:
    """Drift metrics for a single column between baseline and synthetic profiles.

    Numeric drift is expressed as ``synthetic_value - baseline_value``.
    Categorical drift is expressed as the change in cardinality.

    Attributes:
        column_name: Name of the column being compared.
        mean_drift: Difference in mean (synthetic - baseline); ``None`` for
            categorical columns.
        stddev_drift: Difference in standard deviation (synthetic - baseline);
            ``None`` for categorical columns.
        cardinality_drift: Change in distinct-value count (synthetic -
            baseline); ``None`` for numeric columns.
    """

    column_name: str
    mean_drift: float | None = None
    stddev_drift: float | None = None
    cardinality_drift: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        See :meth:`from_dict` for the inverse operation.

        Returns:
            Dictionary representation with all drift fields.
        """
        return {
            "column_name": self.column_name,
            "mean_drift": self.mean_drift,
            "stddev_drift": self.stddev_drift,
            "cardinality_drift": self.cardinality_drift,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ColumnDelta:
        """Deserialise from a plain dictionary.

        Args:
            data: Dictionary as produced by :meth:`to_dict`.

        Returns:
            A new :class:`ColumnDelta` instance.
        """
        mean_drift = data.get("mean_drift")
        stddev_drift = data.get("stddev_drift")
        cardinality_drift = data.get("cardinality_drift")
        return cls(
            column_name=str(data["column_name"]),
            mean_drift=float(mean_drift) if mean_drift is not None else None,
            stddev_drift=float(stddev_drift) if stddev_drift is not None else None,
            cardinality_drift=int(cardinality_drift) if cardinality_drift is not None else None,
        )


@dataclass(frozen=True)
class ProfileDelta:
    """Comparison result between a baseline and a synthetic :class:`TableProfile`.

    Attributes:
        baseline_table: Name of the baseline table.
        synthetic_table: Name of the synthetic table.
        column_deltas: Mapping of column name -> :class:`ColumnDelta`.
    """

    baseline_table: str
    synthetic_table: str
    column_deltas: dict[str, ColumnDelta]

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary suitable for JSON export.

        See :meth:`from_dict` for the inverse operation.

        Returns:
            Dictionary representation with all column deltas.
        """
        return {
            "baseline_table": self.baseline_table,
            "synthetic_table": self.synthetic_table,
            "column_deltas": {name: delta.to_dict() for name, delta in self.column_deltas.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProfileDelta:
        """Deserialise from a plain dictionary.

        Args:
            data: Dictionary as produced by :meth:`to_dict`.

        Returns:
            A new :class:`ProfileDelta` instance.
        """
        raw_deltas: dict[str, Any] = data.get("column_deltas", {})
        if not isinstance(raw_deltas, dict):
            raise TypeError(f"column_deltas must be a dict, got {type(raw_deltas)}")
        column_deltas: dict[str, ColumnDelta] = {
            name: ColumnDelta.from_dict(cd) for name, cd in raw_deltas.items()
        }
        return cls(
            baseline_table=str(data["baseline_table"]),
            synthetic_table=str(data["synthetic_table"]),
            column_deltas=column_deltas,
        )
