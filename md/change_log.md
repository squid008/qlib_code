# Changelog

本项目所有重要变更记录于此，格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)。
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)（后端 `backend/app/__init__.py` 定义，前端标题栏显示）。

## 变更日志索引（★ 分卷 + 横排 ✓）

> **当前系列 v2.10** 就在本文件下方 ✓；更早的按 `major.minor` 存档在 [`md/change_log/`](change_log/) ✓。
> 共 **6 块**、**6 行表格** ✓（原来是一列 **28 行** ✗ —— 2026-10-20 按「分块 + 横过来」重排 ✓）。

| 区块（由近及远） | 分卷 · 节数 |
|---|---|
| **v2.9**（刚归档 ✓ 静默 163→0 / 拆分 / 前端 181 条） | [`v2.9`](change_log/v2.9.md)·100 |
| **v2.0 ~ v2.8**（审计修复 / 撮合 / 事件研究） | [`v2.8`](change_log/v2.8.md)·10 ｜ [`v2.7`](change_log/v2.7.md)·1 ｜ [`v2.6`](change_log/v2.6.md)·1 ｜ [`v2.5`](change_log/v2.5.md)·2 ｜ [`v2.4`](change_log/v2.4.md)·5 ｜ [`v2.3`](change_log/v2.3.md)·1 ｜ [`v2.2`](change_log/v2.2.md)·5 ｜ [`v2.1`](change_log/v2.1.md)·86 ｜ [`v2.0`](change_log/v2.0.md)·1 |
| **v1.20 ~ v1.21**（公式体系 / 面板求值） | [`v1.21`](change_log/v1.21.md)·7 ｜ [`v1.20`](change_log/v1.20.md)·98 |
| **v1.18 ~ v1.19**（事件研究 / 净值口径） | [`v1.19`](change_log/v1.19.md)·99 ｜ [`v1.18`](change_log/v1.18.md)·70 |
| **v1.11 ~ v1.17**（早期小卷） | [`v1.17`](change_log/v1.17.md)·10 ｜ [`v1.16`](change_log/v1.16.md)·10 ｜ [`v1.15`](change_log/v1.15.md)·4 ｜ [`v1.14`](change_log/v1.14.md)·1 ｜ [`v1.13`](change_log/v1.13.md)·1 ｜ [`v1.12`](change_log/v1.12.md)·1 ｜ [`v1.11`](change_log/v1.11.md)·1 |
| **v1.3 ~ v1.10**（最早的几卷） | [`v1.10`](change_log/v1.10.md)·12 ｜ [`v1.9`](change_log/v1.9.md)·5 ｜ [`v1.8`](change_log/v1.8.md)·2 ｜ [`v1.7`](change_log/v1.7.md)·2 ｜ [`v1.6`](change_log/v1.6.md)·10 ｜ [`v1.5`](change_log/v1.5.md)·1 ｜ [`v1.4`](change_log/v1.4.md)·10 ｜ [`v1.3`](change_log/v1.3.md)·2 |

> ⚠ 维护规矩（写死 ✓）：
> · 每次升 **`major.minor`** 就把上一卷整个挪进 `md/change_log/` ✓ 并在上表对应块**加一格** ✓；
> · 主文件里的版本段**一律按语义版本降序** ✓（2026-10-20 整理时发现顺序是乱的 ✗，已重排 ✓）；
> · 同一版本**只留一份** ✓（整理时实测 101 段无重复 ✓）。

---

## [2.10.4] - 2026-10-20

### Fixed + Added（**第二件事「类型检查」开工** ✓ —— mypy 一上来就抓到一个真 bug ✓）

- **⭐ 真 bug（`attr-defined`）** ✓：`metrics._extract_result` 的两条**错误分支**写
  `result.message = ...` ✓，而 `BacktestResult`（pydantic v2）**原先没有 `message` 字段** ✗
  ⇒ 赋值直接 `ValueError: "BacktestResult" object has no field "message"` ✓
  ⇒ **错误分支自身崩** ✗：用户本想看到「指标提取异常：…」✓，实际拿到的是
  「has no field message」✗（一句和真正原因毫不相干的报错 ✓）。
  **为什么一直没被发现** ✗：那两条分支只在"指标提取失败 / 找不到 `report_normal`"时走到 ✓，
  正常路径**一次都不碰** ✓ ⇒ 常规跑测永远绿 ✓；是 mypy 把它揪出来的 ✓。
  ⇒ 修法：给 `BacktestResult` 补 `message: Optional[str] = None` ✓（**加性变更** ✓ 不影响现有字段 ✓），
  并加 `tests/test_backtest_result_message.py`（4 条 ✓）钉住"可赋值 / 可序列化 / 默认 None / 长文本不炸"✓。
