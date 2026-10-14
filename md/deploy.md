# Qlib 量化回测平台 - 部署指南

把本平台从开发机拷贝到新电脑的完整步骤。按顺序操作。

---

## 一、需要拷贝/准备的清单

| 项 | 是否已准备好 | 说明 |
|---|---|---|
| `qlib_code` 整个文件夹 | ✅ | 项目本体（含后端/前端/脚本/依赖清单） |
| `requirements_qlib.txt` | ✅ 已生成 | qlib 环境完整 pip 依赖（**主力安装清单**） |
| `qlib_env.yml` | ✅ 已生成 | conda 环境清单（参考，conda 无法锁定 pip 包） |
| `vs_BuildTools.exe` | ✅ | 编译 C 扩展需 MSVC 工具链 |
| 数据包 `qlib_cn_1/2.tar.gz` | ✅ | A股 qlib 数据 `cn_data`（GitHub Release `data-2026-09-05` 两分卷，下载解压合并，见第四节） |
| `<你的qlib源码目录>` 源码 | ⚠️ 需确认 | **qlib 是源码安装的**（见下文第三步） |

---

## 二、新电脑需要安装的软件

1. **Anaconda / Miniconda**（Python 环境管理）
2. **Node.js ≥ 18**（前端运行必需）
3. **VS BuildTools**（编译 LightGBM 等 C 扩展，已有 vs_BuildTools.exe）

> 验证：`conda --version`、`node -v`、`npm -v`

---

## 三、搭建 Python 环境（重点）

你的 qlib 是 **源码方式安装**（`pip install -e <qlib源码目录>`），不是 pip 的 pyqlib。
`requirements_qlib.txt` 里有一行 `-e <qlib源码目录>`。

### 方式 A：源码方式（推荐）
1. 把 `<qlib源码目录>` 一起拷贝到新电脑
2. 创建并激活环境：
   ```bash
   conda create -n qlib python=3.10 -y
   conda activate qlib
   ```
3. 安装依赖：
   ```bash
   # 先去掉 requirements_qlib.txt 里的 "-e <qlib源码目录>" 这一行（路径不对）
   pip install -r requirements_qlib.txt
   # 再以源码方式安装 qlib（把路径改成新电脑实际的 qlib 源码位置）
   pip install -e D:\你的qlib源码目录
   ```

### 方式 B：pip 安装 pyqlib（更省事，但版本可能有差异）
```bash
conda create -n qlib python=3.10 -y
conda activate qlib
# 去掉 requirements 里的 "-e <qlib源码目录>" 行后
pip install -r requirements_qlib.txt
pip install pyqlib
```
> 注意：pyqlib 的 API 和你现在的源码版可能有细微差异，若回测报错优先用方式 A。

### 关键依赖确认（requirements_qlib.txt 已含）
- lightgbm==4.7.0、matplotlib、scikit-learn、scipy、fastapi、uvicorn、pydantic

---

## 四、部署数据

1. 下载 A股 qlib 数据包（`cn_data`，**两个分卷都要下，缺一不可**），二选一：

   **方式1：GitHub Release 下载（推荐）**
   打开 https://github.com/squid008/qlib_code/releases/tag/data-2026-09-05 ，下载两个分卷：
   - `qlib_cn_1.tar.gz`（987.5 MB）
   - `qlib_cn_2.tar.gz`（1289.1 MB）

   **方式2：命令行下载（需 gh CLI 且已登录 GitHub）**
   ```bash
   gh release download -R squid008/qlib_code data-2026-09-05 -D data
   ```

2. 把两个分卷**都**解压到同一 `qlib_code\data` 目录，自动合并为完整 `cn_data`：
   ```bash
   cd qlib_code
   tar -xzf qlib_cn_1.tar.gz -C data
   tar -xzf qlib_cn_2.tar.gz -C data
   ```

3. 数据路径通过 **环境变量** 或 **目录约定** 指定（不用改代码）：

   **方法1（推荐）设环境变量：**
   ```bash
   set QLIB_PROVIDER_URI=D:\你的数据路径\cn_data
   ```

   **方法2：放到项目目录下**
   把 `cn_data` 放到 `qlib_code\data\cn_data`（后端会自动识别）

   **方法3：放到当前用户主目录**
   `C:\Users\你的用户名\.qlib\qlib_data\cn_data`

