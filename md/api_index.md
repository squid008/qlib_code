# API 索引（自动生成 ✓ **别手改** ✗）
> 由 `backend/tools/gen_api_index.py` 从 `openapi.json` 生成（★ P2-B，2026-10-18 ✓）。
> 路径 **51** 条 ｜ 操作 **54** 个。

| 方法 | 路径 | 说明 | tag |
|---|---|---|---|
| `GET` | `/` | 服务健康检查 |  |
| `POST` | `/api/backtest` | 提交回测任务 | backtest |
| `GET` | `/api/backtest/capacity` | 查询并发回测能力与资源摘要（前端超限提示） | backtest |
| `DELETE` | `/api/backtest/{task_id}` | 删除某个回测的产物目录 | backtest |
| `GET` | `/api/backtest/{task_id}` | 查询回测任务状态 | backtest |
| `GET` | `/api/backtest/{task_id}/artifacts` | 查询任务的模型交付物（训练公式/权重/超参数/特征） | backtest |
| `POST` | `/api/backtest/{task_id}/cancel` | 停止回测任务 | backtest |
| `GET` | `/api/backtest/{task_id}/features` | 特征名 ↔ 公式对照（特征表展示 / 下载 CSV） | backtest |
| `POST` | `/api/backtest/{task_id}/force-cancel` | 强制停止回测任务（终止取数进程 + 立刻落终态） | backtest |
| `GET` | `/api/backtest/{task_id}/image/{name}` | 获取产物图片（曲线/参数快照） | backtest |
| `GET` | `/api/backtest/{task_id}/partial` | 读取滚动回测已跑段的部分结果（partial_result.json） | backtest |
| `GET` | `/api/backtest/{task_id}/result` | 读取持久化的回测完整结果（指标/净值/调仓记录） | backtest |
| `POST` | `/api/backtest/{task_id}/resume` | 断点续跑（未完成的滚动回测） | backtest |
| `GET` | `/api/backtest/{task_id}/snapshot` | 查询任务的产物目录信息（含曲线/参数快照图、参数） | backtest |
| `GET` | `/api/backtests` | 列出所有回测任务 | backtest |
| `GET` | `/api/backtests/history` | 从 artifacts 目录扫描所有回测产物（跨重启/跨版本） | backtest |
| `GET` | `/api/data-sources` | 列出可用数据源及其能力 | data |
| `GET` | `/api/data/calendar` | 获取交易日历 | data |
| `GET` | `/api/data/daily-bars` | 获取日线数据 | data |
| `GET` | `/api/data/financial` | 获取财报数据 | data |
| `GET` | `/api/data/index-constituents` | 获取指数成分股 | data |
| `GET` | `/api/data/industry` | 获取行业分类 | data |
| `GET` | `/api/data/instruments` | 列出证券代码 | data |
| `GET` | `/api/data/minute-bars` | 获取分钟数据 | data |
| `GET` | `/api/datasets` | 列出可用数据集与当前生效项 | datasets |
| `POST` | `/api/datasets/active` | 切换当前数据集（全局生效 ✓） | datasets |
| `GET` | `/api/factors/catalog` | 获取特征集因子目录 | factors |
| `GET` | `/api/factors/custom-formulas` | 列出已保存的自定义公式 | factors |
| `POST` | `/api/factors/custom-formulas` | 编译并保存自定义公式 | factors |
| `DELETE` | `/api/factors/custom-formulas/{formula_id}` | 删除自定义公式 | factors |
| `PUT` | `/api/factors/custom-formulas/{formula_id}` | 编辑自定义公式（重新编译并保存） | factors |
| `POST` | `/api/factors/custom-formulas/{formula_id}/pin` | 置顶 / 取消置顶自定义公式 | factors |
| `GET` | `/api/factors/datasets` | 列出所有可用特征集 | factors |
| `POST` | `/api/factors/event-study` | 事件研究（0/1 信号触发后收益分布，异步提交） | factors |
| `POST` | `/api/factors/event-study/cancel/{task_id}` | 取消事件研究任务 | factors |
| `POST` | `/api/factors/event-study/clear` | 清理已结束的事件研究任务与结果 | factors |
| `POST` | `/api/factors/event-study/nav` | 事件研究的净值曲线（两种资金方案 + 基准） | factors |
| `POST` | `/api/factors/event-study/nav/cancel` | 取消净值曲线计算（关弹窗时调用） | factors |
| `GET` | `/api/factors/event-study/progress/{task_id}` | 查询事件研究任务进度 | factors |
| `POST` | `/api/factors/export/event-study-xlsx` | 导出事件研究（XLSX） | exports |
| `POST` | `/api/factors/export/exit-curve-xlsx` | 导出「离场曲线」（XLSX：截图 + 每个离场一页调仓记录） | exports |
| `POST` | `/api/factors/export/topk-xlsx` | 导出单因子测试「持仓期收益曲线」（XLSX） | exports |
| `GET` | `/api/factors/operators` | 列出翻译器支持的算子分类 | factors |
| `POST` | `/api/factors/single-factor-test` | 单因子测试（不训练模型，异步提交） | factors |
| `POST` | `/api/factors/single-factor-test/cancel/{task_id}` | 取消单因子测试任务 | factors |
| `POST` | `/api/factors/single-factor-test/clear` | 清理已结束的单因子测试任务与结果（释放内存） | factors |
| `GET` | `/api/factors/single-factor-test/progress/{task_id}` | 查询单因子测试任务进度 | factors |
| `GET` | `/api/factors/single-factor-test/tasks` | 列出最近的单因子测试任务 | factors |
| `POST` | `/api/factors/translate` | 翻译益盟/通达信公式为 qlib 表达式 | factors |
| `GET` | `/api/signal-test/options` | 交易信号测试的可选项（基准/池/成交时点/资金方案） | signal-test |
| `POST` | `/api/signal-test/parse` | 只解析 CSV（上传后立刻给出识别结果与诊断） | signal-test |
| `POST` | `/api/signal-test/run` | 跑交易信号测试（同步返回：事件研究 + 回测 / 或聚宽三曲线） | signal-test |
| `GET` | `/api/version` | 版本号 |  |
| `GET` | `/health` | 健康检查 |  |
