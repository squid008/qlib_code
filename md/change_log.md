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

## [2.10.11] - 2026-10-20

### Test（**E2E 补载荷契约** ✓ —— 第 ③ 件的第三块 ✓）

- 在 `backtest-flow.spec.ts` 里把"只断言发了请求" ✓ 加强到**断言 payload 的内容** ✓：
  · 必须含 `universe / start_date / end_date / model / feature / topk` 六个键 ✓；
  · 日期必须是 `YYYY-MM-DD` ✓、`topk > 0` ✓。
  **为什么值这个断言** ✗→✓：后端按这些**键名**解析 ✓ ⇒ 前端改名**不会报错** ✓，
  只会"用默认值跑一遍"✗ —— 用户看到结果 0、界面一切正常 ✓（最难发现的一类 ✓）。
- **⭐ 把"结果区渲染"为什么还没测说清楚** ✓（写在 spec 注释里 ✓）：
  · 已经摸到正确答案的一-Half：**`夏普比率` 才是唯一可用标记** ✓ ——
    `年化收益` 会撞**历史表格的列头** ✗（第一版就因此**假通过** ✓，是 a11y 树里的
    `columnheader "年化收益"` 暴露的 ✓）；
  · 但实测在"契约级 stub"下**指标卡没渲染出来** ✓ ⇒ 从 `success` 到"渲染结果"之间还有一步依赖 ✓
    （很可能是 snapshot/artifacts 的形状 ✓，或要等下一拍轮询 ✓）；
  · ⇒ 需要**逐帧追一次**才敢下断言 ✓，本轮**不写会误报的断言** ✗（宁可少测一条 ✓）。
- **实测** ✓：`npx playwright test` **3 passed（9.5 秒）** ✓ ｜ 本地与 CI 同配置 ✓。
- **校验** ✓：3 passed ｜ CI（e2e job ✓）。

## [2.10.10] - 2026-10-20

### Changed（**mypy 闸门扩到 11 个文件** ✓ —— 公式翻译器的两个纯解析文件 ✓）

- 新增 `app/factors/parser/lexer.py` ✓ 与 `app/factors/parser/parser.py` ✓（9 → **11** 个文件 ✓，仍 **0 错误** ✓）。
- **⭐ 一条真正有价值的修法** ✓：`Parser.error()` **必然 `raise`** ✓，但没标返回值类型 ⇒
  mypy 以为它会正常返回 ✓ ⇒ `expect()` 与 `_parse_atom()` 都被判 **`Missing return statement`** ✗
  （两条 ✓）。正确修法是给它标 **`-> NoReturn`** ✓ ——
  **不是**在每个调用处补一句 `raise` ✗（那是把类型问题变成代码噪声 ✓）。
  这类"函数其实永不返回"的标注，在解析器/校验器里很常见 ✓，标对一次就能消掉一串假阳性 ✓。
- 另修 `lexer.py` 的 `buf = []` ⇒ `buf: list[str] = []` ✓（空 list 必须给元素类型 ✓）。
- **⚠ 故意不收 `semantic.py`** ✗（6 条），理由写进 `pyproject.toml` ✓：其中一条是**语义疑点** ——
  `semantic.py:84` 在 **`dict` 上调 `.add()`** ✓（`dict` 没有 `add` ✗），而 `_scan_deps` 的第 3 参
  被注解成 `dict` ✓、调用处传的却是 `set` ✓ ⇒ **注解与实参自相矛盾** ✓。
  要判断"哪边才是对的"必须读懂语义扫描的数据流 ✓，**改错了会让公式校验静默失效** ✗ ⇒
  留给单独一轮 ✓（本轮只收能确定的那两个文件 ✓）。
- **校验** ✓：`mypy` **0 errors（11 files）** ✓ ｜ `ruff` **0** ✓ ｜
  parser/formula 相关回归 **203 passed** ✓ ｜ CI ✓。

## [2.10.9] - 2026-10-20

### Test（**事件统计 9 条** ✓ —— `event_study.py` 两份合计 **16.03% → 31.34%** ✓）

