"""PIT 回归：旧期重述、TTM 缺列与覆盖旧 bin 的行为。"""
import importlib.util
from pathlib import Path

import h5py
import numpy as np
import pytest


SPEC = importlib.util.spec_from_file_location(
    "_finance_pit_mapping", Path(__file__).parents[1] / "tools" / "dump_finance.py"
)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def day(value):
    return int(np.datetime64(value, "D").astype(np.int64))


def row(quarter, announcement, *, profit, revenue, equity, ocf, capex, **extra):
    return dict(quarter=quarter, info_date=announcement, if_adjusted=0,
                net_profit_parent_company=profit, net_profit=profit,
                revenue=revenue, gross_profit=revenue * 0.4,
                profit_from_operation=profit * 1.5,
                equity_parent_company=equity, total_assets=equity * 2,
                return_on_equity_weighted_average=4.0,
                basic_earnings_per_share=profit / 100,
                cash_flow_from_operating_activities=ocf,
                cash_paid_for_asset=capex, **extra)


def sample_rows():
    return [
        row("2020q1", "2020-04-30", profit=2, revenue=20, equity=80, ocf=10, capex=3),
        row("2020q4", "2021-03-20", profit=10, revenue=100, equity=100, ocf=40, capex=10),
        row("2021q1", "2021-04-27", profit=3, revenue=30, equity=120, ocf=15, capex=5),
        row("2019q4", "2022-03-01", profit=7, revenue=70, equity=70, ocf=21, capex=4),
    ]


def pit_file(tmp_path, rows):
    path = tmp_path / "000001.XSHE.h5"
    with h5py.File(path, "w") as f:
        for name in ("quarter", "info_date"):
            f[name] = np.asarray([r[name] for r in rows], dtype="S10")
        f["if_adjusted"] = [r["if_adjusted"] for r in rows]
        g = f.create_group("fields")
        for name in M.NEED_PIT:
            if any(name in r for r in rows):
                g[name] = [r.get(name, np.nan) for r in rows]
    return path


def daily_at(tab, field, dates):
    return M.expand_daily(tab, M.build_report_metrics(tab)[field], np.array([day(d) for d in dates]))


def read_bin(path, size):
    raw = np.fromfile(path, dtype="<f4")
    values = np.full(size, np.nan)
    start = int(raw[0])
    values[start:start + len(raw) - 1] = raw[1:]
    return values


def test_old_report_does_not_replace_current_period(tmp_path):
    tab = M.PitTable(pit_file(tmp_path, sample_rows()))
    dates = ["2022-02-28", "2022-03-01"]
    for field, expected in (("_equity", 120), ("_net_parent_ttm", 11),
                            ("fin_eps", 0.03), ("fin_rev_yoy", 50), ("fin_fcf_ttm", 33)):
        np.testing.assert_allclose(daily_at(tab, field, dates), [expected, expected])


def test_historical_revision_recomputes_ttm_only_when_disclosed(tmp_path):
    rows = sample_rows() + [row("2020q4", "2022-03-15", profit=-10, revenue=80,
                               equity=90, ocf=-10, capex=5)]
    tab = M.PitTable(pit_file(tmp_path, rows))
    dates = ["2022-03-14", "2022-03-15"]
    np.testing.assert_allclose(daily_at(tab, "_net_parent_ttm", dates), [11, -9])
    np.testing.assert_allclose(daily_at(tab, "fin_fcf_ttm", dates), [33, -12])
    np.testing.assert_allclose(daily_at(tab, "_equity", dates), [120, 120])
    np.testing.assert_allclose(daily_at(tab, "fin_eps_q", dates), [0.03, 0.03])


def test_future_reports_do_not_change_past_values(tmp_path):
    rows = sample_rows()
    before = M.build_report_metrics(M.PitTable(pit_file(tmp_path, rows)))
    rows.append(row("2021q1", "2025-01-01", profit=999, revenue=999,
                    equity=999, ocf=999, capex=0))
    after = M.build_report_metrics(M.PitTable(pit_file(tmp_path, rows)))
    for field in before:
        np.testing.assert_allclose(before[field], after[field][:-1], equal_nan=True)


