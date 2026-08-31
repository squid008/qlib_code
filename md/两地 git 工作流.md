# 两地 Git 工作流（家 + 公司协作）

本项目的代码托管在 GitHub（`https://github.com/squid008/qlib_code`），开发环境有**两地**：家 和 公司。
本说明用于两地开发者统一认识 git 协作方式，避免出现"无共同祖先导致合并困难"的问题。

---

## 核心原则

> **家和公司都从同一个远程仓库（GitHub）拉取和推送，永远不要在任何一方单独 `git init` 一个新仓库。**

只要两地都基于 `origin`（GitHub 远程仓库）工作，就会有共同的提交历史，`git pull` / `git push` 都会顺畅。

---

## 远程仓库

- 地址：`https://github.com/squid008/qlib_code.git`
- 默认分支：`main`
- 认证：HTTPS + 访问令牌（系统凭据存储自动保存）

---

## 一、首次拉取（新环境，如公司电脑首次）

如果某台电脑还没有这个项目，用 `git clone` 而不是自己 init：

```bash
cd 想放项目的目录
git clone https://github.com/squid008/qlib_code.git
cd qlib_code
```

> ⚠️ **不要 `git init`！** 直接 clone 就能拿到完整历史，避免制造独立仓库。

---

## 二、日常两地提交（最常用）

### 在 家 或 公司 工作结束，要上传时：

```bash
cd qlib_code
git pull              # 1. 先拉取另一地的最新改动（重要！）
git status            # 2. 看改了哪些文件
git add -A            # 3. 暂存所有改动
git commit -m "改动说明，简要描述这次改了什么"   # 4. 提交
git push              # 5. 推送到 GitHub
```

### 每次动工前：

```bash
cd qlib_code
git pull              # 拉取另一地最新代码，避免冲突
```

---

## 三、协作建议（重要）

在任一环境开始改动前，按以下顺序操作，保证两地代码同步、不冲突：

1. **先读 `README.md`** —— 里面有项目的完整架构、启动方式、数据源、功能说明。
2. **先 `git pull`** —— 确保拿到的是另一地刚提交的最新代码，不要基于旧代码改。
3. **修改后 `git commit` + `git push`** —— 让另一地能拉到你的改动。

**如果 git 报错**（如 "rejected" / "non-fast-forward"）：
- 说明远程有新提交，先 `git pull` 合并，再 `git push`。
- **不要用 `git push --force` 覆盖远程**（会丢历史），除非明确知道要覆盖。

---

## 四、不要做的事

- ❌ **不要 `git init`**（会制造无共同祖先的独立仓库，合并很痛苦）
- ❌ **不要 `git push --force`**（覆盖远程、丢历史），除非明确知道要覆盖
- ❌ **不要把 node_modules、data、workdir、日志** 提交（已在 .gitignore 排除）

---

## 五、中文 commit message 的写法注意（Windows/PowerShell）

Windows 终端在命令行直接传中文（`git commit -m "中文说明"`）时可能发生 GBK/UTF-8 编码错乱，
导致 commit message 变成乱码（`git log` 里看到 `鏂偣缁祴` 这种）。**建议用 UTF-8 文件方式**：

```bash
# 1. 把提交说明写入 commit_msg.txt（保存为 UTF-8 无 BOM）
# 2. 用文件提交（不经过命令行编码转换）
git commit -F commit_msg.txt
```

**已推送的乱码 message 修正方法**（只改信息，保留改动与作者）：

```bash
git commit --amend -F commit_msg.txt
git fetch origin
git push --force-with-lease origin main   # 覆盖自己最近一次提交（比 --force 安全）
```

> ⚠️ 仅当明确要覆盖**自己刚推送的提交**时才用上面的 force 方式；普通情况下仍遵守第四条"不用 `--force`"。

---

## 六、mlflow 相关文件位置与清理（备忘）

回测平台用 mlflow 做实验追踪，`backend/workdir/` 下会产生以下文件：

- **`mlflow.db`**：mlflow 主实验追踪数据库（`sqlite:///workdir/mlflow.db`），累积**所有历史回测**的 run 记录（参数/指标/产物路径）。只增不减，长期可能涨到上百 MB。
- **`mlflow.db-journal`**：SQLite 预写日志（rollback journal），有未提交事务时存在，检查点后自动清理，正常很小（几十 KB 甚至 0 字节）。

**清理**（当前未加自动清理，需手动）：
- `backend/workdir/mlflow.db`：可删（仅丢失 mlflow 层的 run 索引，**不影响回测功能**——回测真实产物都在 `artifacts/{task_id}/` 下的 `params.json` / `result.json` / 模型文件里）
- 清理前先停止后端，避免 sqlite 文件占用/锁冲突

---

## 七、前端布局经验备忘（App.tsx）

### 问题：某列加按钮后，整行被撑高，下一行输入框下移

回测表单顶部参数区用 `grid grid-cols-2 md:grid-cols-4` 布局，其中第 4 列放按钮。
当初第 4 列只有 2 个按钮（自定义筛选特征 / 使用自定义公式因子）时高度刚好；
后来加了第 3 个按钮（单因子测试），按钮列总高度超过同行输入框列，grid 行高被撑大，
导致下一行（预测周期 / 分层持仓周期 / 持仓周期）整体下移、和上面的输入框错位。

**错误做法**：把第 3 个按钮移到下一行的第 4 列（`items-end` 对底），
虽然输入框不再下移，但按钮和"使用自定义公式因子"隔了一行，看起来零散。

### 解决办法：合并两行 grid + 按钮列 `row-span-2` 跨行

把日期行和预测周期行**合并成同一个 grid**，按钮列加 `md:row-span-2` 跨两行：

```jsx
<div className="grid grid-cols-2 md:grid-cols-4 gap-4 mt-4">
  {/* 第 1 行：开始日期 / 结束日期 / TopK / 按钮列(跨两行) */}
  <label>开始日期...</label>
  <label>结束日期...</label>
  <label>TopK...</label>
  <div className="flex flex-col gap-2 md:row-span-2">
    <button>自定义筛选特征</button>
    <button>使用自定义公式因子</button>
    <button>单因子测试</button>
  </div>
  {/* 第 2 行：预测周期 / 分层持仓周期 / 持仓周期 */}
  <label>预测周期...</label>
  <label>分层持仓周期...</label>
  <label>持仓周期...</label>
</div>
```

要点：
- **必须合并成同一个 grid**，`row-span-2` 才能生效；两个独立 grid 之间无法跨行。
- 按钮列跨两行后，其高度由两行的输入框列决定，**不再撑高任何一行**，输入框位置不变。
- 3 个按钮纵向连续排在第 4 列，"单因子测试"紧跟"使用自定义公式因子"下方。
- 用 `md:row-span-2`（仅 md 及以上生效），小屏 `grid-cols-2` 时按钮列按普通顺序排，不受影响。
- 相关代码位置：`frontend/src/App.tsx` 约 884-983 行的表单参数区。

**通用规律**：凡是在 grid 某列里加高内容、又不想影响同行其他列高度，优先考虑
合并相邻行 + `row-span-*` 跨行，而不是把内容硬塞进单行或挪到别的行。