- 钉住 `build_event_stats`（`curve` / `prob` / `upside` / `top_by_k` / `worst_by_k` ✓）的三条"难发现"口径：
  · **⭐⭐ 直方图不许丢样本** ✓：极端值靠 `np.clip` 并进端桶 ✓ ⇒ 断言
    **各桶计数之和恒等于 `n`** ✓（丢了就是"分布图看起来总有人凭空消失"✗，而图上不会有任何报错 ✓）；
  · **⭐⭐ 榜单必须"按当前 k"** ✓：旧实现固定用 `mat[:, -1]`（max_k 期）✗ ⇒
    **表头写"持有 k 日"、数值却是 max_k 期** ✗，还漏掉"没持满 max_k 但在短 k 上完全有效"的事件 ✗；
    断言 **k=3 的冠军收益 > k=1 的** ✓ 且各自与 `curve` 的 `max` 对得上 ✓（相等就是又用错了 k ✗）；
  · **亏损侧是收益侧镜像** ✓：`gt0 + lt0 <= 1` ✓（零收益不两边算 ✓）、
    `lt100` 在 A 股**结构性恒 0** ✓（价格非负 ⇒ 多头最多亏 100% ✓）。
- 另钉 ✓：`upside` 两侧齐 ✓ 且 `max ≥ median ≥ dn_median ≥ dn_min` ✓；榜单**上限 20/20**、
  顶层兼容字段 **20/10** ✓；空事件 ⇒ 各段为空且 `upside` 全 `None` ✓（不许塞 0 ✗）。
- **✗ 两处我自己的期望错（都被实测纠正 ✓）**：
  ① `lag=1, k=1` 的收益是 `px[T+2]/px[T+1] − 1` ✓，我写成 `1.1² − 1` ✗（那是"相对信号日"口径 ✓）；
  ② **`max_k=0` 不是"夹到 1"** ✗ —— `max(1, int(max_k or 40))` 里 **`0` 是 falsy** ✓
     ⇒ 走的是"**默认 40**"✓；负数才夹到 1 ✓。已拆成两条用例分别钉住 ✓（免得下一个人以为 `0 == 1` ✗）。
- **✗ 又犯一次**：断言消息里嵌 ASCII 双引号 ⇒ `SyntaxError` ✓（**本段第 4 次** ✗，改「」✓ 并写进注释 ✓）。
- **实测** ✓：`test_event_study_align` + `test_event_study_stats` 两份合计覆盖该模块
  **16.03% → 31.34%** ✓（418 语句 ✓），**21 passed** ✓。
- **校验** ✓：9 passed（2.6 秒 ✓）｜ `ruff` **0** ✓ ｜ CI ✓。

## [2.10.8] - 2026-10-20

### Test（**产物定位与口径戳 11 条** ✓ —— `artifacts.py` 两份合计 **12.05% → 23.18%** ✓）

- 接着第 ① 件（覆盖率）继续 ✓。这一批钉的是**所有复用/续测的入口**：
  · **`_find_artifact_base`** ✓ —— 定位错目录的后果是**拿别的任务的模型去预测** ✗
    （结果全错但界面正常 ✓），或**读别人的口径戳** ✓ ⇒ 守卫比对了错的对象 ✗；
    专门断言 **`T1` 不能匹配到 `T10`** ✓（前缀匹配写松了就会 ✓ —— 这类 bug 只在
    "任务号有前缀关系"时出现 ✓，最难在常规回归里遇到 ✗）；
    找不到 ⇒ `None` ✓（调用方据此**不挡** ✓，那是"任务被删"✗ 不是"口径不一致"✗）。
  · **`_read_train_semantics`** ✓ —— 读不到必须返回 `None` ✓（调用方据此**挡住复用** ✓），
    **不许猜默认值** ✗（猜了等于偷偷放行 ✓）；另测：**分段优先、缺了退主目录** ✓
    （不是"读不到就算没有"✗）、**坏 JSON ⇒ `None` 不抛** ✓（复用检查自己不许让回测崩 ✗）、
    **空戳 ⇒ `None`** ✓（"有文件"不等于"有戳"✗）。
  · **`_task_has_segment_models`** ✓：滚动训练（有 `segment_1/` ✓）与单次训练要能区分 ✓
    —— 它只影响**错误提示的措辞** ✓，但说错会让人去查错方向 ✗。
  · **`_train_semantics_info`** ✓：非标量值**转字符串** ✓（否则写 JSON 直接炸 ✗）、
    特征/公式统计的是**个数** ✓、整体必须可 `json.dumps` ✓。
- **做法** ✓：把 `app.config.WORK_DIR` monkeypatch 到 `tmp_path` ✓（函数里是
  `from ..config import WORK_DIR` ✓ 所以补丁能生效 ✓），再用临时目录造
  `20240101_T1/`、`segment_N/`、`model_artifacts.json` ✓。
- **实测** ✓：`test_artifacts_guards` + `test_artifacts_paths` 两份合计覆盖该模块
  **12.05% → 23.18%** ✓（440 语句 ✓），**23 passed** ✓。
