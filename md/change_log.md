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

## [2.10.28] - 2026-10-20

### Fixed（**"CI 上 jsdom 会崩"是错的** ✗→✓ —— 真因：缺 `ResizeObserver` 桩 ✓）

- **这条错误认知的代价** ✓：`metricCards.test.tsx` 里写着「CI（Linux + Node 20）上**只要走 jsdom
  就崩**」✗ ⇒ 从那以后**所有组件测试都退成 `renderToStaticMarkup`** ✓ ⇒
  **点击 / 输入 / 展开类断言一个都写不了** ✗ ⇒ 前端 100+ 组件里只测了纯展示与纯逻辑 ✓
  （覆盖率长期停在 ~18% ✗ 的直接原因之一 ✓）。
- **追到底的结论** ✓：本轮给 `ICChart` 写交互测试时**当场复现**了那个 `TypeError` 的**真正来源**：
  ```
  ReferenceError: ResizeObserver is not defined
  ```
  —— 它来自 recharts 的 `<ResponsiveContainer>` ✓（用它监听容器尺寸 ✓），而 **jsdom 不实现
  `ResizeObserver`** ✗（`matchMedia` 同理 ✓）。⇒ **不是"环境不可用"** ✗，是**少一个 polyfill** ✓。
- **修法** ✓：新增 `frontend/vitest.setup.ts`（只放**最小**浏览器 API 桩 ✓：
  `ResizeObserver` 的 `observe/unobserve/disconnect` 空实现 + `matchMedia` 常量返回 ✓），
  在 `vitest.config.ts` 里 `setupFiles` 挂上 ✓。桩要**最小** ✓ —— 布局在 jsdom 里本来就没有 ✓，
  别假装有 ✗（所以**断言不许依赖尺寸** ✓）。
- **顺带核对了一件更重要的事** ✓：CI 日志里 `taskStatusPanel.test.tsx`（`createRoot` + `act`，
  **必须要 DOM** ✓）**真的跑了** ✓，整轮 **28 文件全过** ✓ ⇒ **jsdom 在 CI 上一直是好的** ✓
  —— 这句误导性注释害了整整两年 ✓。
- **新增 `icChartInteractive.test.tsx`（3 条 ✓）** —— 第一批**真交互**断言：
  · 默认在测试集 ⇒ 点「训练集 IC」后**列表真的换过去** ✓（不是只换高亮 ✗）；
  · **切换分段 ⇒ 徽章数值跟着换** ✓（串味 = 用户看着 A 段数据以为在 B 段 ✗）；
  · 切回测试集 ⇒ 选中态**重置**到第一个分段 ✓。
- **并把那条误导性注释改写了** ✓（`metricCards.test.tsx` 文件头 ✓）：说明真因是缺桩 ✓，
  并写清"该文件继续用 SSR 是**选择**（纯展示、更快、能保服务端可渲染 ✓），不是妥协"✓ ——
  避免下一个人又被它挡住 ✗。
- **实测** ✓：`npx vitest run` **198 passed / 29 文件** ✓ ｜ `tsc -b` **0 错误** ✓ ｜
  CI ✓。**下一步** ✓：交互测试已解锁 ⇒ `SignalTestPanel`(1.5k) / `SingleFactorTestPanel`(2.1k)
  / `EventStudyModal`(1.5k) 这些"零测试大组件"终于可以啃了 ✓（覆盖率的主升浪在这里 ✓）。

## [2.10.27] - 2026-10-20

### Changed（**前端覆盖率门槛 17 → 20** ✓ —— 靠两个图表组件一次性拉起来 ✓）

- CI 实测新数字 ✓：
  ```
  All files        | 20.49 | 86.67 | 66.5 | 20.49
  Statements : 20.49% ( 2335/11394 )
  ICChart.tsx      | 92.25 | ...      ← 0 → 92.25%
  LayerChart.tsx   | 84.44 | ...      ← 0 → 84.44%
  ```
  ⇒ 门槛按「实测值向下取整」= **20** ✓（原 17 ✓）。
- **★ 一条值得记的经验** ✓：**前端覆盖率不是"摊薄"型指标** ✗ ——
  给十个已有测试的小文件加断言，涨不了 1pp ✓；而抓**一个零测试的大组件**（0 → 92%）✓
  一次就 +2.6pp ✓。⇒ 后面推进的正确姿势是"**从最大的零测试文件往下啃**"✓，
  而不是"给已有测试补分支"✗。
- 剩余大头（都还是 0 ✗）：`SignalTestPanel`(1.5k) / `SingleFactorTestPanel`(2.1k) / 各 Modal ✓
  —— 它们**有大量交互**（要 jsdom/RTL ✗，而 CI 上 jsdom 不稳 ✗），成本明显高于图表 ✓，
  要么先解决 "CI 上 jsdom 为什么崩" ✓，要么继续挑**静态可测**的组件 ✓。
- **《开发记录》基线同步** ✓：前端 **195 条 / 28 文件**、覆盖 **20.49%**、门槛 **20** ✓，并记下那条经验 ✓。
- **校验** ✓：CI（新门槛 20 生效 ✓）｜ `npx vitest run` **195 passed** ✓ ｜ `tsc -b` **0** ✓ ｜
  版本 + 体检 14 passed ✓。

## [2.10.26] - 2026-10-20

### Test（**组件级测试开工** ✓ —— `ICChart` 9 条 + `LayerChart` 5 条，前端 181 → **195** ✓）

- **为什么先挑这两个** ✓：它们是**全仓唯二**"有实质逻辑却零测试"的中等组件（203 / 242 行 ✓），
  而且正是用户**直接看图下结论**的地方 ✓（IC 稳定性、分层收益）——
  `src/` 覆盖率 **17.9%** 的黑箱里，它们是最值钱的一块 ✓。