def test_same_day_reports_use_newest_quarter_and_adjusted_version(tmp_path):
    rows = sample_rows()[:2]
    current = row("2021q1", "2021-04-27", profit=3, revenue=30, equity=120, ocf=15, capex=5)
    rows += [current, dict(current, if_adjusted=1, net_profit_parent_company=4),
             row("2020q4", "2021-04-27", profit=12, revenue=100, equity=100, ocf=40, capex=10)]
    tab = M.PitTable(pit_file(tmp_path, rows))
    np.testing.assert_allclose(daily_at(tab, "_net_parent_ttm", ["2021-04-27"]), [14])
    np.testing.assert_allclose(daily_at(tab, "_equity", ["2021-04-27"]), [120])


@pytest.mark.parametrize("vendor", [None, 777.0])
def test_pe_uses_cumulative_components_instead_of_vendor_ttm(tmp_path, vendor):
    rows = sample_rows()
    if vendor is not None:
        for r in rows:
            r["np_parent_company_ownersTTM"] = vendor
    source = pit_file(tmp_path, rows)
    out = tmp_path / "qlib"
    dates = np.array([day("2022-03-01")])
    M.dump_one(source, {"sz000001": (0, np.array([1100.0]))}, dates, out, True, False)
    feature = out / "features" / "sz000001"
    assert read_bin(feature / "fin_pe_ttm.day.bin", 1)[0] == pytest.approx(100.0)
    assert read_bin(feature / "fin_pb.day.bin", 1)[0] == pytest.approx(1100 / 120)


def test_q4_needs_no_prior_year_for_pe_or_fcf(tmp_path):
    tab = M.PitTable(pit_file(tmp_path, [sample_rows()[1]]))
    assert daily_at(tab, "_net_parent_ttm", ["2021-03-20"])[0] == 10
    assert daily_at(tab, "fin_fcf", ["2021-03-20"])[0] == 30


def test_missing_ttm_components_remain_nan(tmp_path):
    tab = M.PitTable(pit_file(tmp_path, [sample_rows()[2]]))
    assert np.isnan(daily_at(tab, "_net_parent_ttm", ["2021-04-27"])[0])
    assert np.isnan(daily_at(tab, "fin_fcf_ttm", ["2021-04-27"])[0])


def test_negative_profit_stays_negative_and_nonpositive_cap_is_missing(tmp_path):
    source = pit_file(tmp_path, [row("2020q4", "2021-03-20", profit=-10,
                                   revenue=100, equity=100, ocf=40, capex=10)])
    out = tmp_path / "qlib"
    dates = np.array([day(d) for d in ["2021-03-20", "2021-03-21", "2021-03-22"]])
    M.dump_one(source, {"sz000001": (0, np.array([1000.0, 0.0, -1.0]))}, dates, out, True, False)
    feature = out / "features" / "sz000001"
    np.testing.assert_allclose(read_bin(feature / "fin_pe_ttm.day.bin", 3), [-100, np.nan, np.nan])
    np.testing.assert_allclose(read_bin(feature / "fin_pb.day.bin", 3), [10, np.nan, np.nan])


@pytest.mark.parametrize("rows", [[], [sample_rows()[2]]])
def test_force_clears_old_bin_when_corrected_field_is_all_missing(tmp_path, rows):
    source = pit_file(tmp_path, rows)
    out = tmp_path / "qlib"
    target = out / "features" / "sz000001" / "fin_pe_ttm.day.bin"
    M.write_bin(target, 0, np.array([99.0]))
    dates = np.array([day("2022-03-01")])
    args = (source, {"sz000001": (0, np.array([1000.0]))}, dates, out)
    original = target.read_bytes()
    M.dump_one(*args, force=True, verify=True)
    assert target.read_bytes() == original
    M.dump_one(*args, force=False, verify=False)
    assert target.read_bytes() == original
    result = M.dump_one(*args, force=True, verify=False)
    assert result["_written"] > 0
    assert np.isnan(read_bin(target, 1)[0])


def test_verify_reports_current_quarter_within_calendar(tmp_path):
    source = pit_file(tmp_path, sample_rows())
    result = M.dump_one(source, {}, np.array([day("2022-03-01")]), tmp_path, False, True)
    assert result["_latest_quarter"] == "2021q1"
    assert result["_latest_info_date"] == "2021-04-27"
