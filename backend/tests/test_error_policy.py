# -*- coding: utf-8 -*-
"""异常分级策略单测（★ P2-A，2026-10-18 ✓）。

守三件事：
  ① `swallow` **真的会留日志** ✓（否则它和 `pass` 没区别 ✗ —— 那就白改了 ✓）；
  ② `tolerate` 吞 `Exception` ✓，但**不吞** `KeyboardInterrupt` / `SystemExit` ✗；
  ③ `must_raise` **一定抛回** ✓（"该报的必须报"✓）。
"""
import pytest

from app.services import error_policy as EP


class _FakeLog:
    def __init__(self):
        self.calls = []

    def _rec(self, level, *a):
        self.calls.append((level, a))

    def debug(self, *a):
        self._rec("debug", *a)

    def info(self, *a):
        self._rec("info", *a)

    def warning(self, *a):
        self._rec("warning", *a)

    def error(self, *a):
        self._rec("error", *a)


@pytest.fixture
def fake(monkeypatch):
    f = _FakeLog()
    monkeypatch.setattr(EP, "_log", lambda: f)
    return f


def test_swallow_logs_with_scope(fake):
    EP.swallow("expr_cache.save", ValueError("boom"))
    assert fake.calls and fake.calls[0][0] == "debug"
    joined = " ".join(str(x) for x in fake.calls[0][1])
    assert "expr_cache.save" in joined          # 位置必须可见 ✓
    assert "boom" in joined                     # 原因必须可见 ✓


def test_swallow_level_and_extra(fake):
    EP.swallow("untrig_nav", RuntimeError("x"), level="warning", extra={"k": "v"})
    assert fake.calls[0][0] == "warning"
    assert "k" in " ".join(str(x) for x in fake.calls[0][1])


def test_tolerate_swallows_exception(fake):
    with EP.tolerate("cache.load"):
        raise ValueError("nope")
    assert fake.calls, "吞了却没留痕 ✗"
    assert "cache.load" in " ".join(str(x) for x in fake.calls[0][1])


@pytest.mark.parametrize("exc", [KeyboardInterrupt(), SystemExit(0)])
def test_tolerate_does_not_swallow_base_exceptions(fake, exc):
    """⭐ `KeyboardInterrupt` / `SystemExit` **必须穿过去** ✗（否则 Ctrl-C 都停不下来 ✗）。"""
    with pytest.raises(type(exc)):
        with EP.tolerate("loop"):
            raise exc
    assert not fake.calls, "BaseException 不该被当成可吞异常记录 ✗"


def test_tolerate_passes_through_on_success(fake):
    with EP.tolerate("ok"):
        pass
    assert not fake.calls


def test_must_raise_reraises_same_exception(fake):
    err = KeyError("missing")
    with pytest.raises(KeyError) as ei:
        EP.must_raise("accounting", err, detail="净值路径")
    assert ei.value is err, "必须抛回**同一个**异常对象 ✓（保栈 ✓）"
    assert fake.calls and fake.calls[0][0] == "error"
