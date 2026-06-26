import pytest

from tradingagents.execution.config import ExecutionConfig


def test_defaults():
    cfg = ExecutionConfig()
    assert cfg.per_name_pct == 0.05
    assert cfg.max_concurrent_positions == 10
    assert cfg.allow_live is False


def test_from_env_overrides_typed(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_EXEC_PER_NAME_PCT", "0.1")
    monkeypatch.setenv("TRADINGAGENTS_EXEC_MAX_CONCURRENT_POSITIONS", "4")
    monkeypatch.setenv("TRADINGAGENTS_EXEC_ALLOW_LIVE", "true")
    cfg = ExecutionConfig.from_env()
    assert cfg.per_name_pct == 0.1
    assert cfg.max_concurrent_positions == 4
    assert cfg.allow_live is True


def test_from_env_unset_uses_defaults():
    cfg = ExecutionConfig.from_env()
    assert cfg.top_k == 10


def test_from_env_invalid_bool_raises(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_EXEC_ALLOW_LIVE", "treu")
    with pytest.raises(ValueError, match="ALLOW_LIVE"):
        ExecutionConfig.from_env()


def test_from_env_invalid_int_raises(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_EXEC_TOP_K", "ten")
    with pytest.raises(ValueError, match="TOP_K"):
        ExecutionConfig.from_env()
