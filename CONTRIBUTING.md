# 贡献指南（CONTRIBUTING）

> 这份文件只写**必须遵守的规矩**，以及"在哪查"。
> 项目的技术背景、评分与取舍都写在 `md/开发记录.md`；未还的债在 `md/技术债.md`。

## 1. 跑起来

```bash
# 后端（Python 3.10，conda env 名 qlib）
pip install -r backend/requirements.txt -r backend/requirements-dev.txt -c backend/constraints.txt
scripts/restart_backend.ps1          # 8001

# 前端
cd frontend && npm install
npm run dev                          # 5173
```

**本地下载要走代理**（外网）：`HTTP_PROXY/HTTPS_PROXY=http://127.0.0.1:7897`。

## 2. 提交前必须过这几道（CI 也全跑）

```bash
cd backend
python -m ruff check app tests        # 静态检查（含 F821 = 未定义名，**搬家漏依赖靠它**）
python -m mypy                        # 整个 app，0 错误
python -m pytest -q -m "not datareq"  # 全量（datareq 需真数据，CI 也会跳）

cd ../frontend
npx tsc -b                            # ⚠ vitest **不查类型**，必须单独跑
npm run lint                          # biome
npx vitest run
```

## 3. 硬规矩（都是踩过坑才立的）

| 规矩 | 为什么 |
|---|---|
| **版本号三处一起改**：`backend/app/__init__.py`、`frontend/src/version.ts`、`package.json`（+ lock） | `tests/test_version_sync.py` 会红；漏一处就"文档说 A、实际 B" |
| **改版本号必须同时写 `md/change_log.md` 的 `## [x.y.z]` 段** | 同一条闸门检查；漏了就是"升级了但没记录" |
| **`>300 行函数` 只减不增**（`tests/test_code_health.py` 的 `BIG_DEBT`） | 拆不动时**不许**调大数字 ✗ —— 那等于把尺子改短 |
| **圈复杂度只减不增**（`tests/test_complexity.py` 的 `FROZEN`） | 同上；新函数超阈值就得拆（早返回 / 抽小函数 / dict 派发） |
| **`mypy` 是整包**（`files = ["app"]`） | 新模块**默认就在闸门里**，不会"悄悄没检查" |
| **抽"状态字典"必须用 `TypedDict`** | 普通 `dict` 会把键取成 `object` —— 本项目为此栽过 3 次（最多一次 108 条类型错） |
| **抽代码块后立刻查"块内定义的名字后面还有人用吗"** | `ruff` 的 F821 会点名；靠通读查不出来（栽过 3 次） |
| **多行表达式里别把 `# type: ignore` 塞在中间** | 注释会吞掉行尾的 `)`，直接 `invalid-syntax` |
| **改文件用编辑工具/脚本文件，别写内联 `python -c`** | PowerShell 会撕碎嵌套引号（栽过 6 次） |

## 4. 加测试的优先级

**优先钉"错了不报错、只是结果悄悄变"的地方**，而不是"能提覆盖率"的地方：

- 数值口径（净值 / 成交 / 覆盖率 / 持仓）⇒ 用 `tests/test_engine_golden*.py` 的**指纹**（合成面板 + 固定种子，跨 Windows/ubuntu 同 sha ✓）；
- 用户能看到的**报错文案** ⇒ 逐条断言（合成一句会把人引向错误方向）；
- 前端**带副作用**的操作（导出 / 提交 / 轮询）⇒ 端到端，别只测渲染。

## 5. 提交信息

用 `type(scope): 说明`（`feat` / `fix` / `refactor` / `test` / `chore` / `docs`），
正文里写**为什么**（尤其是"这个改动避免了什么静默错"）—— `git log` 就是二线的文档。
