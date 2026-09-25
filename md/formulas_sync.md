# 公式库同步（多机协作口径）

> 本文是**公式库怎么存、怎么合并、pull/push 各做什么**的唯一权威说明 —— **不依赖任何人的记忆** ✓。
> 相关代码：`backend/app/services/custom_formulas.py`（存储与合并）、`backend/app/services/formula_git_sync.py`（git 同步）。
> 相关脚本：`scripts/formula_sync.py`、`scripts/formula_state_check.py`、`scripts/formula_remote_view.py`。
> 版本沿革：v1.20.63 自动提交推送 → v1.20.65/66 按人分文件 → **v1.20.69 按装机分文件 + 时间戳裁决** → **v1.20.70 默认不自动推送**。

## 一、存储模型

| 项 | 规则 |
|---|---|
| 写在哪 | `backend/workdir/formulas/<install_id>.json` —— **每台机器一个文件** |
| `install_id` | 首次运行随机生成并落盘 `backend/workdir/.install_id`（如 `u-1dc0ce59`），**永不变**；可用环境变量 `QUANT_FORMULA_USER` 显式指定 |
| ⚠ 与用户名无关 | 旧方案用 `<Windows 用户名>.json` ⇒ **两台机器都叫 `admin` 时又写同一个文件**（pull 回到"整文件 JSON 冲突"）⇒ 已废弃 |
| ⚠ `.install_id` **绝不入库** | 随 git 传到另一台机器 ⇒ 两台机器写同一个文件 ⇒ 冲突（`.gitignore` 的 `backend/workdir/*` 已排除它） |
| 老单文件 | `backend/workdir/custom_formulas.json`（v1.20.66 起停止跟踪）**只读兜底**；老按人文件首次运行会自动**改名**成本装机文件 |
| 读 | **合并所有文件**：按 `id` 去重 ⇒ 双方都能看到彼此的公式 |

## 二、合并规则（只增不减 + 时间戳裁决）

- 同 `id`：**`updated_at` 新者胜**（平局：装机文件优先于老单文件，再按文件名定序）—— **不看"是不是自己"** ✓。
- **删除 = 带戳 tombstone**（`{"id":..., "deleted":true, "updated_at":...}`）：只**屏蔽**、不物理删 ⇒
  数据"只增不减"；**对方之后再编辑（时间戳更新）还能"复活"** ✓（这是"谁最后改谁赢"的设计，不是 bug）。
- **别人的条目只读、不回写**（重编陈旧 `expression` 的结果只回写自己文件里已有的条目）。
- ⚠ **同名公式绝不互相覆盖（身份 = `id`，`name` 从不参与裁决 ✗）** —— 用户 2026-09-25 定稿：
  「我改了一堆公式但忘记 push，结果 pull 一下把我改过的全覆盖了」**绝不允许** ✗。
  ⇒ 同名不同 `id` 的两条会**并列共存** ✓（界面上会看到两条同名 —— 这是**故意的** ✓：
  本机那份是你的改动，绝不因"远端有个同名公式"被顶掉 ✓）。
  ⓘ 要判断是不是"同一条"，看 `id` 而不是名字；想清理重复就在界面上删掉旧的，并且**要 push 才算数** ✓。
  （v1.20.73 起由 `backend/tests/test_formula_pull_rollback.py::test_same_name_*` 锁住 ✓。）
- ⚠ 旧的"自己恒胜（恒优先于时间戳）"规则**已删除** —— 它 + "把别人条目抄进自己文件"会导致**永久遮蔽**对方更新。

## 三、pull / push 的语义（v1.20.70 起）

| 动作 | 语义 |
|---|---|
| **保存/删除公式** | **只在本机生效**：写盘 + **本地自动提交**（改动不丢、工作区干净）⇒ **默认不自动推送** |
| **`pull`** | **以"已推送的内容"为准，但只撤销"删除"、不撤销"新增"** ✓：<br>· 本机**新增/编辑** ⇒ 一律**保留** ✓（"我这边的公式要保住"）<br>· 本机**未推送的删标记** ⇒ **撤销** ⇒ 被删公式**恢复显示** ✓（"没 push 就不算数"）<br>· **已推送**的删标记 ⇒ 保留（删除已生效）✓<br>★ v1.20.73 起：撤销删除时**连正文一起还原** ✓ —— 从**已推送副本**（`origin/main:<本装机文件>`）捞回**同 `id` 的活体条目**再写回 ✓。为什么必须这样：App 的删除是「**把活体条目整条替换成 tombstone**」✗ ⇒ 只删 tombstone 的话**正文回不来** ✗（2026-09-25 实测：本机独有的 `深跌2` pull 后**消失**，本机可见比远端视角少 1 条 ✗）。删除既然没推送过，`origin/main` 那边的同 id 条目**仍是活体** ✓ ⇒ 还原是可靠的 ✓。 |
| **`push`** | **明确动作**：只有显式执行才会同步给另一台机器（未推送期间对面 `pull` 永远看不到 ✓） |
| 想恢复自动推送 | `FORMULA_GIT_SYNC_PUSH=1`（`FORMULA_GIT_SYNC=0` 可整体关闭本机制）✓ |