- **⚠ 放弃了 eslint**（本轮原计划 ✓）：这个环境里 `npm install eslint` 反复被 10 秒探测打断 ✗，
  而且无类型感知的 eslint 只能给 `eqeqeq` 之类 ✓ —— **边际价值低于直接补组件测试** ✓（`tsc` 已管类型 ✓）。
- **测试方式**：`renderToStaticMarkup`（与 `metricCards.test.tsx` 同因 ✓ —— CI 上 jsdom 不稳 ✗）。
  代价是**测不到点击**（切 tab / 点图例 ✗）⇒ 只钉"**用户第一眼看到的默认态对不对**"✓。
- **`ICChart` 钉住的 4 条真契约** ✓：
  · **默认看测试集** ✓（训练集分段名**不许**出现在默认态 ✗）；
  · 徽章小数位：平均IC **4 位** / ICIR **3 位** / RankIC **4 位** / RankICIR **3 位** ✓；
  · **⭐⭐ `null` 的 ICIR / RankIC 徽章整块不渲染** ✓（显示 `ICIR: null` 或 `0` 都是错的 ✗
    —— 用户会把"算不出来"读成"接近 0"✗）；
  · **「汇总」只在对应 tab 的 `merged` 真有 points 时出现** ✓（空数组 ⇒ 不出现 ✗）。
- **`LayerChart` 钉住「三种状态」** ✓：① 无数据 ⇒「暂无分层数据」；② 有分段但该段 `groups` 空 ⇒
  「**该分段**暂无分层数据」；③ 正常 ⇒ 渲染段名 ✓。
  ⚠ 图例（`Group1..5` / `多空(1-5)` / **池内等权** / 基准 ✓）画在 recharts `<Legend>` 里 ✓，
  而 `<ResponsiveContainer>` 在 SSR 下宽高为 0 ⇒ **内部根本不渲染** ✗ ⇒ 这些**测不到** ✓，
  文件头已如实注明 ✓（不假装 ✓）。
- **✗ 两处我自己的错**：
  1. `not.toContain('暂无 IC 数据')` ✗ 被「**该分段**暂无 IC 数据」这个子串命中 ✓
     —— 实测才知道组件有**三种**空态 ✓（已改成分别断言 ✓）；
  2. `0.8765` 的 `toFixed(3)` 得 **0.876** 而不是 0.877 ✗ —— 它在二进制里是 `0.87649999…` ✓
     ⇒ `toFixed` 不是"四舍五入"那么简单 ✓，夹具改用无歧义值并写进注释 ✓。
- **实测** ✓：`npx vitest run` **195 passed / 28 文件** ✓ ｜ `tsc -b` **0 错误** ✓ ｜
  前端覆盖率数字待 CI 报出（先采集、后按实测抬门槛 ✓ 同一套两步法 ✓）。
- **下一步** ✓：CI 报出新数字后，把 `vitest.config.ts` 的 `thresholds` 从 **17** 抬到新的 floor ✓。

## [2.10.25] - 2026-10-20

### Changed（**前端覆盖率门槛上线** ✓ —— 首次拿到真实数字：**17.9%** ✗，门槛 **17** ✓）

- **第二步落地** ✓（第一步 2.10.23 只采集 ✓）：CI 跑完报出
  ```
  All files | 17.9 | 87.83 | 67.35 | 17.9
  Statements : 17.9% ( 2040/11394 )
  ```
  ⇒ 按规矩「**门槛 = CI 实测值向下取整**」= **17** ✓，写进 `vitest.config.ts` 的 `thresholds` ✓。
- **⚠ 只卡 `statements` / `lines`** ✗→✓（不卡 `branches` 87.8 / `functions` 67.4）：
  v8 的分支采样口径影响更大 ✓，且本项目"纯逻辑模块高覆盖、组件几乎为零" ✗
  ⇒ 分支数天然偏高 ✓，一上来卡分支会让闸门**看起来严、其实只拦无关紧要的东西** ✗。
- **⚠ 17.9% 这个数字很难看，但它是真的** ✓（这一点写进了配置注释 ✓）：
  181 条用例几乎全在 `src/*.ts` 纯逻辑与表单组件上 ✓，
  `SignalTestPanel`(1.5k) / `SingleFactorTestPanel`(2.1k) / 各 Modal **基本为零** ✗。
  ⇒ 闸门的价值是**不许更差**（ratchet ✓）；**要提升得另开"组件级测试"的回合** ✓ ——
  这也让前端从"6.5 分（黑箱）"变成"**有数字、有闸门**"✓。
- **《开发记录》基线同步** ✓：新增「依赖与安全」与「前端覆盖率 17.9 / 门槛 17」两段 ✓，
  并把"**影子依赖清单**"的教训写死 ✓（CI 裸 `pip install` 里藏了 6 个未声明包 ✓ ⇒
  换成 `-r requirements.txt` 后 4 个测试模块 `ModuleNotFoundError` ✓ 已补齐 ✓）。
- **校验** ✓：CI（门槛 17 生效 ✓）｜ `pip-audit` **No known vulnerabilities found** ✓ ｜
  后端 TOTAL **61.83%**（门槛 61 ✓）｜ 版本 + 体检 14 passed ✓。

## [2.10.24] - 2026-10-20

### Fixed（**`requirements.txt` 漏声明 6 个依赖** ✓ —— 换安装方式时立刻暴露 ✓）

- v2.10.23 把 CI 的"裸 `pip install` 列表"换成 `-r requirements.txt` ✓ ⇒ **当场红了** ✓：
  ```
  ModuleNotFoundError: No module named 'h5py'
  （4 个测试模块收集失败：test_finance_pit_mapping / test_finance_yoy_absbase /
    test_tushare_finance / test_tushare_pit_dedup）
  ```