- **另外两条 `mypy` 真问题** ✓：`float(initial_account)` 上 `Optional` ✗
  （`float(None)` 会 TypeError ✓）⇒ 改 `float(initial_account or 1.0)` ✓（下面算的是相对净值，
  基数只影响缩放 ✓）；`account = {}` 被推成 `dict[str, dict]` ✗ ⇒ 显式标 `dict` ✓。
- **闸门怎么接的** ✓（这次只做**关键切片**，不是全量 ✗ —— 全量 108 文件一次性严格化是
  "重写一遍"级别的工程 ✓）：
  · `backend/pyproject.toml` 加 `[tool.mypy]` ✓：`files = app/models + app/config.py +
    app/engine/metrics.py` ✓、`ignore_missing_imports = true` ✓、
    **`plugins = ["pydantic.mypy"]`** ✓；
  · **插件为什么必须有** ✗→✓：不启用时 mypy 把每个 `Field(...)` 当成"必填参数"✓
    ⇒ `BacktestResult()` 报 **12 条** `Missing named argument` **假阳性** ✗
    （这种噪声会让人把闸门关掉 ✓）。启用后同一份代码 **0 错误** ✓。
  · `.github/workflows/ci.yml` 新增步骤 `python -m mypy` ✓、并把 `mypy` 加进后端依赖 ✓；
  · 要求 **0 错误** ✓ —— 先把闸门做成"**如实失败**"✓，再按"只增不减"的棘轮思路往周边扩 ✓。
- **校验** ✓：`mypy` **0 errors（4 files）** ✓ ｜ 本文件 4 条 + 相关 20 条 **24 passed** ✓ ｜
  关键词回归（result/model/serial/api）**78 passed** ✓ ｜ `ruff` **0 错误** ✓ ｜ CI ✓（含新 mypy 步骤 ✓）。

## [2.10.3] - 2026-10-20

### Test（`_apply_node` **其余分支** 14 条 ✓ —— `panel_expr` 三份合计 **30.05% → 34.48%** ✓）

- 接着上一轮继续扫分支 ✓（`ROUND / BARSCOUNT / BARSSINCEN / HHVBARS / LLVBARS / And / Or /
  IdxMax / IdxMin / Rank / Quantile / Corr / Slope / Resi / Rsquare` ✓）。
- **断言策略：只钉性质，不猜实现** ✓ —— 上一轮已经因为"猜语义"红过好几次 ✗
  （`FILTER`/`Count`/`Greater`/`MA` 都不符合我的预期 ✓），所以这一批只断言**必然成立的性质**：
  · **有界** ✓：`HHVBARS/LLVBARS ∈ [1, N]` ✓、`IdxMax/IdxMin ∈ [1, N]` ✓、
    `Rank ∈ (0, 1]` ✓、`Quantile` 落在样本 `[min, max]` 内 ✓；
  · **是 0/1 信号** ✓（`And`/`Or` ✓），且 `And` 的交集**不多于**任一单条件 ✓；
  · **不抛、形状对、无 `inf`** ✓（`Corr/Slope/Resi/Rsquare` 一族 ✓）。
- **⭐ 三条被实测纠正的签名/口径**（都写进注释 ✓，下次不用再猜 ✗）：
  1. `IdxMax/IdxMin/Rank/Slope/Rsquare/Resi` 都是 **`(序列, N)`** ✓ —— 少给 N 直接
     `IndexError: list index out of range` ✗；`Quantile` 是 **`(序列, N, q)`** ✓ —— 少给 N
     `TypeError: cannot convert the series to int` ✗；
  2. **`IdxMax/IdxMin` 返回 1-based**（`1..N` ✓），不是 0-based ✗（与 qlib 口径一致 ✓）；
  3. **`Rank` 返回归一化排名**（`1 / 0.5 / 0.333` ✓ 落在 `(0,1]` ✓），不是名次整数 ✗。