- **校验** ✓：11 passed（6.3 秒 ✓）｜ `ruff` **0** ✓ ｜ CI ✓。

## [2.10.7] - 2026-10-20

### Added（**E2E 全链路（API 契约级）** ✓ —— 第 ③ 件的第二块 ✓）

- 新增 `frontend/e2e/backtest-flow.spec.ts`（**1 条 ✓**）：把
  「**填表 → `POST /api/backtest` → 轮询 `GET /api/backtest/{id}` → `GET .../result` → 渲染**」
  这条**前端自己的编排**跑一遍 ✓ —— 单测只能覆盖其中一段 ✓，谁先谁后、什么时候切到结果区，
  只有整条跑才看得见 ✓。
- **为什么 stub 而不是起真后端** ✓：真后端要 qlib + 行情（CI 没有 ✗），一次回测还是**分钟级** ✓
  ⇒ 进 CI 会让每个 PR 多等 10 分钟 ✗。所以用"**契约级 stub**"：请求/响应形状照真实接口写 ✓，
  验证"**前端在正确响应下会正确编排**"✓。
  ⚠ **如实标注**：它**不能**证明后端算得对 ✗ —— 那是 `test_engine_golden`（NAV 指纹 ✓）
  与后端 1,500+ 条用例的事 ✓。
- **⭐ 一条被 a11y 树当场戳破的"假通过"** ✗→✓：第一版断言「结果区出现 `年化收益` 指标卡」✓ ——
  它**通过了** ✓，但排查另一条断言时看 a11y 树才发现：`年化收益` 也出现在**历史表格的列头**里 ✓
  （`columnheader "年化收益"` ✓）⇒ **这条断言永远会过，等于没测** ✗。
  ⇒ 改成钉**真实可验证的契约**：点「开始回测」后**真的发出 `POST /api/backtest`** ✓
  且 body 非空 ✓、全程**零未捕获异常** ✓；并把"结果区渲染"**如实标为下一轮** ✗（要先用结果区独有文案 ✓，
  不能用会误报的通用词 ✓）。标题也从"…结果区出现指标卡"改成"**…真的发出创建请求**"✓
  （**标题不许说得比断言多** ✗）。
- 另记一个坑 ✓：指标卡标签节点带空白 ✓ ⇒ `getByText(..., { exact: true })` **不中** ✗（去掉 exact ✓）。
- **校验** ✓：`npx playwright test` **3 passed（7.5 秒）** ✓ ｜ `tsc -b` **0 错误** ✓ ｜ CI（e2e job ✓）。

## [2.10.6] - 2026-10-20

### Changed（**mypy 闸门扩一圈：4 → 9 个文件** ✓ —— 又修掉一处真实隐患 ✓）

- **扩了哪些** ✓：`+ app/engine/analysis.py` ✓、`+ app/engine/artifacts.py` ✓、
  `+ app/engine/inst_mask.py` ✓、`+ app/signals/engine.py` ✓、`+ app/signals/pricing.py` ✓
  （原来只有 `models` + `config.py` + `engine/metrics.py` ✓）。
- **⚠ 一条必须加的开关** ✗→✓：**`follow_imports = "silent"`** ——
  不加时 mypy 会顺着 `import` 把**整张依赖图**都检查 ✓（6 个文件 ⇒ **105 条** ✗，
  大头在 `factors/` 分支 ✓）⇒ 闸门第一天就变成噪声 ✓，最后一定被人关掉 ✗。
  加上之后**只报配置里列出的文件** ✓（同一批 6 个文件 ⇒ **15 条** ✓，逐条可修 ✓）。
- **⭐ 又抓到一个真实隐患** ✓（`engine/inst_mask.py`: `Assignment to variable "e" outside except`）：
  解析区间文件时写过 `except Exception as e:` ✓，而 Python **在 `except` 块结束时会删掉 `e`** ✓
  ⇒ 下面 `for s, e in segs:` **同名复用** ✓（合法但极易误读 ✓，改名为 `st/en` ✓）。
  这类"名字遮蔽"不会报错 ✓，但会让人在调试时看到**已被删除的变量** ✗。