- **根因** ✓：这 6 个包一直**只在 CI 的内联列表里** ✗、没写进 `requirements.txt` ✗ ——
  `h5py` / `scipy` / `joblib` / `PyYAML` / `tqdm` / `Cython`。
  ⇒ 教训写进文件注释 ✓：**CI 里的裸 `pip install` 列表就是一份"影子依赖清单"** ✗，
  它和 `requirements.txt` 一旦不同步，只在"换安装方式"或"换机器"时才炸 ✓
  （与当年 `openpyxl` 那次 `ModuleNotFoundError` 是**同一个形状** ✓）。
- **这正好说明这次改动值了** ✓：把影子清单消掉之后，**第一件事就是把缺的依赖暴露出来** ✓ ——
  以前它们藏在 CI 的列表里，谁也不会去核对 ✓。
- **校验** ✓：CI（后端收集恢复 ✓）｜ 本地 `pytest` 不受影响（本机早有这些包 ✓）｜ 版本 + 体检 14 passed ✓。

## [2.10.23] - 2026-10-20

### Added（**三件工程化补强** ✓ —— 把两个「6 分档」拉起来 ✓）

**① 前端覆盖率采集（第一步：只测不卡 ✓）**
- 之前前端有 **181 条用例**但**完全不知道覆盖了哪些组件** ✗ —— 这是工程里最后一块黑箱 ✓
  （后端有 61.83% + 门槛 61 ✓，前端什么都没有 ✗）。
- 加了 `@vitest/coverage-v8@^2.1.9`（与 vitest **2.1.9** 对齐 ✓）+ `npm run test:cov` ✓ +
  `vitest.config.ts` 的 `coverage`（`include: src/**` ✓，排除测试自身与 `main.tsx` ✓ ——
  否则等于自己给自己加分 ✗）。
- **⚠ 本轮只采集、不设门槛** ✓（与后端当初同一套**两步法** ✓）：先让 CI 报出真实数字 ✓，
  下一版再按「实测值向下取整」设 `thresholds` ✓ ——
  凭感觉设一个数，闸门要么**一上线就红** ✗、要么**松到没意义** ✗。
- ⚠ 本地无法验证这条 ✓：`npm install @vitest/coverage-v8` 在这个环境里**反复被 10 秒探测打断** ✗
  ⇒ 覆盖率包由 **CI 安装** ✓，本地只验证了 `tsc -b` ✓ 与 `npm test`（181 条 ✓）不受影响 ✓。

**② 依赖漏洞扫描进 CI（阻断式 ✓）**
- 新增 step `python -m pip_audit -r requirements.txt` ✓ —— 挡住"**带已知 CVE 的版本一路绿灯上线**"✗
  （这类问题**不会让任何测试变红** ✓）。
- ⚠ 规矩写进注释 ✓：扫出来只**改 `constraints.txt` 升到已修版本**（单独 commit ✓），
  **不许**在 CI 里加 `--ignore-vuln` 糊过去 ✗。

**③ 依赖锁定：从"裸装"到"声明 + 锁定"**
- 新增 `backend/constraints.txt` ✓（**全部 `==`**，由**本机实测通过的版本**导出 ✓：
  fastapi 0.141.1 / pydantic 2.13.4 / numpy 2.2.6 / pandas 2.3.3 / pyarrow 25.0.1 /
  lightgbm 4.7.0 / matplotlib 3.10.9 / openpyxl 3.1.5 / xlrd 2.0.2 … ✓）。
- 新增 `backend/requirements-dev.txt` ✓（pytest / pytest-cov / ruff / mypy / **pip-audit** / matplotlib ✓）
  —— 运行时不必装测试框架 ✓。
- **CI 安装改成** `pip install -r requirements.txt -r requirements-dev.txt -c constraints.txt` ✓
  （原来是一串**没有版本**的 `pip install numpy scipy pandas ...` ✗ ⇒ 上游一升级，CI 会先红在
  "某个测试莫名其妙失败"✗ 而不是"依赖不对"✗ —— 这个坑项目里已经踩过两次 ✓）。
- **顺手补一个漏声明的依赖** ✓：`python-multipart` 一直在 CI 的列表里 ✓、却没写进
  `requirements.txt` ✗ ⇒ 换台机器装就 `ModuleNotFoundError` ✓（与当年 `openpyxl` 那次同一个形状 ✓）。
- ⚠ 规矩 ✓：**升级依赖 = 单独一个 commit 只改 `constraints.txt`** ✓ ⇒ "哪次升级导致变红"一眼可见 ✓。

- **校验** ✓：`tsc -b` **0 错误** ✓ ｜ `npm test` **181 passed** ✓ ｜ `ruff` **0** ✓ ｜
  版本 + 体检 14 passed ✓ ｜ CI（含新 audit 与前端覆盖率 step ✓）。

## [2.10.22] - 2026-10-20

### Test + Chore（**C′ 收尾** ✓ —— 剔除分支终于被**断言**住 ✓；`single_test` 52.4% 定档 ✓）

- **补上"反面验证"** ✓：上一轮记的是"标签全 NaN ⇒ 计数恒 0"✓，这轮补
  **"给了非 NaN 标签 ⇒ 必须真的剔掉"** ✓（`limit_up_excluded_t` ✓ 与 `limit_up_excluded_t1` ✓ 各一条 ✓）。
  两条合起来才算把"列存在就用标签"这条语义钉住 ✓ —— 只测一边，另一边退化了也发现不了 ✗。
- **✗ 我又错了一个口径**：以为被剔的样本会从 `n_obs` 里消失 ✗ —— 实测 `n_obs` 是**剔除前**的
  有效配对数 ✓（仍 240 ✓）。"剔了多少"只能看 `limit_up_excluded*` 这一组 ✓。
  ⇒ 顺手把这条口径也变成断言 ✓（`hit["n_obs"] == base["n_obs"]` ✓）。