- **✗ 一个必须写清的边界** ✓：这一批**没有**钉住这些算子的**精确数值语义** ✗（只钉形状与性质 ✓）。
  文件头已注明 ✓ —— 不假装测了 ✗。要钉精确值，得先逐个通读实现 ✓（本轮不做 ✓）。
- **实测** ✓：`test_panel_expr_ops` + `test_panel_expr_eval` + `test_panel_expr_eval_more`
  三份合计覆盖该模块 **30.05% → 34.48%** ✓（1,421 语句 ✓），**57 passed** ✓。
- **校验** ✓：14 passed（3.4 秒 ✓）｜ `ruff` **0 错误** ✓ ｜ CI ✓。

## [2.10.2] - 2026-10-20

### Test（**合成面板跑真求值 25 条** ✓ —— `panel_expr` 两份合计覆盖 **14.14% → 30.05%** ✓）

- **做法**：`PanelEvaluator.__init__` 要 qlib 日历 ✗ ⇒ 用 `object.__new__` 手工装配合成面板 ✓
  （`_full` + `_field_cache` + 三个缓存 ✓，与 `test_chip_field.py` 同套 ✓），
  然后 `ev.eval(parse_expr(expr))` ✓ —— **走的正是线上 `eval_expr` 那条路** ✓。
- **⭐ 三条我原先假设错、被实测纠正的事实**（都写进注释 ✓）：
  1. **`eval` 认规范化算子名**（qlib 风格 ✓）：窗口只有 `Mean/Sum/Std/Var/Max/Min/Count` ✓、
     比较是 `Gt/Lt/Ge/Le/Eq/Ne` ✓ —— **`MA`/`HHV`/`LLV`/`Mod`/`Cross` 在这里不存在** ✗
     （`MA` 由上层 codegen 映射 ✓）；我第一次写 `MA($close,3)` 直接 `ValueError` ✓；
  2. **`>` 不能写在函数实参里** ✗ —— `Count($close>2,3)` 报「期望 , 或 )」✓ ⇒ 条件要写 `Gt($close,2)` ✓；
  3. **`Greater` 是逐元素取大**（`_BIN_ELEM` ✓），**不是比较** ✗ —— 比较必须用 `Gt` ✓。
- **⭐⭐ 抓到一个真实陷阱：跨公式复用节点缓存会串味** ✗ ——
  `parse_expr` 每次 `_CANON.clear()` ⇒ 节点 key **从 0 重排** ✓ ⇒ 结构不同的公式会**撞 key** ✓
  ⇒ 不清缓存就会把**上一个公式的结果**当前公式的 ✓（实测：`Gt($close,3)` **原样返回了** `Pow($close,2)` 的数组 ✓）。
  这解释了生产 `eval_expr` 里"每个表达式都清 `_node_cache`"那段代码 ✓（那里写的理由是**内存** ✓，
  而这里补上了**正确性**理由 ✓）。⇒ 已固化成 `test_cache_must_be_cleared_between_different_formulas` ✓。
- **覆盖到的语义** ✓：`Mean/Sum/Std/Var/Max/Min` 与 pandas `rolling(3, min_periods=1)` **逐位对拍** ✓；
  `Ref` 与 `groupby.shift` 对拍 ✓；`Abs/Sqrt/Log/Sgn/TRUNC` 数值对拍 ✓；`Pow` ✓；
  `If/BETWEEN`（含边界 ✓）/`BARSLAST`（从未成立 ⇒ **全 0** ✓）/`BARSSINCE` ✓；
  `EMA` 与 `EMA_TDX` **首值相同、尾部靠拢、始终不越界** ✓；`SMA` **首值 = 首值、单调、且滞后** ✓；
  `sr=True` 返回**压缩序列**（停牌日**不在 index 里** ✓，回填由 `eval_expr` 做 ✓）✓；
  节点缓存命中返回**同一对象** ✓ 且字节记账同步 ✓。
- **✗ 两处我主动收敛**（不写自己都没吃透的断言 ✗）：`FILTER` / `Count` 的实际输出与我的假设不符 ✓
  ⇒ 只断言"**是合法信号序列**（0/1、长度对、计数落在 `[0,N]`）" ✓，具体抑制语义**记为待查** ✓。
