from __future__ import annotations

import os
from dataclasses import dataclass, fields

_ENV_PREFIX = "TRADINGAGENTS_EXEC_"
_BOOL_TRUE = ("true", "1", "yes", "on")
_BOOL_FALSE = ("false", "0", "no", "off")


def _coerce(value: str, reference: object) -> object:
    """Coerce an env string to the type of the field's default value."""
    if isinstance(reference, bool):
        normalized = value.strip().lower()
        if normalized in _BOOL_TRUE:
            return True
        if normalized in _BOOL_FALSE:
            return False
        raise ValueError(f"expected a boolean, got {value!r}")
    if isinstance(reference, int) and not isinstance(reference, bool):
        return int(value)
    if isinstance(reference, float):
        return float(value)
    return value


@dataclass(frozen=True)
class ExecutionConfig:
    per_name_pct: float = 0.05
    max_concurrent_positions: int = 10
    max_gross_exposure_pct: float = 1.0
    daily_loss_limit_pct: float = 0.03
    kill_switch_flatten: bool = False
    top_k: int = 10
    allow_live: bool = False

    @classmethod
    def from_env(cls) -> ExecutionConfig:
        overrides: dict[str, object] = {}
        for f in fields(cls):
            env_var = _ENV_PREFIX + f.name.upper()
            raw = os.environ.get(env_var)
            if raw is None or raw == "":
                continue
            try:
                overrides[f.name] = _coerce(raw, f.default)
            except ValueError as exc:
                raise ValueError(f"Invalid value for {env_var}: {exc}") from exc
        return cls(**overrides)
