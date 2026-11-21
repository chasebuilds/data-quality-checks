"""Declarative data-quality checks run as DuckDB SQL."""

from .checks import compile_check
from .config import CHECK_TYPES, CheckSpec, ConfigError, Suite, TableSource, load_suite
from .runner import CheckResult, SuiteResult, run_check, run_suite

__all__ = [
    "CHECK_TYPES", "CheckResult", "CheckSpec", "ConfigError", "Suite", "SuiteResult",
    "TableSource", "compile_check", "load_suite", "run_check", "run_suite",
]
__version__ = "0.1.0"
