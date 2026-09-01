# 启动 / 停止说明

本平台由**后端**（FastAPI，端口 8001）和**前端**（Vite，端口 5173）两部分组成，
需要分别启动。以下是最简单的开启和停止方法（均通过项目根目录下的 `.bat` 脚本完成）。

> 所有脚本都在 `qlib_code\` 目录下，双击即可运行。

---

## 一、开启（启动）

按顺序执行以下两步：

### 1. 启动后端

**双击运行** `start_backend.bat`

- 弹出黑色命令行窗口，看到 `Uvicorn running on http://0.0.0.0:8001` 即启动成功。
- **注意**：这个窗口**不要关闭**，关闭它后端就停了。
- 脚本已内置 qlib 环境的 python 路径（`D:\miniconda3\envs\qlib\python.exe`），双击即可直接运行，无需手动 `conda activate`。若你的 qlib 环境不在该路径，请修改脚本里的 `set "PYTHON=..."` 一行。

### 2. 启动前端

**双击运行** `start_frontend.bat`

- 看到 `Local: http://localhost:5173/` 即启动成功。
- 这个窗口同样**不要关闭**。

### 3. 打开页面

在浏览器地址栏输入：

```
http://localhost:5173
```

或

```
http://127.0.0.1:5173
```

看到 "Qlib 量化回测平台" 界面即为正常。此时就可以选参数、点"开始回测"了。

> 也可以在编辑器的内置浏览器中打开（输入 `http://localhost:5173`）。

---

## 二、停止

关闭后端和前端，有以下两种方式：

### 方式 A：双击停止脚本（推荐）

1. **双击** `stop_frontend.bat` —— 停止前端（端口 5173）
2. **双击** `stop_backend.bat` —— 停止后端（端口 8001）

脚本会自动找到对应端口的进程并结束，并提示是否成功。

> 若提示"没有找到正在监听的进程"，说明该服务本来就没在运行，属正常。

### 方式 B：直接关闭启动窗口

- 关闭 `start_backend.bat` 的黑窗口 → 后端停止
- 关闭 `start_frontend.bat` 的黑窗口 → 前端停止

> 用 `Ctrl+C` 或直接点窗口右上角 ✕ 关闭均可。**未关闭的窗口里进程会一直运行**，
> 所以长期不用时建议用方式 A 或关闭窗口彻底停掉。

---

## 三、常见问题

| 问题 | 解决 |
|---|---|
| 启动后端报"端口被占用" | 8001 被占，先运行 `stop_backend.bat` 或改 `start_backend.bat` 里的端口 |
| 启动前端报"端口被占用" | 5173 被占，先运行 `stop_frontend.bat` 或改 `start_frontend.bat` 里的端口 |
| 浏览器打不开页面 | 确认前后端两个窗口都在运行，且端口无报错 |
| 回测后页面无结果 | 确认后端窗口有日志输出、无红色报错 |
| bat 里中文乱码 / 报 `xxx is not recognized` | 脚本已改为纯英文（ASCII），可避免乱码。若仍异常，确认文件是无 BOM 的 UTF-8/ANSI 编码 |

---

## 四、相关文件

| 文件 | 作用 |
|---|---|
| `start_backend.bat` | 启动后端（FastAPI，8001） |
| `start_frontend.bat` | 启动前端（Vite，5173） |
| `stop_backend.bat` | 停止后端 |
| `stop_frontend.bat` | 停止前端 |

---

## 五、后台静默启动与 IDE 内预览

> 前三节是"开着命令行窗口"的常规方式；本节是**不占窗口**的后台静默启动，
> 以及在 **IDE 内置浏览器**里预览页面的方法。

### 5.1 在 IDE 里打开预览

CodeBuddy 右侧有菜单按钮（三个点 `...`），点开后有两项：

- **预览**：点击后由 AI 调用内置 Preview 能力，在 IDE 内打开预览面板。
- **打开浏览器**：点击后会在 IDE 里打开一个内置浏览器窗口，在其中输入
  `http://localhost:5173` 即可预览页面。