- **实测** ✓：`test_panel_expr_ops` + `test_panel_expr_eval` 两份合计覆盖该模块 **30.05%** ✓
  （1,421 语句 ✓；单 ops 时是 14.14% ✓）；**43 passed** ✓。
- **校验** ✓：25 passed（2.7 秒 ✓）｜ `ruff` **0 错误** ✓ ｜ CI ✓。
- **下一步（①覆盖率继续）** ✓：`ops_ext` 的纯 numpy 层补 `_seg_window_op` / `_dyn_bars` 等 ✓；
  再往上是 `single_test` 巨型聚合 ✗（需 mock 数据集 ✗，与 `_run_single` 同类 ✓）。

## [2.10.1] - 2026-10-20

### Test（**面板底层算子对拍 18 条** ✓ —— 第一件事「覆盖率」开工 ✓）

- 目标来自上一轮评分：**覆盖率 58.23% → 70%** ✓。按"未覆盖行数"排序，`panel_expr.py` 是最大头
  （**1,028 行** ✗），但它可以用**合成面板直接测模块级算子** ✓ —— 不必构造整个求值器 ✓。
- 五个被性能重写过的算子（注释都承诺"**逐位相同**"✓）⇒ 这里就用**朴素 `groupby` 写法对拍** ✓
  （这才是能钉住重写正确性的方式 ✓）：
  · **`_ref`** ✓：与 `groupby.shift` 逐位一致 ✓；`k<0` = 未来 ✓；**`sr=True` 剔停牌后 shift** ✓
    （停牌日仍是 NaN ✓ 且**不占窗口** ✓ —— 写成普通 rolling 会差得很隐蔽 ✗）；
  · **`_roll`** ✓：`mean`/`max`/`min` **三种**分别与 `groupby.rolling(2, min_periods=1)` 对拍 ✓；
    **窗口不足按已有数据算** ✓（否则开头 N−1 行全 NaN ✗）；`sr=True` 时停牌不占窗口 ✓
    （构造 `[1, NaN, 3]` ⇒ 第 3 行窗口 = `[1,3]` ⇒ 均值 2 ✓）；
  · **`_sma_apply_series`** ✓：手算三行核对递归式 `Y=(M·X+(N−M)·Y_prev)/N` ✓；
    **`M=0` ⇒ 全 NaN** ✓（不是原值、也不是 0 ✗）；**`M>N` 夹到 N** ✓（否则 alpha>1 发散 ✗）；
  · **`_ema_apply_series`** ✓：qlib 分支 `N≥1 ⇒ ewm(span=N)` ✓、`0<N<1 ⇒ ewm(alpha=N)` ✓；
    **`EMA_TDX` 必须 `adjust=False`** ✓（`adjust=True` 只在序列开头有差 ✗ 最难发现 ✓）；
    `N=0` 的手写 expanding 指数加权**逐值核对** ✓（权重 `a=1−2/(1+n)` 递减 ✓）；
  · **`_align`** ✓：同一 index **对象** ⇒ 直接返回（不复制 ✓ v1.18.33 优化 ✓）；
    不同行集 ⇒ **并集**并补 NaN ✓（不许丢掉某只票的日期 ✗）。
- **实测** ✓：本文件单独跑 `panel_expr` 覆盖 **14.14%** ✓（5 个算子已全覆盖 ✓；
  该模块 1,421 条语句里大头在 `PanelEvaluator.eval` 与 `_apply_node` ✗）。
- **校验** ✓：18 passed ✓（2.6 秒 ✓）｜ `ruff` **0 错误** ✓ ｜ CI ✓。
- **下一步（同一件事继续）** ✓：给 `PanelEvaluator` 造**合成面板**（monkeypatch `_union_layout`
  + 预填 `_field_cache` ✓）⇒ 才能真正覆盖 `eval` / `_apply_node`（约 **370 行** ✓，
  也是"公式语义"最核心的部分 ✓）。

## [2.10.0] - 2026-10-20

### Chore（**收尾：基线全面刷新** ✓ —— 这一整段（2.9.89~2.9.99）的账结清 ✓）

