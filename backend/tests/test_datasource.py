# -*- coding: utf-8 -*-
"""数据源抽象层单元测试：代码转换、工厂分发、回测引擎数据源解析。"""
import pytest

from app.models.backtest import BacktestRequest
from app.datasource.rqalpha_source import RQAlphaDataSource
from app.engine import qlib_engine


class TestCodeConversion:
    def test_to_file_code_stock_sz(self):
        assert RQAlphaDataSource._to_file_code("sz000001") == "000001.XSHE"
        assert RQAlphaDataSource._to_file_code("SZ000001") == "000001.XSHE"

    def test_to_file_code_stock_sh(self):
        assert RQAlphaDataSource._to_file_code("sh600000") == "600000.XSHG"
        assert RQAlphaDataSource._to_file_code("SH600000") == "600000.XSHG"

    def test_to_file_code_already_bundle(self):
        assert RQAlphaDataSource._to_file_code("000001.XSHE") == "000001.XSHE"

    def test_from_file_code(self):
        assert RQAlphaDataSource._from_file_code("000001.XSHE") == "sz000001"
        assert RQAlphaDataSource._from_file_code("600000.XSHG") == "sh600000"

    def test_roundtrip(self):
        for code in ["sz000001", "sh600000", "sz000300"]:
            f = RQAlphaDataSource._to_file_code(code)
            back = RQAlphaDataSource._from_file_code(f)
            assert back.startswith(code[:2])


class TestResolveDataSource:
    def test_qlib_uses_factory(self, monkeypatch):
        """默认 data_source=qlib 时走工厂，且 provider_uri 来自请求或数据源。"""
        class FakeDS:
            name = "qlib"
            provider_uri = "/fake/qlib"

        monkeypatch.setattr(qlib_engine, "get_data_source", lambda name: FakeDS())
        req = BacktestRequest(data_source="qlib")
        ds, uri = qlib_engine._resolve_data_source(req)
        assert ds.name == "qlib"
        assert uri == "/fake/qlib"

    def test_qlib_uri_prefers_request(self, monkeypatch):
        class FakeDS:
            name = "qlib"
            provider_uri = "/default"

        monkeypatch.setattr(qlib_engine, "get_data_source", lambda name: FakeDS())
        req = BacktestRequest(data_source="qlib", data_source_provider_uri="/explicit")
        _, uri = qlib_engine._resolve_data_source(req)
        assert uri == "/explicit"

    def test_rqalpha_rejected_for_backtest(self, monkeypatch):
        """rqalpha 可用于数据查询，但回测引擎当前拒绝（不静默降级）。"""
        class FakeRQ:
            name = "rqalpha"
            provider_uri = None

        monkeypatch.setattr(qlib_engine, "get_data_source", lambda name: FakeRQ())
        req = BacktestRequest(data_source="rqalpha")
        with pytest.raises(ValueError, match="仅支持 qlib"):
            qlib_engine._resolve_data_source(req)

    def test_unknown_source_rejected(self, monkeypatch):
        monkeypatch.setattr(qlib_engine, "get_data_source", lambda name: (_ for _ in ()).throw(KeyError("x")))
        req = BacktestRequest(data_source="unknown")
        with pytest.raises(ValueError, match="数据源"):
            qlib_engine._resolve_data_source(req)
