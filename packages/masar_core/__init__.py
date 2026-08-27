"""masar_core — نواة المجال: الإعدادات، الثوابت، الصلاحيات، آلات الحالة، الأمان."""

from .config import Config, get_config
from .errors import (
    Conflict,
    DependencyUnavailable,
    FeasibilityViolation,
    Forbidden,
    InvalidTransition,
    MasarError,
    NotFound,
    OptimizationFailed,
    OutOfScope,
    RateLimited,
    ReasonRequired,
    Unauthorized,
    ValidationError,
)

__all__ = [
    "Config", "get_config",
    "MasarError", "ValidationError", "NotFound", "Conflict", "InvalidTransition",
    "Unauthorized", "Forbidden", "OutOfScope", "ReasonRequired", "RateLimited",
    "DependencyUnavailable", "OptimizationFailed", "FeasibilityViolation",
]
