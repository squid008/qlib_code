# 两地 Git 工作流（家 + 公司协作）

本项目的代码托管在 GitHub（`https://github.com/dev/qlib_code`），开发环境有**两地**：家 和 公司。
本说明用于两地开发者统一认识 git 协作方式，避免出现"无共同祖先导致合并困难"的问题。

---

## 核心原则

> **家和公司都从同一个远程仓库（GitHub）拉取和推送，永远不要在任何一方单独 `git init` 一个新仓库。**

只要两地都基于 `origin`（GitHub 远程仓库）工作，就会有共同的提交历史，`git pull` / `git push` 都会顺畅。

---

## 远程仓库

- 地址：`https://github.com/dev/qlib_code.git`
- 默认分支：`main`
- 认证：HTTPS + 访问令牌（系统凭据存储自动保存）

---

## 一、首次拉取（新环境，如公司电脑首次）

如果某台电脑还没有这个项目，用 `git clone` 而不是自己 init：

```bash
cd 想放项目的目录
git clone https://github.com/dev/qlib_code.git
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
