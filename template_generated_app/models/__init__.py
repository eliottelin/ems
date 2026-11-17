"""Daily demonstration components and shared scalar input validation."""
from math import isfinite


def require_finite(**values: float) -> None:
    """Reject missing/nonfinite scalar values before evaluating model equations."""
    for name, value in values.items():
        try:
            finite = isfinite(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite numeric scalar") from exc
        if not finite:
            raise ValueError(f"{name} must be finite")
