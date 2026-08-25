# 手动提交 / 上传 GitHub 指南

本文件的改动**默认不自动推送到 GitHub**，由你手动执行提交。以下是常用命令。

> 仓库地址：https://github.com/dev/qlib_code
> 本地分支：`main`

---

## 一、日常提交（三步）

每次改完代码后，打开终端（在 `D:\quant\qlib_code` 目录下），执行：

```powershell
cd D:\quant\qlib_code

# 1. 暂存所有改动
git add -A

# 2. 提交（-m 后面写本次改动的说明）
git commit -m "本次改动的说明，例如：修复分层回测基准线"

# 3. 推送到 GitHub
git push
```

- 第 1、2 步是**本地提交**（不联网，随时可做）
- 第 3 步 `git push` 才是**上传到 GitHub**（需要网络/代理）

---

## 二、查看状态

```powershell
# 查看有哪些文件改动了（未提交）
git status

# 查看最近几次提交记录
git log --oneline

# 查看某个文件改了什么
git diff 文件名
```

---

## 三、取消/回退（万一提交错了）

```powershell
# 撤销最后一次提交（保留改动，回到未提交状态）
git reset --soft HEAD~1

# 彻底丢弃最后一次提交（慎用，会丢改动）
git reset --hard HEAD~1
```

---

## 四、首次在新电脑拉取代码

```powershell
git clone https://github.com/dev/qlib_code.git
cd qlib_code
```

---

## 提醒

- **不用每次都 push**：本地 `git commit` 就可以保存版本，`git push` 只是同步到 GitHub。
- **提交前可自查**：`git status` 看是否有不该上传的大文件/日志（正常应只看到源码和配置，不含 `data/`、`node_modules/`、`*.log`）。