- **⚠ 如实说明：覆盖率没有涨** ✗（仍 **52.43%** ✓，86 passed ✓）——
  说明剔除分支那些行**早就被执行过** ✓，本轮补的是**断言**而不是覆盖 ✓。
  ⇒ 这正是"C′ 该收尾"的信号 ✓（连续两轮零覆盖增长 ✓）。
- **C′ 结论写进《开发记录》** ✓（v2.10.16 ~ 2.10.22）：
  · `_test_one`（312 行 ✓）＋ `_quantile_curve_block`（597 行 ✓）都已用**合成面板**测通 ✓
    （从 `_test_one(df, …)` 进 ✓，不手搓 23 个 kwarg ✗）；
  · `single_test` **5.28% → 52.4%** ✓（九份用例 **86 条** ✓）；
  · **剩下 577 行不再追** ✗ —— 大头要"真标签/真面板"✓；唯一还能推进的入口是
    `_load_feature_panel`（要 qlib 面板数据 ✗）⇒ 那是**换环境**而非"写测试" ✓。
- **校验** ✓：12 passed（本文件 ✓）｜ 九份合计 **86 passed** ✓ ｜ `ruff` **0** ✓ ｜ CI ✓。

## [2.10.21] - 2026-10-20

### Test（**剔除计数：契约自洽 + 一条实测事实** ✓ —— ⚠ 本轮**没有**提升覆盖率 ✓，如实说明）

- **先说结果** ✓：`single_test` 覆盖**仍是 52.43%** ✗（这两条落在已覆盖的行上 ✓）。
  这一轮买到的是**两条更耐用的东西** ✓：一条**自洽性断言** ✓、一条**实测事实** ✓。
- ① **计数自洽** ✓：`limit_up_excluded == lu_t + lu_t1 + susp` ✓，非信号组同理 ✓ ——
  前端把这几个数**并排显示** ✓ ⇒ 对不上用户一眼就能发现 ✓（比"某个数对不对"更容易被信任 ✓）。
- ② **⭐ 一条我试了三版才搞清的事实** ✓（写进用例注释 ✓，避免下一个人重走 ✗）：
  面板里 `LIMIT_UP` / `T1_LIMIT_UP` 列**存在但全 NaN** 时，**即使 `CHANGE` 到了 10%**（主板涨停 ✓），
  **剔除计数也保持 0** ✓ ⇒ 判定走的是"**列存在 ⇒ 只用标签**"✗，缺标签**不做比例兜底** ✓
  （见 `_exclude` 与 `engine.limits.mark_limit_up` 的注释 ✓）。
  ⇒ **这不是功能坏了** ✓：真实回测里 `fill_limits` 会先把标签补齐 ✓（那条链路已有 7 条用例 ✓）；
  但它记下一条**夹具硬约束** ✓：**想验剔除，必须给非 NaN 的交易所标签** ✓，光让价格涨 10% 不够 ✗。
- **✗ 三次试错（都留着，免得下一个人再试一遍）**：
  1. 手工把 `LIMIT_UP` 设成当日 `CLOSE` ✗ ⇒ **没人读**（判据看的是 `CHANGE` ✓ / 或标签列 ✓）；
  2. 让 `CLOSE` 跳涨 10% 但 `CHANGE` 仍是 0 ✗ ⇒ `mark_limit_up(frame, "CLOSE", "CHANGE")` 读的是 `CHANGE` ✓；
  3. 把 `CHANGE` 设成 10.0 仍然 0 ✗ ⇒ 因为**标签列存在（全 NaN）**✓ 就直接走标签分支 ✓。
- **⚠ 待办（下一轮）** ✓：用**非 NaN 标签**造一条用例，把 `lu_t` / `lu_t1` / `susp` 三个计数真正测到 ✓。
- **校验** ✓：10 passed（3.4 秒 ✓，本文件）｜ 九份合计 **84 passed** ✓ ｜ `ruff` **0 错误** ✓ ｜ CI ✓。

## [2.10.20] - 2026-10-20

### Test（**C′ 第二刀：597 行的 `_quantile_curve_block` 跑到** ✓ —— `single_test.py` **43.3% → 52.4%** ✓）

- **做法（关键决策）**：它的 23 个形参全是 `_continuous_stats` 的**自由变量** ✓ ——
  手搓这 23 个值既脆、又会**测出一条假的分位曲线** ✗ ⇒ 改从**调用方**进：
  `_test_one(df, ...)` ✓（真实代码替我们算好中间量 ✓），断言落在它产出的**三个结构**上 ✓。
- **先用探针看清结构** ✓（临时脚本，跑完即删 ✓）⇒ 拿到 44 个键与形状，
  其中三个正是这一块的产物 ✓：`quantile_curves`（`n_groups/n_periods/dates/
  monotonicity/best_quantile` ✓）、`topk_curves`（`rebalance_period/horizon/n_days/
  dates/side/default_quantile` ✓）、`topk_sensitivity`（`default_k/ks/…` ✓）。
- **7 条断言** ✓：
  · **分组数跟着 `quantiles` 变** ✓（5/10 各验一次 ✓ —— 写死 10 就说明参数被忽略 ✗）；
  · 曲线结构自洽 ✓：`len(dates) == n_periods` ✓、`monotonicity ∈ [-1,1]` ✓、
    `best_quantile` 落在合法组号内 ✓（它决定前端高亮哪一组 ✓）；
  · **`topk_list` 的两种口径都认** ✓：`≤1` 视为**比例**（0.1 = 10% ✓）、`>1` 视为**只数**（10 ✓）
    ⇒ `ks` 至少两档 ✓（只认一种的话，用户切到"按百分比"就得到空图 ✗）；
  · **调仓期默认跟随预测周期** ✓（`horizon=3` ⇒ `rebalance_period==3` ✓），
    显式传 7 时用它 ✓（否则设置项形同虚设 ✗）；
  · **取消必须抛 `FactorTestCancelled`** ✓（被吞 = "点了取消但继续算"✗）；
  · **样本 < 100 ⇒ 整段跳过** ✓，并钉住一个**契约细节**：跳过时三个键**根本不存在** ✗
    （不是 `None` ✓）⇒ 前端读它们**必须判空** ✓，否则小样本因子渲染崩 ✗。