- **其余 6 条都是"注解与实现不一致"** ✓（全部按真实语义修 ✓，没有 `type: ignore` ✗）：
  · `price(..., kind: str = None)` ⇒ `"str | None"` ✓（默认值是 None 而注解是 str ✗）；
  · `nav_rows` 里 `rec = {"date": ...}` ⇒ 显式 `dict` ✓（否则被推成 `dict[str,str]` ✗，
    后面写 `None`/`float` 全不兼容 ✓）；
  · `artifacts = {...}` ⇒ 显式 `dict` ✓（否则嵌套值被推成联合类型 ✗ ⇒
    `artifacts["model_info"]["num_trees"]=…` 报"无法在下标上赋值"✓）；
  · `float(req.topk_ratio)` ⇒ `float(... or 0)` ✓（`Optional` 收窄不到 ✓）；
  · `all_trades` 是**异构容器**（`dict` 与 `("__ROW__", rec)` 元组混装 ✓，
    代码里本来就有注释说明 ✓）⇒ 显式标 `list` ✓，让类型**如实表达混装** ✓。
- **⚠ 故意先不收 `engine/charts.py`** ✗：它 6 条 `arg-type` 全是 matplotlib 对
  `list[Any | None]` 的严格签名 ✓ ⇒ 要修就得改绘图数据流 ✓（收益低、风险高 ✓）；
  已写进 `pyproject.toml` 注释 ✓ —— **不是忘了** ✓。
- **校验** ✓：`mypy` **0 errors（9 files）** ✓ ｜ `ruff` **0** ✓ ｜
  相关回归（engine/mask/artifact/pricing/analysis/metrics）**277 passed** ✓ ｜ CI ✓。

## [2.10.5] - 2026-10-20

### Added（**第三件事「E2E」骨架上线** ✓ —— 真浏览器里跑整棵 React 树 ✓）

- **新增** `frontend/playwright.config.ts` ✓ + `frontend/e2e/smoke.spec.ts`（2 条 ✓）+
  `npm run test:e2e` ✓ + CI 独立 job `e2e` ✓（只下 chromium ✓，失败上传 trace/截图 ✓）。
- **它到底能抓到什么**（jsdom 抓不到的 ✓）：我们已有 200+ 条前端单测，但 jsdom
  **没有布局引擎** ✗、没有 `ResizeObserver` ✗（`EventDistChart` 那次就是靠打桩才挂得上 ✓）、
  也不会真跑构建产物 ✓ ⇒ **打包/构建层的错只会在真浏览器里露头** ✗；而白屏往往**不报错** ✓，
  只在 console 里留一条 `Uncaught TypeError` ✗ ⇒ 这里把它变成**断言** ✓（`pageerror` + `console.error` 全收集 ✓）。
- 两条用例 ✓：① 首屏渲染（`#root` 文本 > 50 字符 ✓）＋ 顶栏显示**前端自己的 `FE_VERSION`** ✓
  ＋ **零未捕获异常** ✓；② 接口全被 stub 成**空数据**时**不白屏** ✓。
- **⚠⚠ 实测踩到两个"会静默骗人"的坑** ✓（都已写进配置文件注释 ✓）：
  1. **`reuseExistingServer: !CI` ⇒ 本地复用了别人的服务** ✗ —— 第一版用 5174 端口，
     本地跑时它接管了一个**已经在跑的应用** ✓（截图里是「交易模拟器 / 用户名 / 口令 / v0.66.89」✓）
     ⇒ **我测的根本不是本项目** ✗，而且**还会通过** ✓ —— 这是 E2E 最危险的失败模式 ✓
     ⇒ 改成 **端口 5299 + `reuseExistingServer: false` 永远** ✓（配合 `--strictPort`：端口被占就明确失败 ✓）；
  2. **vite 默认绑 `localhost`（Windows 上常先解析到 IPv6 `::1`）** ✗，而 Playwright 轮询
     `http://127.0.0.1:PORT` ✓ ⇒ 实测**干等 90 秒超时** ✗（服务其实起来了 ✓，只是监听在另一个协议栈 ✓）
     ⇒ 启动命令补 **`--host 127.0.0.1`** ✓。
- **为什么先 stub 全部 `/api/**`** ✓：骨架阶段不该依赖后端（CI 起 FastAPI 要 qlib 数据 ✗）；
  ⚠ **如实标注**：这测的是"**前端在 API 正常时能渲染**"✓，**不是**"前后端联调通了"✗ ——
  后者（提交回测 → 等结果）需要真后端 + 真数据 + 分钟级等待 ✓，属**下一轮全链路 E2E** ✓，本轮不假装 ✓。
- **实测** ✓：本地 `npm run test:e2e` **2 passed（10.5 秒）** ✓ ｜ `npx tsc -b` **0 错误** ✓ ｜
  `npm test`（单测）**不受影响** ✓（`vitest` 只收 `src/**` ✓）｜ `ruff` **0** ✓ ｜ CI（含新 e2e job ✓）。

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

