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

> 校验：`mf_amount_main = mf_amount_xl + mf_amount_l`、`mf_vol_<档> = mf_vol_<档>_b − mf_vol_<档>_s`（勾稽）；源 h5 vs bin 逐日比对已一致（开发机验证）。

### 物化字段：前复权价（`pre*`）+ 筹码（`chip_*`）—— **每台机器必须各做一次**

这两组是**本项目自造的派生字段**（`data/` 已被 .gitignore 排除）⇒ **既不在数据包里、也不在 git 里**，
必须在目标机器上各生成一次。⚠ **缺了不报错、会静默失效**（最坑的地方）：

| 缺失字段 | 后果 |
|---|---|
| `preclose / preopen / prehigh / prelow / prevwap` | **`forward`（前复权）失效** ⇒ 价格量纲因子被按**后复权价**排序（LLT K=20 年化 +9.03% ↔ 米筐 −5.24%，见 change_log `[1.19.96]`） |
| `chip_cost_{5,30,75,95}` / `chip_win_{close,high,low}` | 用 `COST()/WINNER()` 的公式（过顶 / 黏合强突破 / 蹦极新生 …）**静默返回全 NaN**（`panel_expr._chip_or_bin` 优先读 bin、**不检查文件是否存在**） |

生成与核对（脚本在 **`backend/tools/`**，与 `dump_moneyflow.py` 同处；cwd 任意；实测耗时：`pre*` 约 8 分钟、`chip_*` 约 9 分钟）：

```bash
python backend/tools/build_preclose.py            # 前复权：5 字段 × ~6141 只
python backend/tools/materialize_chip.py 400      # 筹码：7 字段 × ~6141 只（分批；可中断续跑）
python backend/tools/verify_materialized.py       # 核对覆盖率/同轴/NaN/数值；退出码 0 才算好
```

- 两者默认**只补缺失**（`build_preclose.py --overwrite` 可全量重写；`materialize_chip.py` 靠 `overwrite=False` 天然断点续跑）；
- ⚠ **行情数据更新后必须重跑 `build_preclose.py`**（`factor_last` 变 ⇒ 整条历史价缩放）；
- ⚠ `materialize_chip.py` **必须分批**（默认 400 只/批）：`chip_store.materialize` 一次吃全池会构造
  "全池一次性面板" ⇒ 实测 **20 分钟 0 个文件、CPU 仅 ~22% 单核、ETA 不可估**（2026-09-19 实测教训）；
- 口径：`pre* = 源字段 / factor_last`（逐位；`factor_last` = `factor.day.bin` 末值，五字段共用 ⇒ 不会 `prehigh < prelow`）；
  `chip_*` 必须与 `$close` **同轴**（首值+长度一致，否则多股票一起加载会报 `identically-labeled`，见 change_log `[1.19.91]/[1.19.92]`）。

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