**推荐顺序**：先启动后端（8001）→ 再启动前端（5173）→ 最后打开预览。
前后端谁先谁后其实都能跑（Vite 代理是运行时转发的），但按这个顺序最稳妥，
保证页面打开后接口不会报错。

### 5.2 后台静默启动（PowerShell）

> 在 PowerShell 里逐条执行。`Start-Process` 表示新起一个后台进程，
> `-WindowStyle Hidden` 表示不弹窗口。

**1. 启动后端（端口 8001）**

```powershell
cd D:\quant\qlib_code\backend
Start-Process -FilePath "D:\miniconda3\envs\qlib\python.exe" `
  -ArgumentList "-m","uvicorn","app.main:app","--host","0.0.0.0","--port","8001" `
  -WorkingDirectory "D:\quant\qlib_code\backend" `
  -RedirectStandardOutput "D:\quant\qlib_code\backend\server.log" `
  -RedirectStandardError "D:\quant\qlib_code\backend\server_err.log" `
  -WindowStyle Hidden
```

- 日志写到 `backend\server.log`（出错时查看）。
- 注意：`-RedirectStandardOutput` 和 `-RedirectStandardError` **不能指向同一个文件**，所以日志和错误分两个文件。

**2. 启动前端（端口 5173）**

```powershell
cd D:\quant\qlib_code\frontend
Start-Process -FilePath "cmd.exe" `
  -ArgumentList "/c","npm run dev > vite.log 2>&1" `
  -WorkingDirectory "D:\quant\qlib_code\frontend" `
  -WindowStyle Hidden
```

- 日志写到 `frontend\vite.log`。

**3. 验证两个服务是否起来**

```powershell
Invoke-WebRequest -Uri "http://localhost:8001/docs" -UseBasicParsing | Select-Object StatusCode
Invoke-WebRequest -Uri "http://localhost:5173"  -UseBasicParsing | Select-Object StatusCode
```

- 都返回 `200` 即正常；前端首次启动要等几秒，报错可过 3 秒再试。

**4. 打开预览**

- 方式 A：命令行打开系统默认浏览器

  ```powershell
  Start-Process "http://localhost:5173"
  ```

- 方式 B：用 5.1 的 IDE 入口（推荐），点 CodeBuddy 右侧三个点 → "打开浏览器"，在 IDE 内置浏览器里输入 `http://localhost:5173`。

### 5.3 停止服务（按端口找进程）

后台启动没有窗口可关，停止用端口反查进程：

```powershell
# 停后端
Get-NetTCPConnection -LocalPort 8001 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }

# 停前端
Get-NetTCPConnection -LocalPort 5173 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```

先查看端口占用（确认 PID 再杀更安全）：

```powershell
Get-NetTCPConnection -LocalPort 5173,8001 -State Listen | Select-Object LocalPort,OwningProcess
```

### 5.4 日常速查

| 操作 | 命令 |
|---|---|
| 启动后端 | 见 5.2 第 1 步 |
| 启动前端 | 见 5.2 第 2 步 |
| 验证服务 | `Invoke-WebRequest -Uri "http://localhost:8001/docs" -UseBasicParsing` |
| 开系统浏览器预览 | `Start-Process "http://localhost:5173"` |
| IDE 内置浏览器预览 | 点 CodeBuddy 右侧三个点 → "打开浏览器" → 输入 `http://localhost:5173` |
| 看后端日志 | `Get-Content D:\quant\qlib_code\backend\server.log -Tail 50 -Wait` |
| 看前端日志 | `Get-Content D:\quant\qlib_code\frontend\vite.log -Tail 50 -Wait` |
| 停后端 | `Get-NetTCPConnection -LocalPort 8001 -State Listen \| ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }` |
| 停前端 | `Get-NetTCPConnection -LocalPort 5173 -State Listen \| ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }` |

> 提示：若提示"没有找到正在监听的进程"，说明该服务本来就没在运行，属正常。