- **✗ 两处我自己的错**：① `FactorTestCancelled` 从 `app.signals.engine` 导 ✗（它在
  `app.factors.single_test` 里 ✓）；② 跳过时断言 `is None` ✗ ⇒ 实际是**缺键** ✓（改 `.get` ✓）。
- **实测** ✓：九份 `single_test` 用例合计覆盖该模块 **43.28% → 52.43%** ✓（1,213 语句 ✓），
  **82 passed** ✓，相关回归 **146 passed** ✓。
- **校验** ✓：7 passed（3.5 秒 ✓）｜ `ruff` **0 错误** ✓ ｜ CI ✓。

## [2.10.19] - 2026-10-20

### Changed（**门槛抬到 61** ✓ —— 第一次按 **CI 自己的数字**定门槛 ✓）

- 闸门修好后的第一次 CI 跑完 ✓，日志里真的出现了：
  ```
  TOTAL                     18996   7363   61.24%
  Required test coverage of 60% reached. Total coverage: 61.24%
  1543 passed, 4 skipped, 23 deselected
  ```
  ⇒ 按规矩「**门槛 = CI 实测值向下取整**」= **61** ✓（v2.10.17 时只能拿本地 61.50 保守压一档到 60 ✓）。
- **⭐ 顺手纠正一条流传已久的说法** ✗→✓：过去几个版本都写着"**CI 历来略高于本地**"✓
  （依据是 2.9.55 那次本地 55.06 / CI 55.18 ✓）—— 这次**反了** ✓：
  **本地 61.50 / CI 61.24**（CI 低 0.26pp ✓，因为 CI 少 10 个用例、多 4 个 skip ✓）。
  ⇒ 结论改成：**只认 CI 数** ✓，"CI 恒高"不可作为估计依据 ✗（已写进 CI 注释与《开发记录》✓）。
- **《开发记录》基线同步** ✓：覆盖率一行改为"本地 61.50 ／ **CI 实测 61.24** ｜ 门槛 **61**"✓，
  并把"闸门曾静默失效（`run: >-` 里的 `#`）"与"以后调门槛先看 CI 的 `TOTAL` 行"写进去 ✓。
- **校验** ✓：CI（新门槛 **61** ✓ 生效）｜ 本地同命令 `TOTAL 61.50%` ✓ ｜ 版本 + 体检 14 passed ✓。

## [2.10.18] - 2026-10-20

### Fixed（**覆盖率门槛从 v2.9.6 起就静默失效** ✗→✓ —— YAML 折叠块里的 `#` 不是注释 ✓）

- **⭐ 症状**：`gh run view --log` 里**从来找不到 `TOTAL` 行** ✗。前几轮我把这归咎于
  "工具不打印"✗，只能拿**本地**数字定门槛 ✓ —— 这个摩擦点一直存在 ✓。
- **真因**（一眼看出，但藏了很久 ✗）：
  ```yaml
  run: >-                                  # ← YAML **折叠块标量**
    python -m pytest tests -m "not datareq" -q
    # ★ v2.9.55 ... 56 → 60 的说明       # ← 块内的 `#` **不是** YAML 注释 ✗，是内容 ✓
    --cov=app --cov-report=... --cov-fail-under=60
  ```
  折叠成一行交给 bash 后，bash 把 `#` 之后**全当注释** ✓ ⇒ 实际只跑了
  `python -m pytest tests -m "not datareq" -q` ✓ ⇒ **`--cov` / `--cov-fail-under` 从未生效** ✗✗
  ⇒ **覆盖率门槛从 v2.9.6 起形同虚设** ✓，CI 压根没开覆盖率 ✓
  （也顺带解释了"为什么日志里没有 TOTAL"✓ —— 不是工具的问题，是**我自己把参数注释掉了** ✗）。
- **修法** ✓：`run:` 改成**单行** ✓，一切说明文字移到**真正的 YAML 注释**里 ✓
  （并在注释里留下门槛变更史 54 → 55 → 56 → 60 ✓ 与"改数字必须同步 change_log"的规矩 ✓）。
- **本地用与 CI 完全相同的命令验证** ✓（这很关键：以前只跑 `pytest -q`，从没验过带参数的那条 ✓）：
  ```
  TOTAL  18996  7313  61.50%
  Required test coverage of 60% reached. Total coverage: 61.50%
  1553 passed
  ```
  ⇒ 现在**会真的打印 TOTAL** ✓ 并**真的卡门槛** ✓。
- **下一步（同一件事的后半段）** ✓：等这次 CI 跑完，从日志里读到 **CI 自己的** `TOTAL` ✓，
  就能按规矩把门槛抬到 61 ✓ —— 这次终于**有 CI 数字可依据**了 ✓（过去只能拿本地数保守压一档 ✓）。
- **校验** ✓：本地同命令 **1553 passed / TOTAL 61.50% / 门槛 60 reached** ✓ ｜ CI ✓。

## [2.10.17] - 2026-10-20

### Changed（**覆盖率门槛 56 → 60** ✓ ＋ 基线全面刷新 ✓）