- **全部取实测值** ✓（跑了一次**本地全量**：**1477 passed / 1 分 59 秒** ✓）：
  · 总覆盖率 **57.80% → 58.23%** ✓（18,922 语句 / 未覆盖 7,903 ✓）；
  · 后端用例 **1392 → 1477**（+85 ✓，全部**脱数据脱模型** ✓）。
- **这一段的补强账** ✓（每一条都有"坏了会怎样"的注释 ✓）：
  | 模块 | 覆盖 | 条数 | 守的是什么 |
  |---|---|---|---|
  | `engine/charts.py` | **3.3% → 79.7%** | 4 | 三个 PNG **真的生成**且是合法 PNG ✓（异常全被吞 ✓ 碎图只能靠测试发现 ✗） |
  | `engine/inst_mask.py` | **9.1% → 85.2%** | 8 | **区间外必须 False** ✓（防"彼时尚未纳入"的前视 ✗） |
  | `signals/parsers`（整包） | **→ 65.1%** | 10 | 三种模式识别 / GBK / 二进制必须给人话 ✓ |
  | `engine/analysis.py` | **→ 84.7%** | 11 | **有 `ret` 就必须用 `ret`** ✓ / 复利 ✓ / `long_average` 用池内等权 ✓ |
  | `signals/pricing.py` | **8.5% → 21.4%** | 7 | `fill_limits` 只补缺的一侧 ✓ / 幅度按板块 ✓ |
  | `engine/artifacts.py` | **→ 30.7%** | 12 | 复用守卫"**没有戳也挡**" ✓ / `NaN` 清洗 ✓ |
  | `routers/export_xlsx.py` | **30.7% → 36.5%** | 20 | 截图只收 **PNG + 4MB** ✓ / 文件名七个非法字符 ✓ |
  | `engine/metrics.py` | **→ 45.5%** | 9+10 | 基准归一到 1.0 ✓ / **曲线保留全程（审计 T2）** ✓ |
  | `signals/engine.py` | **→ 90.2%** | 14 | 拒单**轮转**不饿死靠后方案 ✓ / `NaN→None` 断线 ✓ |
  | `factors/event_study.py` | **→ 54.3%** | 10 | **`lag=1` 买 T+1 卖 T+2** ✓（差一格整条平移 ✗） |
- **★ 顺手修掉两个真 bug** ✓（都是"界面正常、数字错"那一类 ✗）：
  1. **`_inst_codes`**：`pd.factorize` 对缺失值返回 **`-1`** ✗ ⇒ `ups[codes]` 索引到**最后一个代码** ✗
     ⇒ **NaN 行被当成某只真实股票** ✓（而注释承诺 `NaN → 'NAN'` ✗）⇒ 已按 `-1` 遮罩修复 ✓，
     并用**老写法对拍**钉住逐元素等价 ✓；
  2. **`fill_limits`**：函数头判"两侧都在才算有标签"✓，但循环里**无条件写两列** ✗
     ⇒ "只给 `LIMIT_UP`、缺 `LIMIT_DOWN`"时**已有那侧被推算值覆盖** ✗ ⇒ **ST 的 5% 会被改成 10%** ✓
     ⇒ 已改成"只补缺的那一侧" ✓。
- **✗ 三个我反复犯的错（已写进注释，避免第四次）** ✗：
  ① **断言消息里嵌 ASCII 双引号** ⇒ `SyntaxError`（本段 **3 次** ✗）⇒ 统一用「」✓；
  ② 各种**夹具假设**被实测纠正（`label_cols` 以 `int` 为键 ✓、`BacktestResult.nav` 默认 `None` ✓、
     基准每点 round 6 位 ✓、`pd.bdate_range` 只产工作日 ✓、周末区间不贡献交易日 ✓）；
  ③ 报覆盖率时**只报能验证的数字** ✓（取不到就明说"未取到"✗ 不编 ✗）。
- **基线已刷新** ✓（《开发记录》顶部：版本 / 用例数 / 覆盖率 / 门槛 / 各模块覆盖 / 条数 /
  两个真 bug / 五条"有意停手"✓ —— 下次开工先看那几行 ✓）。
- **校验** ✓：本地全量 **1477 passed** ✓ ｜ `ruff` **0 错误** ✓ ｜ 版本三处 + lock 同步 ✓ ｜ CI ✓。

