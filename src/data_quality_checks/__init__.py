"""Declarative data-quality checks run as DuckDB SQL."""

from .config import CHECK_TYPES, CheckSpec, ConfigError, Suite, TableSource, load_suite

__all__ = ["CHECK_TYPES", "CheckSpec", "ConfigError", "Suite", "TableSource", "load_suite"]
__version__ = "0.1.0"