- **依据是实测** ✓：跑了一次本地全量 —— **1553 passed / 103 秒** ✓ ⇒ 总覆盖率 **61.50%** ✓
  （18,996 语句 / 未覆盖 7,313 ✓）。比上次（58.23%）涨 **3.27pp** ✓ ——
  主要来自 `single_test`（22.18 → 43.4 ✓）、`panel_expr`（27.7 → 42.5 ✓）、`ops_ext`（55 → 62.5 ✓）。
- **⚠ 门槛只抬到 60，不是 61** ✗→✓：按口径"门槛 = 实测值向下取整"应是 **61** ✓，
  但 **`gh run view --log` 里看不到 `TOTAL` 行** ✗ ⇒ **CI 实测值取不到** ✓
  ⇒ 只用本地数时**宁可保守一档** ✓（本地 61.50 / CI 历来略高 ⇒ 60 留 1.5pp 余量 ✓）。
  这条口径已写进 CI 注释与《开发记录》✓，避免下一个人以为"少算了一位"✗。
- **《开发记录》基线刷新** ✓（v2.10.17）：
  · 后端 **1553 passed**（本地全量 ✓）｜ 覆盖率 **61.50%** ｜ 门槛 **60** ✓；
  · **三道闸门**写清楚 ✓：`mypy` **18 个文件 0 错误** ✓（含"必须保留 `follow_imports = silent`"的警告 ✓，
    以及**故意未收**的三个文件与理由 ✓）、`pytest + 覆盖率门槛` ✓、`playwright` **3 条** ✓；
  · 模块覆盖按当前实测逐个列出 ✓（`signals/engine` 90.2 ｜ `inst_mask` 85.2 ｜ `analysis` 84.7 ｜
    `charts` 79.7 ｜ `ops_ext` 62.5 ｜ `event_study` 55.5 ｜ `metrics` 45.5 ｜ **`single_test` 43.4** ｜
    `panel_expr` 42.5 ｜ `export_xlsx` 36.5 ｜ `artifacts` 34.5 ｜ `pricing` 21.4 ✓）；
  · **★ 记下一条教训** ✓：判断"某函数要不要 mock qlib"**必须读签名** ✗ 不能凭模块名猜 ——
    `_test_one` 被我在 5 个版本里反复写成"需要三件套"✗，实际只收 `DataFrame` ✓；
    并记下 **docstring 与 `_NR` 白名单不同步**（少写 7 列 ✗）✓。
- **校验** ✓：本地全量 **1553 passed** ✓ ｜ CI（新门槛 **60** ✓）｜ `mypy` **18 文件 0 错误** ✓ ｜
  `ruff` **0** ✓ ｜ `npm test` **181 passed** ✓ ｜ `playwright` **3 passed** ✓。

## [2.10.16] - 2026-10-20

### Test（**C′ 第一刀：312 行的 `_test_one` 终于能测了** ✓ —— `single_test.py` **22.18% → 43.28%** ✓）

- **⭐⭐ 先纠正一个我自己重复多次的错误结论** ✗→✓：
  前几轮我一直写"`_test_one` 要 mock「数据集+模型+recorder」三件套 ✗ ⇒ 有意不做"✓ ——
  **读签名才发现它根本不碰 qlib** ✓：`_test_one(df, factor, col, ...)` 收的是**已经准备好的
  DataFrame** ✓，docstring 也写明要哪几列 ✓ ⇒ **一张合成面板就能跑** ✓。
  ⇒ 教训：**"需要 mock 什么"必须去读函数签名，不能凭模块名推断** ✗
  （`single_test.py` 名字里有 qlib 味 ✓，但它 90% 是纯 pandas 计算 ✓）。
- 新增 `tests/test_single_test_one.py`（**8 条 ✓**）：
  · **契约字段齐全** ✓（`id/name/source/coverage/nonzero_ratio/is_binary/grouping/
    quintile_ret/ic/rank_ic/n_obs/limit_up_excluded/...` ✓ —— 少一个前端就少一块 ✓）；
  · **连续因子** ⇒ `is_binary=False` ✓；**0/1 因子** ⇒ `is_binary=True` 且**必须**给出
    `trigger` / `not_trigger` 两组 ✓（缺一组就没有对照组 ✓）；
  · **⭐ 覆盖率分母只算 `CLOSE` 有效行** ✓：把一只票的 `CLOSE` 全置 NaN ⇒ 分母**必须正好少 NDAY** ✓
    （旧口径用"面板并集行数"⇒ 会随同桌因子变 ✗）；
  · **同一张表跑两次完全一致** ✓（无隐藏状态 ✓）；
  · **四个剔除开关任意单个开启都不抛** ✓（标签列不存在时必须当"没有"✓，否则用户一勾就 500 ✗）。
- **✗✗ 两次 `KeyError` 才把夹具补齐（docstring 与实现不同步）**：
  源码 `_NR` 白名单实际要 `CLOSE_FF / OPEN / OPEN_FF / T1_OPEN / LIMIT_UP / T1_LIMIT_UP /
  IS_ST / T1_IS_ST` ✓ —— 而 docstring 只写了 6 列 ✗；`T1_LIMIT_UP` 缺了直接炸 ✓。
  ⇒ 夹具一次造全 ✓（缺的置 NaN = "没有标签"⇒ 不剔除 ✓），并把"docstring 不同步"记在这里 ✓。
- **实测** ✓：八份 `single_test` 用例合计覆盖该模块 **22.18% → 43.28%** ✓（1,213 语句 ✓），
  **75 passed** ✓，相关回归 **128 passed** ✓。
- **校验** ✓：8 passed（3.6 秒 ✓）｜ `ruff` **0 错误** ✓ ｜ CI ✓。

## [2.10.15] - 2026-10-20

### Changed（**mypy 闸门 17 → 18 个文件** ✓ —— `factors/benchmark_curves.py` 一次清掉 8 条 ✓）

