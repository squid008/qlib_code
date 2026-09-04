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
| `qlib_bin.tar.gz` | ✅ | A股日线数据（需解压） |
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

1. 解压 `qlib_bin.tar.gz`，得到 `cn_data` 数据目录
2. 数据路径通过 **环境变量** 或 **目录约定** 指定（不用改代码）：

   **方法1（推荐）设环境变量：**
   ```bash
   set QLIB_PROVIDER_URI=D:\你的数据路径\cn_data
   ```

   **方法2：放到项目目录下**
   把 `cn_data` 放到 `qlib_code\data\cn_data`（后端会自动识别）

   **方法3：放到当前用户主目录**
   `C:\Users\你的用户名\.qlib\qlib_data\cn_data`

> 配置优先级：环境变量 > 项目内 data/cn_data > 主目录 .qlib

### 资金流向（moneyflow）数据（v1.6.5+，资金流/L2 因子用）

资金流字段（`mf_amount_*` / `mf_pct_*`，共 10 个）以 bin 形式随 `cn_data` 存放：
`cn_data/features/<代码>/mf_*.day.bin`（约 5,451 只 × 10 = 54,510 文件 / 432.5 MB）。
它们**不在 git 仓库**（`data/` 已被 .gitignore），部署/拷贝方式二选一：

- **方式 A（推荐）**：拷贝源 h5 目录 `E:\rq\moneyflow\`（399.6 MB，含 `sid.h5` + `mf_2016~mf_2026.h5`）后执行：
  ```bash
  python backend/tools/dump_moneyflow.py            # 默认写入 D:\quant\qlib_code\data\cn_data
  python backend/tools/dump_moneyflow.py --qlib-dir <你的cn_data路径>   # 自定义数据目录
  ```
- **方式 B**：打包/拷贝 `cn_data/features/` 下所有 `mf_*.day.bin`（保持目录结构），解压到目标 `cn_data/features/`。

**数据更新（服务器每天跑）**：更新行情日历后执行 `python backend/tools/dump_moneyflow.py --force`（幂等全量重写，约 1 分钟）即可补新交易日资金流；若行情 bin 重建（日历重排/前插日期），需与 moneyflow 基于同一天历一起重 dump，否则字段错位。

> 校验：`mf_amount_main = mf_amount_xl + mf_amount_l`（勾稽）；源 h5 vs bin 逐日比对已一致（开发机验证）。

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

## 附：如何重新导出依赖（在家更新后）
```bash
conda activate qlib
pip freeze > requirements_qlib.txt        # 生成 pip 依赖
conda env export --no-builds > qlib_env.yml  # 生成 conda 环境
```