> 配置优先级：环境变量 > 项目内 data/cn_data > 主目录 .qlib

### 资金流向（moneyflow）数据（v1.7.1+，资金流/L2 因子用）

资金流字段共 **45 个 bin**（净额/占比 × 净+买卖方向 + 量）随 `cn_data` 存放：
`cn_data/features/<代码>/mf_*.day.bin`（约 5,2xx 只 × 45）。
它们**不在 git 仓库**（`data/` 已被 .gitignore），部署/拷贝方式二选一：

- **方式 A（推荐）**：拷贝源 h5 目录 `E:\rq\moneyflow3\`（含 `sid.h5` + `mf_2013~mf_2026.h5`，2013-01-04 ~ 2026-08-19，4 档买卖分开的量与金额）后执行：
  ```bash
  python backend/tools/dump_moneyflow.py            # 默认写入 D:\quant\qlib_code\data\cn_data
  python backend/tools/dump_moneyflow.py --qlib-dir <你的cn_data路径>   # 自定义数据目录
  ```
  脚本自动检测源结构（moneyflow3 原始买卖 / 旧 net 字段）；旧源仅导出兼容净额字段。
- **方式 B**：打包/拷贝 `cn_data/features/` 下所有 `mf_*.day.bin`（保持目录结构），解压到目标 `cn_data/features/`。

字段（dump_moneyflow.py 自动幂等：已存在跳过，--force 全量重写）：
- 净额/净占比 10：`mf_amount_<main|xl|l|m|s>`、`mf_pct_<main|xl|l|m|s>`（万元 / %）
- 买卖方向 20：`mf_amount_<档>_b/_s`、`mf_pct_<档>_b/_s`
- 量（手）15：`mf_vol_<main|xl|l|m|s>`（净流入量）+ `mf_vol_<档>_b/_s`（买卖量）→ 供 `L2_VOL`

**数据更新（服务器每天跑）**：更新行情日历后执行 `python backend/tools/dump_moneyflow.py --force`（幂等全量重写）即可补新交易日资金流；若行情 bin 重建（日历重排/前插日期），需与 moneyflow 基于同一天历一起重 dump，否则字段错位。

- ⚠ **东财源止于 2026-08-19** ✓：此后由 **tushare `moneyflow`** 续 ✓（`backend/tools/dump_tushare_mf.py` 先与 08-19 逐字段对拍再写 ✓），
  续尾的洞/尾用 `backend/tools/fill_mf_gap.py` 补 ✓（判据「`volume>0` 却没有 `mf`」✓，**带重叠闸门** ✓）。
- ★★ **有 4 只是"东财 + tushare 两源拼接"** ✓（`sh600984`/`sz002084`/`sz002445`/`sz300176`，2026-10-14 用户拍板放行 ✓）：
  它们**对拍不过闸门** ✗（与东财段重叠处最大相对差 **1184.7% / 12.1% / 22.9% / 12.3%** ✓ = 两家系统性口径差 ✓）
  ⇒ **用 `mf_*` 做因子/回测前先读** `data/<ds>/features/_mf_source_notes.json` ✓（逐条记明区间 + 差异 ✓；
  由 `fill_mf_gap.py --override-gate` **自动写** ✓，代码里绑死 ✓）。细表见 `md/数据源接入.md` ✓。

> 校验：`mf_amount_main = mf_amount_xl + mf_amount_l`、`mf_vol_<档> = mf_vol_<档>_b − mf_vol_<档>_s`（勾稽）；源 h5 vs bin 逐日比对已一致（开发机验证）。

### 物化字段：前复权价（`pre*`）+ 筹码（`chip_*`）—— **每台机器必须各做一次**

这两组是**本项目自造的派生字段**（`data/` 已被 .gitignore 排除）⇒ **既不在数据包里、也不在 git 里**，
必须在目标机器上各生成一次。⚠ **缺了不报错、会静默失效**（最坑的地方）：

| 缺失字段 | 后果 |
|---|---|
| `preclose / preopen / prehigh / prelow / prevwap` | **`forward`（前复权）失效** ⇒ 价格量纲因子被按**后复权价**排序（LLT K=20 年化 +9.03% ↔ 米筐 −5.24%，见 change_log `[1.19.96]`） |
| `chip_cost_{5,30,75,95}` / `chip_win_{close,high,low}` | 用 `COST()/WINNER()` 的公式（过顶 / 黏合强突破 / 蹦极新生 …）**静默返回全 NaN**（`panel_expr._chip_or_bin` 优先读 bin、**不检查文件是否存在**） |

生成与核对（脚本在 **`backend/tools/`**，与 `dump_moneyflow.py` 同处；cwd 任意；实测耗时：`pre*` 约 8 分钟、`chip_*` 约 6~9 分钟）：

```bash
python backend/tools/build_preclose.py                  # 前复权：5 字段 × ~6141 只
python backend/tools/materialize_chip.py 400            # 筹码：7 字段 × ~6141 只（分批；可中断续跑）
python backend/tools/materialize_chip.py 400 --overwrite # ★ 全池**重算**（口径变过 / 修了口径之后必须用）
python backend/tools/verify_materialized.py             # 核对覆盖率/同轴/NaN/数值/**口径戳/展开比**；退出码 0 才算好
```

- 两者默认**只补缺失** ⇒ ⚠⚠ **`materialize_chip.py` 不加 `--overwrite` 时，对"已有 `chip_cost_95` 的股票
  一律跳过"** ✗ —— 所以**修了筹码口径之后直接跑它等于什么都没做** ✗（会打印"无需物化（都已有）" ✓）。
  这是最容易漏的一步 ✗（v1.20.44 修换手率单位时就踩过 ✓）；
- ⚠ **行情数据更新后必须重跑 `build_preclose.py`**（`factor_last` 变 ⇒ 整条历史价缩放）；
- ⚠ `materialize_chip.py` **必须分批**（默认 400 只/批）：`chip_store.materialize` 一次吃全池会构造
  "全池一次性面板" ⇒ 实测 **20 分钟 0 个文件、CPU 仅 ~22% 单核、ETA 不可估**（2026-09-19 实测教训）；
- 口径：`pre* = 源字段 / factor_last`（逐位；`factor_last` = `factor.day.bin` 末值，五字段共用 ⇒ 不会 `prehigh < prelow`）；
  `chip_*` 必须与 `$close` **同轴**（首值+长度一致，否则多股票一起加载会报 `identically-labeled`，见 change_log `[1.19.91]/[1.19.92]`）。

### 物化字段：基本面（`fin_*` 米筐源 / `ftdx_*` 通达信源）—— **每台机器必须各做一次**

`FINANCE(q[,口径])` 用的是**物化出来的字段**（不在数据包里、也不在 git 里）：

```bash
python backend/tools/dump_finance.py                    # 米筐源（E:/rq/finance/pit + 市值）→ fin_*（累计 10 + 三档 24）
python backend/tools/dump_tushare_finance.py --qlib-dir data/cn_data2   # tushare 源 → 同一批 fin_* 字段
python backend/tools/dump_finance_tdx.py                # 通达信源（D:/new_tdx/vipdoc/cw）→ ftdx_*（多源复核用）
python backend/tools/dump_finance_tdx.py --verify --codes sz000001,sh600519   # 两源交叉校验
python backend/tools/dump_finance_tdx.py --force        # 改了口径后全量重写
```

- ★ 2026-10-10（v1.21.4）起 `FINANCE(q)` 默认取**单季度**，另有 `FINANCE(q,2)` 年化 / `FINANCE(q,3)` TTM ⇒
  每个 q=3..10 都物化 `_q` / `_ann` / `_ttm` **三档**（共 24 个字段/只 ✓）；**两条链共用同一份派生公式** ✓；
- 米筐源需要 `E:/rq/finance/pit/*.h5`（**有公告日** ⇒ 严格按公告日对齐 ✓）；
- ⚠ **`--qlib-dir` 一定要写**（`build_preclose.py` 也支持 ✓，2026-10-10 加）：这些工具的默认目录来自
  `app.config.QLIB_PROVIDER_URI` = **当前生效数据集** ⇒ 修 `cn_data` 时若"当前生效"是 `cn_data2`，
  就会**写错数据集** ✗（历史事故：`cn_data` 的 `pre*` 变成了 cn_data2 的副本 ✓）；
- ⚠ **数据集路径的优先级（2026-10-10 改）**：**落盘的 `data/active_dataset.json`（UI 切换）优先** ✓，
  其次环境变量 `QLIB_PROVIDER_URI`；要"环境变量强制覆盖"（容器/CI）请显式设 `QLIB_PROVIDER_URI_FORCE=1` ✓。
  两者冲突时启动会打一行告警 ✓（历史上"环境变量优先"导致 UI 切换**静默不生效**、对照实验得出"两边一样"的假象 ✗）。
- 通达信源需要 `D:/new_tdx/vipdoc/cw/gpcw*.zip`（会员"财务数据下载"）；
  ⚠ 它**没有公告日** ⇒ 可用日按**法定披露截止日**推定（一季报 4/30、中报 8/31、三季报 10/31、年报次年 4/30），
  **保守：宁晚不错** ⇒ 同一交易日两源可能差一个报告期（对比时务必对齐报告期 ✓）；
- ⚠ `FINANCE_TDX(q)` 函数本身**已下线**（2026-10-10 ✓）—— `ftdx_*` 数据仍照常 dump，只作**多源复核** ✓；
- 口径戳：`features/_finance_meta.json`（含 `tier_fields` / `tier_note` ✓）/ `features/_finance_tdx_meta.json`（重跑后自动刷新 ✓）；
- ⚠ 两个 dump 工具写盘成功后会自动**落数据戳**（`feature_cache.bump_data_version` ✓）⇒ 否则面板缓存继续"有效" ✗。
- ⚠⚠ **用 `--codes` 局部重跑 tushare 链时，务必带 `--mc-tushare`** ✗→✓（2026-10-13 实测踩到 ✓）：
  不带它时 `fin_pe_ttm`/`fin_pb` 的分母只能吃到**本地 `market_cap` bin** ⇒ 实测 1500 只的 PE/PB
  **掉回米筐日历末日 2026-08-21** ✗（带参数重跑即恢复 ✓，且缓存命中 ⇒ **零 API 调用** ✓）。
- ⚠ **PIT 去重规则（2026-10-13 修 ✓）**：同一 (报告期, 公告日) 多行时，**非空值永不被空值行覆盖** ✓ ——
  tushare `fina_indicator` 对不少股票会返回"一行有 `roe_waa` ✓ + 一行 null ✗"的重复行，且两行
  `update_flag` 都缺 ⇒ 旧规则"相等则后者胜"把有值行顶掉 ✗（实测 `fin_roe` 因此对 **325 只活跃股**
  停在 2026 中报公告日 ✗，历史里另有 1570 只的**中间洞** ✗）。回归测试：`backend/tests/test_tushare_pit_dedup.py` ✓。
- ★ **`fin_roe` 缺则自算兜底（2026-10-13 定 ✓，默认开 ✓）**：`roe_waa` 缺失时
  `fin_roe = 累计归母净利 ÷ 期末归母权益 × 100` ✓，**硬门槛 权益 > 0** ✓（资不抵债 ⇒ 分母为负
  ⇒ 数值反向、毫无意义 ✗ ⇒ 保持 NaN ✓）。开关 `--roe-fill-missing`（默认开）/ `--no-roe-fill-missing`（复现旧结果 ✓）；
  兜底格子会记进 `features/_finance_warnings.json`（`roe_filled_cells` / `roe_filled_codes` ✓）。
  **Why**：tushare 的 `roe_waa` 覆盖与"亏损/资不抵债"强相关 —— 2026 中报四象限实测
  （`ai_test/fin39_probe.py` ✓）：净资产<0 的 108 只中 **80 只无 roe** ✓，净资产>0 的 5569 只中**仅 12 只无** ✓。
  实测效果：39 只缺口 → **31 只**（剩下的正是 31 只净资产为负 ✓，保持 NaN 正确 ✓）。
  回归测试：`backend/tests/test_tushare_roe_fallback.py` ✓。
  ⚠ 该兜底是**全量规则**（任何股票、任何报告期只要 `roe_waa` 缺、权益>0 就补 ✓）——
  **2026-10-13 已做全市场全量重跑** ✓（用户："全跑一次看看" ✓）：5908 只有数据 / **零 API** /
  615 秒 / 0 失败，实测兜底 **3753 只 / 36,792 格**（含历史中间洞 ✓）；全市场快照前后对比 ✓：
  原有 bin **消失 0** ✓、计数下降**仅 1 条** ✓（`sz000047`，2002 年退市股、旧 2001 值不复现 ✓）、
  上升**只有** `fin_roe`/`fin_pb`/`fin_pe_ttm`（后两者是北交所 920 段 + `sh689009` 的"29 天尾巴"
  补成完整序列 ✓）、抽样**值→NaN 0 格** ✓。
  ⚠ 依赖 `FINANCE(6)` 的因子/回测在**历史区间**会有变化 ✓（原来 NaN 的格子现在有值 ✓）。

### 数据修复 / 体检工具（2026-10-13，v2.1.96 ✓ —— 全部**带闸门 + 备份 + 零 API 或极少量调用** ✓）

```bash
python backend/tools/build_index_turn.py --qlib-dir data/cn_data2            # 指数 turn：有源重建 / 无源置空
python backend/tools/rebuild_turn.py     --qlib-dir data/cn_data2 --apply     # ★ 个股 turn 全量重建（daily_basic.turnover_rate）
python backend/tools/fill_market_cap.py  --qlib-dir data/cn_data2            # market_cap 补到 close 末日
python backend/tools/fill_vwap_tail.py   --qlib-dir data/cn_data2            # vwap/prevwap 按洞补 + 整条建
python backend/tools/fill_amount_gap.py  --qlib-dir data/cn_data2            # amount 共同窗口缝补（按天取数）
python backend/tools/fill_amount_history.py --qlib-dir data/cn_data2 --apply  # amount 个股整条历史（退市股 ✓，按股票取数）
python backend/tools/materialize_market.py --overwrite                        # ★ mkt_* 横向统计重新物化（2026-10-14 跑过 ✓）
# 全部默认 dry-run ✓，加 --apply 才写 ✓；各自把旧 bin 备份到 data/<ds>/_backup_*_20261013/ ✓
python ai_test/health_both_1013.py --datasets cn_data,cn_data2               # ★ 两套数据集全面体检（本地）
python ai_test/hole_probe_1013.py --dataset cn_data2                         # 空洞形态（洞 vs 尾巴 ✓）
```

- ⚠ **"缺口"的正确定义** ✓：`该字段绝对末日 < 该股自己的 close 绝对末日` ✓ ——
  用「< 日历末日」会大批误报退市股 ✗；用「全股票取 max」会把个股落后**藏起来** ✗（两处大缺口因此漏了很久 ✓）。
  比的时候**必须带 header** ✓（各字段 header 不同 ✗，`market_cap`/`vwap` 就是被这两条掩盖的 ✓）；
- ⚠ **"洞"和"尾巴"是两种病** ✗：补尾巴的脚本补不了中间洞 ✓（`vwap` 的 62 天洞就是补完 `amount` 后才暴露的 ✓）；
- 现状与已知缺口（北交所无源 / 中证800·全指无 `turn` 源 / 米筐侧未同步 / `mkt_num_all` 待定 等）✓
  已逐条写进 `md/数据源接入.md` 的「★ 两套数据集的现状与已知缺口」一节 ✓（同事的 AI 直接读那节 ✓）。

### 数据修复：`cn_data` 的 `open/high/low`（2026-10-10，v1.21.5）

```bash
python backend/tools/rebuild_cn_data_ohlc_from_bundle.py            # dry-run（默认 ✓，先看闸门）
python backend/tools/rebuild_cn_data_ohlc_from_bundle.py --apply    # 写盘（旧 bin 备份到 ai_test/ ✓）
python backend/tools/build_preclose.py --qlib-dir data/cn_data --overwrite   # 用自家价重算 pre* ✓
```

- **背景**：`cn_data` 的 `open/high/low` 曾被写成 `close`（全池 95.8% 行四值相同 ✗）⇒ 用日内高低点的
  公式全失真（`黄金坑启动` 触发 6 ↔ 3691 ✓）；`pre*` 也曾是另一套数据的副本（混源 ✗）。
- 口径：**米筐 bundle 原始 O/H/L × `$factor`**（与 `$close` 同式 ⇒ 同源 ✓，实测中位相对差 3.4e-08 ✓）；
  bundle 日频只从 **2005-01-01** 起 ⇒ 更早的行**保留原值**并汇报（**不拿另一套数据补** ✗）。
- 跑完会**自动落数据戳** ✓（`build_preclose` 那步请再补落一次或跑 `bump_data_version` ✓）。

### 物化字段：横向统计（`mkt_*` —— `BLOCKSETNUM` / `INSUM`）—— **每台机器必须各做一次**

通达信那种"**横向**（跨股票）统计"（板块当日股数、板内某公式输出的横向聚合，例如 A 股"黄金坑纯度"）
在 **qlib 侧无法现算**（qlib 表达式是**逐股**时间序列，看不见其它股票 ✗）⇒ 只能先物化成本项目自造的
**市场级字段**（同一交易日全市场同值）：`features/{code}/mkt_*.day.bin`。

```bash
python backend/tools/materialize_market.py                # 自动扫描公式库里的 BLOCKSETNUM/INSUM 用法
python backend/tools/materialize_market.py --overwrite     # ★ 改了被调公式 / 板块口径之后必跑
python backend/tools/materialize_market.py --dry-run       # 只列出"会物化什么"
```

- 被调公式（如 `IS_GOLD_PIT`）必须是**已保存的公式**（`INSUM` 按**输出名**引用它）⇒ 改了那个公式后必须重跑 ✓；
- 口径：板块成分按**当日真实成分**（`instruments/*.txt` 自带起止日 ⇒ 无未来函数 ✓）；
  `全部A股` = 全部在市 A 股（含 ST、含停牌、不含退市），`沪深A股` = 同上但剔除北交所；
  停牌股**算在分母里**、其指标值当日按 **0** 计（与通达信"成分股数"口径一致 ✓）；
- 缺了同样**不报错、静默全 NaN** ✗ ⇒ 用 `/api/version` 的 **`market_meta.ok`** 判断
  （`false` = 没物化或口径过期 ⇒ 立刻重跑 ✓）；
- 默认窗口 `2010-01-01 ~ 数据末日`（`--start/--end` 可改）：`BARSCOUNT()` 会从**窗口起点**开始计数，
  对"上市满 N 天"这类判据无影响 ✓（老股票早已超过 N 天）。

> ★★ **v1.20.45 起：怎么判断"这台机器的 `chip_*` 要不要重物化"**（同事 `git pull` 后**先做这件** ✗）：
> 1. `curl http://127.0.0.1:8001/api/version` ⇒ 看 **`chip_meta.ok`** ✓
>    （`false` ⇒ 本机 bin 是**别的口径**物化的、或**压根没物化** ✗ ⇒ 立刻重物化 ✓）；
> 2. 后端**启动日志**里也会有一条 `[chip-meta] …` ✓（不一致时是 **WARNING** ✗）；
> 3. `python backend/tools/verify_materialized.py` ⇒ 退出码 `0` 才算好 ✓（它同时检查
>    **物化口径戳** + **筹码展开比 `COST(95)/COST(5)`** ✓）。
> ⚠ 为什么要这么绕：**旧口径的 bin 是"静默错误"** ✗ —— 不报错、数值看着合理、连
> `COST(5) ≤ COST(95)` 单调都成立 ✗ ⇒ 只能靠"戳"（`features/_chip_meta.json` ✓）和
> **跨度体检**发现 ✓。实测 2026-09-21：换手率被放大 100 倍时，四档塌缩成
> `1.6 / 1.7 / 1.7 / 1.7` ✗，而**展开比从 1.58 掉到 1.015** ✗ 才看得出来 ✓。
> 期望值（v1.20.44 重物化后 360 只实测 ✓）：有 `$turn` 组 p50 ≈ **1.58**、反推组 ≈ **2.41**。

---

## 五、启动前端

```bash
cd qlib_code\frontend
npm install        # 首次安装依赖（如果没带 node_modules）
npm run dev        # 启动，端口 5173
```

---

## 六、启动后端

```bash
cd qlib_code\backend
# 用 qlib 环境的 python（按你机器实际路径）
python -m uvicorn app.main:app --host 0.0.0.0 --port 8001
# 或直接双击 start_backend.bat
```

> 端口说明：后端用 **8001**（8000 可能被其他服务占用）；前端 Vite 代理已指向 8001。

> ⚠ **重启后端必须杀"进程树"**（2026-09-14 实测教训）：qlib 取数的 joblib/loky worker 是后端的**子进程**，
> 只 `Stop-Process` 掉监听 8001 的那个进程的话，**12 个 worker 会变成孤儿活下来**（每个 hold 上百 MB，
> 每重启一次累加一份，越跑越占内存）。正确做法：
> ```powershell
> # 1) 杀进程树（/T = 连同子进程）
> $pid8001 = (Get-NetTCPConnection -LocalPort 8001 -State Listen).OwningProcess
> taskkill /PID $pid8001 /T /F
> # 2) 兜底：清掉"父进程已不在"的孤儿取数 worker
> Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
>   Where-Object { $_.CommandLine -like "*popen_loky*" } |
>   Where-Object { -not (Get-Process -Id $_.ParentProcessId -ErrorAction SilentlyContinue) } |
>   ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
> ```
> 本机已把这两步写进 `backend/workdir/restart_backend.ps1`（⚠ `workdir/` 是本地专用、不随仓库走，
> 换机后需按上面自行补上）。

---

## 七、验证部署

浏览器打开 `http://localhost:5173`，然后：
1. 看"历史回测"是否为空（正常，新环境无历史）
2. 选股票池 csi300、模型 Linear、起止日期（如 2022-2023）
3. 起始资金填 100（万）
4. 点"开始回测"，确认能跑通并出曲线、指标、调仓记录、训练产物

---

## 八、常见问题

| 问题 | 解决 |
|---|---|
| `import qlib` 报错 | qlib 未安装，见第三步 |
| 后端启动报"数据路径不存在" | 确认 QLIB_PROVIDER_URI / data/cn_data 存在 |
| 前端 `npm run dev` 报错 | 确认 Node.js ≥18，`npm install` |
| 8001 端口被占用 | 改 `start_backend.bat` 端口，并改 `frontend/vite.config.ts` |
| 回测结果全 0 / 无调仓 | 起始资金太小（<1万）买不起一手；或数据区间无数据 |
| summary.png 中文乱码 | 需安装微软雅黑字体（Windows 自带） |

---

## 附：单因子特征面板缓存与环境变量（v1.19.0 起）

单因子测试的**特征面板**（`panel_expr` 面板求值器）现在会**写磁盘缓存**，同一组参数重跑可直接命中
（全A + 5 年实测 `feature_s` 从 ~19s 降到 ~0.1s）。缓存目录：`backend/workdir/feature_cache/`
（已被 `.gitignore` 忽略，不会进仓库）。

⚠ **单份「全A + 5 年」面板缓存约 318MB**，因此必须做容量治理（写入后自动执行）：

| 环境变量 | 默认 | 含义 |
|---|---|---|
| `QLIB_CACHE_MAX_MB` | `4096`（4GB） | 缓存总大小上限，超出按 mtime **从旧到新**删（LRU）；`0` = 不限制 |
| `QLIB_CACHE_MAX_FILES` | `200` | 缓存文件数上限；`0` = 不限制 |
| `QLIB_CACHE_MAX_AGE_DAYS` | `30` | 超过该天数的缓存直接删；`0` = 不清理过期 |

**建议**：如果常开多个股票池/区间来回调参，把 `QLIB_CACHE_MAX_MB` 调到 `8192`~`16384`
（命中一次省 ~19s，很划算）；磁盘紧张则保持默认或调小。

其他相关开关：

| 环境变量 | 默认 | 含义 |
|---|---|---|
| `QLIB_SFT_PANEL` | `1` | 设为 `0` 强制单因子测试**跳过面板求值器、回退 qlib `D.features`**（同时不查/不写面板缓存）|
| `QLIB_SFT_PANEL_EMA` | `0` | 设为 `1` 时含 `EMA/EMA_TDX/SMA` 的字段**回退 qlib**（逃生口）|

手工清理缓存：直接删除 `backend/workdir/feature_cache/` 下的 `*.pkl`（可随时删，删了只会重算一次）。

---

## 附：如何重新导出依赖（在家更新后）
```bash
conda activate qlib
pip freeze > requirements_qlib.txt        # 生成 pip 依赖
conda env export --no-builds > qlib_env.yml  # 生成 conda 环境
```