- 这个模块的 8 条错误**同一个根因** ✓：`cums, cums_cmp = [], []` ✓ 之后既 `append(None)` ✓
  （缺价的期 ⇒ 前端**断线** ✓）又 `append(float)` ✓ ⇒ 空 list 不标注时，mypy 按第一次
  `append(float)` 推成 `list[float]` ✗ ⇒ 后面 6 处 `append(None)` 全报 `arg-type` ✓
  （外加 2 条 `var-annotated` ✓）。
  ⇒ 标成裸 `list`（= `list[Any]` ✓）**只改注解，语义一字未动** ✓。
- **⚠ `factors/parser/codegen.py` 仍未收** ✗（8 条 ✓）：那几条**不是注解问题** ——
  `"Expr" has no attribute "value"` ✓、`Cannot call function of unknown type` ✓、
  以及 `998~1026` 行同一变量先装 `str` 又装 `float/int` ✗ ⇒ 都要先读懂那段代码生成的数据流 ✓
  （改错会**生成错公式**✗，而公式错只会体现为"因子值不对"✓）⇒ 单独一轮 ✓，不硬塞 ✗。
- **校验** ✓：`mypy` **0 errors（18 files）** ✓ ｜ `ruff` **0** ✓ ｜ 相关回归（benchmark/curve）**56 passed** ✓ ｜ CI ✓。

## [2.10.14] - 2026-10-20

### Changed（**mypy 闸门扩到 17 个文件** ✓ —— `services/` 与 `datasource/` 各一圈 ✓）

- 从 12 → **17** 个文件 ✓（仍 **0 错误** ✓）：新增 `services/error_policy.py` ✓、`services/expr_cache.py` ✓、
  `services/custom_formulas.py` ✓、`datasource/qlib_source.py` ✓、`datasource/tdx_gpcw.py` ✓。
  这一批统一是"**注解与实现不一致**"✗，**只改注解** ✓、一行行为没动 ✓。
- **⭐ 两类值得记的修法**：
  1. **`None` 被 mypy 冻结成 `None` 类型** ✗→✓：`datasource/qlib_source.py` 里 `self._D = None` ✓
     等 lazy init 才换成 `qlib.data.D` ✓ ⇒ 不标注时 mypy 认为它**永远是 `None`** ✗ ⇒
     `D.features / list_instruments / calendar` 三处全报"`None` 没有该属性"✓ ⇒ 标 **`Any`** ✓
     （它的类型**依赖运行时是否装了 qlib** ✓，静态上无从确定 ✓）；
  2. **同一个名字被复用成两种类型** ✗→✓：`custom_formulas.py` 里 `cur` 先装 `int`（tomb 的值 ✓）
     又被拿去装元组 ✓ ⇒ 换个名字即可 ✓。这类"同名换类型"是最容易让类型检查失效的写法 ✓。
- 其余 ✓：`error_policy` 的 `detail` 会从 `str` 变成**元组** ✓（两种形状都传给 logger ✓）⇒ 标 `object` ✓；
  `expr_cache.prune` 的三个默认值是 `None` 而注解是 `float/int` ✗ ⇒ 改 `"float | None"` ✓；
  `expr_cache.status` 的 `dict(_STATS)` 被推成 `dict[str, int]` ✗ 而后面要塞 `float` ⇒ 标 `dict` ✓；
  `tdx_gpcw` 有一条**已经失效的 `type: ignore`** ✗ ⇒ 删掉 ✓（`warn_unused_ignores` 会把它当错误 ✓）。
- **⚠ `services/artifacts_service.py` 故意先不收** ✗（7 条 ✓，理由写进 `pyproject.toml`）：
  其中两条不是注解问题 —— `"object" has no attribute "__iter__"` ✓ 与
  `Unsupported target for indexed assignment ("list[str] | dict | str | None")` ✓
  ⇒ 指向**同一坨可变结构在多个分支里被当成不同类型用** ✗ ⇒ 要先读懂数据流（可能顺手改结构 ✓）
  才敢动 ✓ —— 单独一轮 ✓，本轮不硬塞 ✗。
- **校验** ✓：`mypy` **0 errors（17 files）** ✓ ｜ `ruff` **0** ✓ ｜
  相关回归（expr/cache/formula/datasource/tdx/error）**405 passed** ✓ ｜ CI ✓。

## [2.10.13] - 2026-10-20

### Test + Changed（**E2E 补完最后一块：结果面板真的渲染出指标卡** ✓）

- 上一轮留下的硬骨头是"**结果面板渲染**"✗ —— 这一轮把它解决了 ✓，靠的是**两个稳定选择器** +
  一次追因 ✓。
- **⭐ 追因结论（关键）**：结果面板的**唯一入口是历史回测列表的「查看」按钮** ✓ ——
  `handleViewResult` 只挂在 `HistoryPanel` 的那个按钮上 ✓；而**任务卡片只管"选中"** ✗
  （`handleSelectTask` 只 `setTask` + 拉 `artifacts` ✓，**不请求 `/result`** ✗）。
  ⇒ 用例必须：先 stub 一条历史记录 ✓ → 点「查看」✓ → `/result` 才被请求 ✓ → 指标卡出现 ✓。
- **加了两个 `data-testid`** ✓（纯增量 ✓，不改行为 ✓）：
  · `TaskStatusPanel` 的任务卡：`task-card-<id>` ✓ + `data-task-status` ✓；
  · `HistoryPanel` 的「查看」按钮：`history-view-<id>` ✓。
  理由写进注释 ✓：原来只能**按文本点** ✗，而任务列表**持久化在 localStorage** ✓、历史区又有同名文本
  ⇒ 实测会点到**别人的任务** ✗（上一轮探针里那串 `GET /api/backtest/<别人的id>/snapshot` ✓ 就是这么来的 ✓）。