> ⚠ **推代码时会把未推送的公式提交一起带走**（同分支同一 `git push`）⇒ 推送前先看清单 ✓（`formula_sync.py push` 会打印）。

## 四、日常操作（仓库根任意位置）

```powershell
python scripts/formula_sync.py status   # 现状：本机新增 / 未推送删标记 / 未推送提交 / 合并可见条数
python scripts/formula_sync.py pull     # ① 备份 ② 撤销未推送的删标记 ③ git pull --ff-only（带代理）
python scripts/formula_sync.py push     # ① 打印将推送的提交 ② 备份 ③ git push（带代理）④ 打印"对面 pull 后可见条数"
```

只看细项时：

```powershell
python scripts/formula_state_check.py   # 本机 vs origin/main：必须保留的新增 / 需回滚的未推送删标记 / 可见条数
python scripts/formula_remote_view.py   # 远端视角：另一台机器 pull 后会看到几条（用生产合并逻辑算 ✓）
```

- 备份落在 `backend/workdir/formula_backups/<时间戳>/`（在 `.gitignore` 里，不随库走 ✓）。
- ⚠ **`pull` 里"撤销未推送删标记"只会重写本装机的文件**，**其它条目（新增/编辑/已推送删标记）一律不动** ✓。

## 五、排错速查

| 现象 | 原因 / 处理 |
|---|---|
| 另一台看不到我刚保存的公式 | 正常：**默认不推送** ⇒ 需要 `python scripts/formula_sync.py push`（或 `FORMULA_GIT_SYNC_PUSH=1`） |
| 删了公式，pull 之后又回来了 | 正常：**未推送的删除不算数**（pull 会撤销它）⇒ 要真删就 push |
| 删了公式，pull 之后没回来 | 说明该删标记**已经推送过**（删除已生效）⇒ 想恢复就在界面上重新保存一条 |
| **pull 后公式少了一条**（本机可见 ≠ 远端视角） | v1.20.73 之前的 bug：撤销删除时只删 tombstone、**正文没还原** ✗（本机独有的公式会消失 ✗）⇒ 升级后重跑 `python scripts/formula_sync.py pull` 即可 ✓；临时手工捞回：`git show origin/main:backend/workdir/formulas/<install_id>.json` 里按 id 取回**活体条目**写回本机文件 ✓ |
| 界面上出现**两条同名公式** | 正常：同名不同 `id` **并列共存** ✓（同名绝不覆盖 ✓）。多半是你"删了又重存"（⇒ 新 id）或与对面各存了一条 ⇒ 要合并就删掉不要的那条并 **push** ✓ |
| 公式改动一直没进 git | 看启动日志 `[formula-git] ...`（安全打印 ✓）；`git log --oneline -1` 是否有 `chore(formulas): 自动同步…` |
| 接口 500 且日志有 `'gbk' codec can't encode` | 老问题（v1.20.69 已修：模块内 `print` 一律走 `_Utf8SafeStream`）；新加日志时**照做** |
| 用 PowerShell 比对接口返回的中文结果不对 | PS 5.1 的 `Invoke-RestMethod` 会把 JSON 中文解成乱码 ⇒ **换 Python**（`urllib.request` + `json.loads`）核对 |

## 六、改动这一块时的注意事项

1. **动合并规则或写入键**：必须同时更新 `backend/tests/test_formula_store_users.py`（已有 13 例锁住：装机 ID 与用户名无关、
   时间戳裁决、tombstone 可复活、`list` 绝不回写别人条目、迁移）✓。
2. **动 `formula_git_sync`**：`tests/test_formula_git_sync.py` 里有"**老单文件不存在也不能让整条 add 失败**"
   （`git add --force -- <不存在的路径>` 会 `fatal` 并中止整条 add ⇒ 静默不同步 ✗）与"默认不推送"的锁 ✓。
3. 提交前跑仓库自带闸门：`powershell -File scripts/check.ps1`（pytest + ruff + tsc ✓）。
4. **动 pull 的回滚逻辑**（撤销未推送的删除）：看 `backend/tests/test_formula_pull_rollback.py`
   （7 例锁住：**正文必须还原** ✓、已推送的删除**不回滚** ✓、本机新增/编辑**保留** ✓、
   **同名不同 id 并列共存** ✓）—— 核心实现是 `custom_formulas.rollback_unpushed_deletes()`（纯函数 ✓）。