- **断言用 `夏普比率` 而不是 `年化收益`** ✓：后者在**历史表格列头**里也出现 ✓
  ⇒ 用它断言会**永远通过**✗（v2.10.7 那次假通过就是它 ✓，a11y 树里的 `columnheader "年化收益"` 暴露的 ✓）。
- **✗ 三个 Playwright 细节坑（都被实测抓出 ✓）**：
  1. `getByText(TASK_ID)` 会**同时**命中任务卡与历史行 ✓ ⇒ strict mode 报
     `resolved to 2 elements` ✗ ⇒ 必须 `.first()` ✓；
  2. `getByText('最大回撤')` 会**连带命中「超额最大回撤」** ✓（子串 ✓）⇒ 必须 `{ exact: true }` ✓；
     ⚠ 而指标卡标签节点**带空白** ✓ ⇒ 对 `夏普比率` 反而**不能**加 `exact` ✗（上一轮实测 ✓）
     —— **"要不要 exact"取决于该词是否别处有更长的兄弟** ✓，不能一刀切 ✗；
  3. 路由顺序：`**/api/backtests*` 与 `**/api/backtests/history*` 都注册时 ✓，
     **后注册的优先** ✓ ⇒ history 必须在泛化之后注册 ✓（否则永远命中 `{}` ✗）。
- **实测** ✓：`npx playwright test` **3 passed（9.0 秒）** ✓ ｜ `tsc -b` **0 错误** ✓ ｜
  `npm test` **181 passed** ✓（改组件未影响单测 ✓）。
- **校验** ✓：3 passed ｜ CI（e2e job ✓）。

## [2.10.12] - 2026-10-20

### Test + Changed（**A/B/C 三件一起收** ✓ —— E2E 追因 / semantic 注解 / ops_ext 对拍 ✓）

**A｜E2E 往前推一格（写了一个临时探针，追清原因 ✓）**
- 用临时 `_probe.spec.ts`（跑完即删 ✓）记录了"点开始回测之后"的**全部请求** ✓ ——
  结论：前端只请求 `GET /backtest/{id}` 与 `.../artifacts` ✓，**不请求 `/result`** ✗
  ⇒ **结果面板是"选中任务"才加载的** ✓，不是完成后自动渲染 ✓（难怪指标卡不出现 ✓）。
- 顺带发现 ✗：本应用把任务列表**持久化在 `localStorage`** ✓ ⇒ 残留的旧任务会让"点卡片"命中别人 ✓
  （探针里出现一串 `GET /api/backtest/<别人的id>/snapshot` ✓）⇒ 现在用例开头**先清 localStorage** ✓。
- 断言升级为**轮询闭环** ✓：任务必须出现在「任务状态」且显示 **`success` + `100.0%`** ✓
  （把"提交 → 轮询 → 反映到界面"整段覆盖 ✓）；标题同步改成
  「发出创建请求（含载荷契约）→ 轮询到 success 上屏」✓（**标题不许说得比断言多** ✓）。
- **仍未做** ✓：结果面板渲染断言 ✗ —— 已经确认 `夏普比率` 是唯一可用标记 ✓（`年化收益` 会撞历史表头 ✗），
  但"点卡片"需要**稳定选择器**（`data-testid` ✓）+ 确认 `/result` 响应形状 ✓ ⇒ 下一轮 ✓。

**B｜`semantic.py` 的"语义疑点"查清了：是注解写错，代码是对的 ✓**
- 上一版把 `semantic.py` 排除，理由是「`dict` 上调 `.add()`」✓。这轮读完数据流：
  `delayed` **一直是 `set`** ✓（函数体 `.add()` ✓、三处调用都传 `set()` ✓）⇒ **零运行时风险** ✓，
  纯属注解写错 ✓ ⇒ 改注解 ✓、把它**收进 mypy 闸门** ✓。
- 其余 5 条全是"空容器没有元素类型" ✓ ⇒ **只加注解，一行行为没动** ✓。
- 结果：mypy **11 → 12 个文件**，仍 **0 错误** ✓；parser/semantic 相关回归 **237 passed** ✓。

**C｜`ops_ext` 向量化基元 10 条** ✓
- 这一批全是**性能重写** ✓，注释对每个 `_seg` 版都写着「与×××**逐位一致**」✓ —— 只有对拍能验 ✓：
  · 非 seg 版 vs **朴素逐元素实现** ✓（`barslast` / `dyn_window_sum` / `dyn_window_count` / `dyn_ref` ✓）；
  · **单段时 `_seg` 版必须与非 seg 版完全相等** ✓（写错一格截断这里立刻红 ✓）；
  · **段感知要在段起点截住** ✓：构造"第二段开头 3 天"✓ —— 正确得 `1.0` ✓，不截断会得 `21.0` ✗
    （把上一段的 10 带进来了 ✓）。**这类错只在"每只票开头 N 根"上出现** ✗，中段完全正常 ✓；
  · `bars_count_seg` **段内从 1 重新数** ✓ 且 NaN 不计 ✓；
  · `barslast_vec_seg`：命中点落在段起点之前 ⇒ **记 0** ✓。
- **✗ 一处我自己的期望错**：以为段内没有命中会"从旧命中点继续数"✗ ⇒ 实际是 **0** ✓（口径正确 ✓）。
- 实测：10 passed（2.7 秒 ✓），本文件单独跑 `ops_ext` 覆盖 **27.67%** ✓。

- **校验** ✓：`npx playwright test` **3 passed** ✓ ｜ `mypy` **0 errors（12 files）** ✓ ｜
  `ruff` **0** ✓ ｜ 后端相关回归 **237 passed** ✓ ｜ CI ✓。

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

