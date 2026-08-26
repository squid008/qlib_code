import { useState, useEffect, useRef } from 'react'
import {
  submitBacktest,
  getBacktestTask,
  listDataSources,
  getBacktestArtifacts,
  getBacktestResult,
  cancelBacktest,
  getFactorCatalog,
  getBacktestSnapshot,
} from './api'
import type { BacktestRequest, BacktestTask, DataSourceInfo, ModelArtifacts, FactorCatalog } from './types'
import MetricCards from './components/MetricCards'
import NavChart from './components/NavChart'
import LayerChart from './components/LayerChart'
import ICChart from './components/ICChart'
import TradeLog from './components/TradeLog'
import ModelArtifactsPanel from './components/ModelArtifacts'
import HistoryPanel from './components/HistoryPanel'

// 各模型的超参表单字段定义（占位提示为对应模型的 Qlib/默认值）
interface ModelParamField {
  key: string
  label: string
  placeholder: string
  step?: number
}
const MODEL_PARAM_FIELDS: Record<string, ModelParamField[]> = {
  lightgbm: [
    { key: 'max_depth', label: '最大深度 max_depth', placeholder: 'Qlib默认 8' },
    { key: 'num_leaves', label: '叶子节点数 num_leaves', placeholder: 'Qlib默认 210' },
    { key: 'min_child_samples', label: '叶子最少样本 min_child_samples', placeholder: 'Qlib默认 20' },
    { key: 'learning_rate', label: '学习率 learning_rate', placeholder: 'Qlib默认 0.0421', step: 0.001 },
    { key: 'n_estimators', label: '树数量 n_estimators', placeholder: '默认 100' },
    { key: 'subsample', label: '子采样 subsample', placeholder: 'Qlib默认 0.8789', step: 0.01 },
    { key: 'colsample_bytree', label: '特征采样 colsample_bytree', placeholder: 'Qlib默认 0.8879', step: 0.01 },
    { key: 'reg_alpha', label: 'L1正则 reg_alpha', placeholder: 'Qlib默认 205.7', step: 0.1 },
    { key: 'reg_lambda', label: 'L2正则 reg_lambda', placeholder: 'Qlib默认 580.98', step: 0.1 },
  ],
  xgboost: [
    { key: 'max_depth', label: '最大深度 max_depth', placeholder: 'XGBoost默认 6' },
    { key: 'learning_rate', label: '学习率 learning_rate', placeholder: 'XGBoost默认 0.3', step: 0.01 },
    { key: 'n_estimators', label: '树数量 n_estimators', placeholder: '默认 100（XGB用early_stopping截断）' },
    { key: 'min_child_weight', label: '最小子节点权重 min_child_weight', placeholder: 'XGBoost默认 1' },
    { key: 'subsample', label: '子采样 subsample', placeholder: 'XGBoost默认 1', step: 0.01 },
    { key: 'colsample_bytree', label: '特征采样 colsample_bytree', placeholder: 'XGBoost默认 1', step: 0.01 },
    { key: 'gamma', label: '分裂最小损失减 gamma', placeholder: 'XGBoost默认 0', step: 0.01 },
    { key: 'reg_alpha', label: 'L1正则 reg_alpha', placeholder: 'XGBoost默认 0', step: 0.1 },
    { key: 'reg_lambda', label: 'L2正则 reg_lambda', placeholder: 'XGBoost默认 1', step: 0.1 },
  ],
}

export default function App() {
  const [form, setForm] = useState<BacktestRequest>({
    universe: 'csi300',
    start_date: '2022-01-01',
    end_date: '2023-12-31',
    model: 'LightGBM',
    model_params: {},
    topk: 50,
    n_days_hold: 10,
    label_horizon: 2,
    layer_rebalance: 1,
    n_days_learn: 20,
    data_source: 'qlib',
    feature: 'Alpha158',
    selected_features: null,
    bins: 5,
    deal_price: 'close',
    open_cost: 0.0005,
    close_cost: 0.0015,
    min_cost: 5,
    impact_cost: 0.0005,
    volume_threshold: 0.25,
    limit_threshold: 0.095,
    trade_unit: 100,
    split_mode: 'single',
    train_win: 12,
    train_unit: 'month',
    test_win: 3,
    test_unit: 'month',
    step_win: null,
    step_unit: null,
    initial_capital: 100000000,
  })

  const [dataSources, setDataSources] = useState<DataSourceInfo>({})
  // 起始资金（万元，UI 显示用；提交时转换为元）
  const [capitalWan, setCapitalWan] = useState<number>(10000)
  const [task, setTask] = useState<BacktestTask | null>(null)
  const [artifacts, setArtifacts] = useState<ModelArtifacts | null>(null)
  // 当前展示的回测结果（可来自实时任务，也可来自历史查看）
  const [viewResult, setViewResult] = useState<BacktestTask | null>(null)
  const [viewArtifacts, setViewArtifacts] = useState<ModelArtifacts | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // 因子库目录与特征勾选
  const [factorCatalog, setFactorCatalog] = useState<FactorCatalog | null>(null)
  const [customFeatures, setCustomFeatures] = useState<string[]>([]) // 已勾选的特征名
  const [showFeaturePanel, setShowFeaturePanel] = useState(false) // 是否展开特征选择面板

  // 加载数据源能力信息
  useEffect(() => {
    listDataSources().then(setDataSources).catch(() => {})
  }, [])

  // 加载因子目录（Alpha158 默认）
  useEffect(() => {
    getFactorCatalog('Alpha158')
      .then((c) => {
        setFactorCatalog(c)
        // 默认勾选全部特征（未选择自定义 → 用全量）
        setCustomFeatures(c.flat.map((f) => f.name))
      })
      .catch(() => {})
  }, [])

  // 清理轮询
  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [])

  const update = (k: keyof BacktestRequest, v: string | number | null) =>
    setForm((f) => ({ ...f, [k]: v as never }))

  // 更新单个模型超参（空字符串 → 移除该键，表示用默认值）
  const updateModelParam = (k: string, v: string) => {
    setForm((f) => {
      const cur = { ...(f.model_params || {}) }
      if (v === '') {
        delete cur[k]
      } else {
        cur[k] = Number(v)
      }
      return { ...f, model_params: cur }
    })
  }

  // 切换特征集（Alpha158/Alpha360/其他），并重新拉取目录
  const handleFeatureDatasetChange = async (v: string) => {
    update('feature', v)
    setShowFeaturePanel(false)
    try {
      const c = await getFactorCatalog(v)
      setFactorCatalog(c)
      setCustomFeatures(c.flat.map((f) => f.name))
      // 切换特征集时清空自定义选择 → 使用该特征集全量
      setForm((f) => ({ ...f, feature: v, selected_features: null }))
    } catch {
      setForm((f) => ({ ...f, feature: v }))
    }
  }

  // 勾选/取消单个特征
  const toggleFeature = (name: string) => {
    setCustomFeatures((prev) => {
      const next = prev.includes(name) ? prev.filter((x) => x !== name) : [...prev, name]
      // 同步到 form.selected_features：为空则置 null（使用全量）
      setForm((f) => ({ ...f, selected_features: next.length > 0 ? next : null }))
      return next
    })
  }

  // 全选 / 全不选
  const toggleAllFeatures = (select: boolean) => {
    if (!factorCatalog) return
    const next = select ? factorCatalog.flat.map((f) => f.name) : []
    setCustomFeatures(next)
    setForm((f) => ({ ...f, selected_features: next.length > 0 ? next : null }))
  }

  // 复现模式：用历史回测的参数填充表单（万元资金换算），定位到开始回测按钮
  const handleUseParams = (params: BacktestRequest, taskId: string) => {
    // 合并历史参数到当前表单：只覆盖"有定义"的字段，缺失字段保留表单值，避免 controlled→uncontrolled
    const merged: BacktestRequest = { ...form }
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null) (merged as unknown as Record<string, unknown>)[k] = v
    }
    merged.load_model_task_id = taskId // 复用权重
    setForm(merged)
    setCapitalWan((params.initial_capital || 0) / 10000)
    setError('')
    // 同步特征勾选状态：历史选中了特征则带出，否则全量
    if (params.selected_features?.length) {
      setCustomFeatures(params.selected_features)
      setShowFeaturePanel(true)
    } else if (factorCatalog) {
      setCustomFeatures(factorCatalog.flat.map((f) => f.name))
    }
    // 定位到"开始回测"按钮位置
    try {
      const el = document.getElementById('start-backtest-btn')
      if (el && typeof el.scrollIntoView === 'function') {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' })
      }
    } catch {
      // 忽略滚动异常
    }
  }

  // 直接复用历史回测参数 + 模型权重开始回测（不训练）
  const handleReuseBacktest = async (params: BacktestRequest, taskId: string) => {
    setError('')
    // 合并历史参数到当前表单：只覆盖"有定义"的字段
    const merged: BacktestRequest = { ...form }
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null) (merged as unknown as Record<string, unknown>)[k] = v
    }
    merged.load_model_task_id = taskId // 复用权重
    setForm(merged)
    setCapitalWan((params.initial_capital || 0) / 10000)
    setTask(null)
    setArtifacts(null)
    setViewResult(null)
    setViewArtifacts(null)
    await submitAndRun(merged, (params.initial_capital || 0))
    // 定位到结果/按钮位置
    try {
      const el = document.getElementById('nav-chart') || document.getElementById('start-backtest-btn')
      if (el && typeof el.scrollIntoView === 'function') {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' })
      }
    } catch {
      // 忽略滚动异常
    }
  }

  // 打开某个历史回测的结果（调仓记录/曲线/训练产物）
  const handleViewResult = async (taskId: string) => {
    setError('')
    // 切换查看历史回测：清空实时任务与旧结果，避免残留上一次查看的曲线
    setTask(null)
    setViewResult(null)
    setViewArtifacts(null)

    // 先尝试读回测结果（result.json / 内存任务）
    let loadedResult = false
    try {
      const t = await getBacktestTask(taskId)
      if (t?.result) {
        setViewResult(t)
        loadedResult = true
      }
    } catch {
      // 内存任务丢失，忽略，下面尝试读持久化 result.json
    }
    if (!loadedResult) {
      try {
        const data = await getBacktestResult(taskId)
        if (data) {
          const t = { task_id: taskId, status: 'success', progress: 100, result: data } as BacktestTask
          setViewResult(t)
          loadedResult = true
        }
      } catch {
        // result.json 不存在：可能是只有模型产物的目录，保留现有/清空结果
      }
    }

    // 加载该回测的参数并填充表单（同步分层/IC等说明文案，不提交回测）。
    // 只覆盖历史参数中"有定义"的字段，缺失字段保留表单现有值，避免 controlled→uncontrolled 告警。
    try {
      const snap = await getBacktestSnapshot(taskId)
      if (snap?.params) {
        const merged: BacktestRequest = { ...form }
        for (const [k, v] of Object.entries(snap.params)) {
          if (v !== undefined && v !== null) (merged as unknown as Record<string, unknown>)[k] = v
        }
        setForm(merged)
        setCapitalWan((snap.params.initial_capital || 0) / 10000)
      }
    } catch {
      // 无 params.json（旧任务），忽略，保持当前表单
    }

    // 单独尝试读模型产物（权重/特征等），不依赖 result.json
    try {
      const a = await getBacktestArtifacts(taskId)
      setViewArtifacts(a)
    } catch {
      setViewArtifacts(null)
    }

    // 两者都没有才提示
    if (!loadedResult && !viewArtifacts) {
      setError('该回测既无结果也无模型产物，无法查看')
    }
    // 滚动到收益曲线区块（而非页面底部）
    try {
      const el = document.getElementById('nav-chart')
      if (el && typeof el.scrollIntoView === 'function') {
        el.scrollIntoView({ behavior: 'smooth', block: 'start' })
      }
    } catch {
      // 忽略滚动异常
    }
  }

  // 停止回测
  const stopBacktest = async () => {
    if (!task) return
    try {
      await cancelBacktest(task.task_id)
      setError('正在停止回测...')
    } catch {
      setError('停止回测失败')
    }
  }

  // 提交回测并轮询进度（可复用于"开始回测"和"复用回测"）
  const submitAndRun = async (payload: BacktestRequest, capitalYuan: number) => {
    setLoading(true)
    try {
      const body: BacktestRequest = {
        ...payload,
        initial_capital: capitalYuan,
      }
      const { task_id } = await submitBacktest(body)
      pollRef.current && clearInterval(pollRef.current)
      pollRef.current = setInterval(async () => {
        try {
          const t = await getBacktestTask(task_id)
          setTask(t)
          if (t.status === 'success' || t.status === 'failed' || t.status === 'cancelled') {
            if (pollRef.current) {
              clearInterval(pollRef.current)
              pollRef.current = null
            }
            if (t.status === 'failed') setError(t.message)
            if (t.status === 'cancelled') setError('回测已停止')
            if (t.status === 'success') {
              // 加载模型交付物（公式/权重/超参数/模型文件）
              try {
                const a = await getBacktestArtifacts(task_id)
                setArtifacts(a)
              } catch {
                setArtifacts(null)
              }
            }
          }
        } catch (e) {
          if (pollRef.current) {
            clearInterval(pollRef.current)
            pollRef.current = null
          }
          setError('查询任务状态失败')
        }
      }, 1500)
    } catch (e) {
      setError('提交回测失败，请确认后端服务已启动')
    } finally {
      setLoading(false)
    }
  }

  const startBacktest = async () => {
    setError('')
    // 校验日期格式：必须是完整的 YYYY-MM-DD（避免键盘输入不完整日期导致 bug）
    const dateRe = /^\d{4}-\d{2}-\d{2}$/
    if (!dateRe.test(form.start_date) || !dateRe.test(form.end_date)) {
      setError('请通过日历完整选择开始/结束日期（格式 YYYY-MM-DD），避免键盘输入不完整的日期')
      setLoading(false)
      return
    }
    if (form.end_date < form.start_date) {
      setError('结束日期不能早于开始日期')
      setLoading(false)
      return
    }
    setTask(null)
    setArtifacts(null)
    setViewResult(null)
    setViewArtifacts(null)
    await submitAndRun(form, (capitalWan || 0) * 10000)
  }

  const running = task?.status === 'running' || task?.status === 'pending' || task?.status === 'cancelling'

  return (
    <div className="min-h-screen">
      <header className="bg-slate-900 text-white py-4 px-6 shadow">
        <h1 className="text-xl font-bold">Qlib 量化回测平台</h1>
        <p className="text-sm text-slate-400">
          Alpha158 因子 + LightGBM 模型 + TopK 策略 | 多数据源支持
        </p>
      </header>

      <main className="max-w-6xl mx-auto p-6 space-y-6">
        {/* 参数表单 */}
        <section className="bg-white dark:bg-slate-800 rounded-xl shadow p-6">
          <h2 className="text-lg font-semibold mb-4">回测参数</h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <label className="block">
              <span className="text-sm text-slate-500">股票池</span>
              <select
                className="mt-1 w-full border rounded px-2 py-1"
                value={form.universe}
                onChange={(e) => update('universe', e.target.value)}
              >
                <option value="csi300">沪深300</option>
                <option value="csi500">中证500</option>
                <option value="csi800">中证800</option>
                <option value="csi1000">中证1000</option>
                <option value="all">全部A股</option>
              </select>
            </label>

            <label className="block">
              <span className="text-sm text-slate-500">模型</span>
              <select
                className="mt-1 w-full border rounded px-2 py-1"
                value={form.model}
                onChange={(e) => update('model', e.target.value)}
              >
                <option value="LightGBM">LightGBM</option>
                <option value="XGBoost">XGBoost</option>
                <option value="Linear">线性回归</option>
              </select>
            </label>

            <label className="block">
              <span className="text-sm text-slate-500">数据源</span>
              <select
                className="mt-1 w-full border rounded px-2 py-1"
                value="qlib"
                disabled
                title="当前仅支持 Qlib 数据源"
              >
                <option value="qlib">Qlib</option>
              </select>
            </label>

            <div className="block">
              <span className="text-sm text-slate-500">特征集</span>
              <select
                className="mt-1 w-full border rounded px-2 py-1"
                value={form.feature}
                onChange={(e) => handleFeatureDatasetChange(e.target.value)}
              >
                <option value="Alpha158">Alpha158</option>
                <option value="Alpha360">Alpha360</option>
              </select>
              {/* 自定义特征选择开关 */}
              <button
                type="button"
                onClick={() => setShowFeaturePanel(!showFeaturePanel)}
                className="mt-2 w-full text-xs border rounded px-2 py-1.5 text-blue-600 hover:bg-blue-50 dark:text-blue-400"
              >
                {showFeaturePanel ? '收起特征选择 ▲' : '自定义筛选特征 ▼'}
              </button>
              {/* 特征选择面板 */}
              {showFeaturePanel && factorCatalog && (
                <div className="mt-2 border rounded p-2 bg-slate-50 dark:bg-slate-900 text-xs">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-slate-500">
                      已选 {customFeatures.length} / {factorCatalog.total} 个特征
                      {form.selected_features ? '' : '（全量）'}
                    </span>
                    <span className="space-x-1">
                      <button type="button" onClick={() => toggleAllFeatures(true)} className="text-blue-600 hover:underline">
                        全选
                      </button>
                      <span className="text-slate-300">|</span>
                      <button type="button" onClick={() => toggleAllFeatures(false)} className="text-blue-600 hover:underline">
                        清空
                      </button>
                    </span>
                  </div>
                  <div className="max-h-64 overflow-y-auto space-y-2 pr-1">
                    {factorCatalog.groups.map((g) => (
                      <div key={g.group}>
                        <div className="font-semibold text-slate-600 dark:text-slate-300 mb-1">
                          {g.group}
                        </div>
                        <div className="flex flex-wrap gap-1.5">
                          {g.fields.map((f) => (
                            <label
                              key={f.name}
                              title={`${f.name}\n公式: ${f.expression}\n${f.description}`}
                              className={`px-2 py-1 rounded border cursor-pointer select-none ${
                                customFeatures.includes(f.name)
                                  ? 'bg-blue-600 text-white border-blue-600'
                                  : 'bg-white dark:bg-slate-700 border-slate-300 dark:border-slate-600 hover:border-blue-400'
                              }`}
                            >
                              <input
                                type="checkbox"
                                className="hidden"
                                checked={customFeatures.includes(f.name)}
                                onChange={() => toggleFeature(f.name)}
                              />
                              {f.name}
                            </label>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                  <p className="mt-2 text-slate-400">
                    鼠标悬停在特征名上可查看公式与说明；勾选后仅使用所选特征回测。
                  </p>
                </div>
              )}
            </div>

            <label className="block">
              <span className="text-sm text-slate-500">开始日期</span>
              <input
                type="date"
                className="mt-1 w-full border rounded px-2 py-1"
                value={form.start_date}
                onChange={(e) => update('start_date', e.target.value)}
              />
            </label>

            <label className="block">
              <span className="text-sm text-slate-500">结束日期</span>
              <input
                type="date"
                className="mt-1 w-full border rounded px-2 py-1"
                value={form.end_date}
                onChange={(e) => update('end_date', e.target.value)}
              />
            </label>

            <label className="block">
              <span className="text-sm text-slate-500">TopK 选股数</span>
              <input
                type="number"
                className="mt-1 w-full border rounded px-2 py-1"
                value={form.topk}
                onChange={(e) => update('topk', Number(e.target.value))}
              />
            </label>

            <label className="block">
              <span className="text-sm text-slate-500">持仓周期(天)</span>
              <input
                type="number"
                className="mt-1 w-full border rounded px-2 py-1"
                value={form.n_days_hold}
                onChange={(e) => update('n_days_hold', Number(e.target.value))}
              />
            </label>
            <label className="block">
              <span className="text-sm text-slate-500">
                预测周期(天)
                <span className="block text-[10px] text-slate-400">模型预测未来N日收益</span>
              </span>
              <input
                type="number"
                min={1}
                className="mt-1 w-full border rounded px-2 py-1"
                value={form.label_horizon}
                onChange={(e) => update('label_horizon', Number(e.target.value))}
              />
            </label>
            <label className="block">
              <span className="text-sm text-slate-500">
                分层持仓周期(天)
                <span className="block text-[10px] text-slate-400">{'1=每日重排；>1=调仓日分组持有（评估实盘）'}</span>
              </span>
              <input
                type="number"
                min={1}
                className="mt-1 w-full border rounded px-2 py-1"
                value={form.layer_rebalance}
                onChange={(e) => update('layer_rebalance', Number(e.target.value))}
              />
            </label>
          </div>

          {/* 模型超参数（LightGBM / XGBoost 可配置且字段不同；留空使用各自默认值；Linear 无树参数） */}
          {(() => {
            const modelKey = form.model.toLowerCase()
            const fields = MODEL_PARAM_FIELDS[modelKey]
            if (!fields) return null // Linear 等没有树参数
            return (
              <div className="mt-5 pt-4 border-t border-slate-200 dark:border-slate-700">
                <h3 className="text-sm font-semibold text-slate-600 dark:text-slate-300 mb-3">
                  {form.model} 模型参数（留空 = {form.model} 默认值）
                </h3>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                  {fields.map((f) => (
                    <label className="block" key={f.key}>
                      <span className="text-sm text-slate-500">{f.label}</span>
                      <input
                        type="number"
                        step={f.step}
                        className="mt-1 w-full border rounded px-2 py-1"
                        placeholder={f.placeholder}
                        value={form.model_params?.[f.key] ?? ''}
                        onChange={(e) => updateModelParam(f.key, e.target.value)}
                      />
                    </label>
                  ))}
                </div>
                <p className="mt-2 text-xs text-slate-400">
                  {modelKey === 'lightgbm'
                    ? '提示：深度过深 / 叶子数过多 / 学习率过高易过拟合；留空项自动使用 Qlib 默认值。'
                    : 'XGBoost 与 LightGBM 参数名不同（如 min_child_weight 而非 min_child_samples，无 num_leaves）；留空项使用 XGBoost 默认值。'}
                  复用历史回测参数时会自动带出这些设置。
                </p>
              </div>
            )
          })()}

          {/* 交易成本与成交设置 */}
          <div className="mt-5 pt-4 border-t border-slate-200 dark:border-slate-700">
            <h3 className="text-sm font-semibold text-slate-600 dark:text-slate-300 mb-3">
              交易成本与成交设置
            </h3>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <label className="block">
                <span className="text-sm text-slate-500">起始总资产(万元)</span>
                <input
                  type="number"
                  step="100"
                  className="mt-1 w-full border rounded px-2 py-1"
                  value={capitalWan}
                  onChange={(e) => setCapitalWan(Number(e.target.value))}
                />
              </label>

              <label className="block">
                <span className="text-sm text-slate-500">成交价基准</span>
                <select
                  className="mt-1 w-full border rounded px-2 py-1"
                  value={form.deal_price}
                  onChange={(e) => update('deal_price', e.target.value)}
                >
                  <option value="close">收盘价</option>
                  <option value="open">开盘价</option>
                  <option value="vwap">均价</option>
                </select>
              </label>

              <label className="block">
                <span className="text-sm text-slate-500">买入手续费(%)</span>
                <input
                  type="number"
                  step="0.0001"
                  className="mt-1 w-full border rounded px-2 py-1"
                  value={form.open_cost}
                  onChange={(e) => update('open_cost', Number(e.target.value))}
                />
              </label>

              <label className="block">
                <span className="text-sm text-slate-500">卖出手续费(%)</span>
                <input
                  type="number"
                  step="0.0001"
                  className="mt-1 w-full border rounded px-2 py-1"
                  value={form.close_cost}
                  onChange={(e) => update('close_cost', Number(e.target.value))}
                />
              </label>

              <label className="block">
                <span className="text-sm text-slate-500">滑点/冲击成本(%)</span>
                <input
                  type="number"
                  step="0.0001"
                  className="mt-1 w-full border rounded px-2 py-1"
                  value={form.impact_cost}
                  onChange={(e) => update('impact_cost', Number(e.target.value))}
                />
              </label>

              <label className="block">
                <span className="text-sm text-slate-500">最低手续费(元)</span>
                <input
                  type="number"
                  className="mt-1 w-full border rounded px-2 py-1"
                  value={form.min_cost}
                  onChange={(e) => update('min_cost', Number(e.target.value))}
                />
              </label>

              <label className="block">
                <span className="text-sm text-slate-500">成交量限制(比例)</span>
                <input
                  type="number"
                  step="0.05"
                  className="mt-1 w-full border rounded px-2 py-1"
                  value={form.volume_threshold ?? ''}
                  onChange={(e) =>
                    update(
                      'volume_threshold',
                      e.target.value === '' ? null : Number(e.target.value),
                    )
                  }
                />
              </label>

              <label className="block">
                <span className="text-sm text-slate-500">涨跌停限制(比例)</span>
                <input
                  type="number"
                  step="0.005"
                  className="mt-1 w-full border rounded px-2 py-1"
                  value={form.limit_threshold ?? ''}
                  onChange={(e) =>
                    update(
                      'limit_threshold',
                      e.target.value === '' ? null : Number(e.target.value),
                    )
                  }
                />
              </label>

              <label className="block">
                <span className="text-sm text-slate-500">每手股数</span>
                <input
                  type="number"
                  className="mt-1 w-full border rounded px-2 py-1"
                  value={form.trade_unit ?? ''}
                  onChange={(e) =>
                    update('trade_unit', e.target.value === '' ? null : Number(e.target.value))
                  }
                />
              </label>
            </div>
            <p className="mt-2 text-xs text-slate-400">
              提示：成交量限制填 0.25 表示单笔成交不超过当日成交量的 25%；留空表示不限量（理想成交）。
              涨跌停限制填 0.095 表示涨/跌停无法交易；留空表示不设涨跌停。
            </p>
          </div>

          {/* 训练/测试划分（滚动训练） */}
          <div className="mt-5 pt-4 border-t border-slate-200 dark:border-slate-700">
            <h3 className="text-sm font-semibold text-slate-600 dark:text-slate-300 mb-3">
              训练/测试划分
            </h3>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <label className="block">
                <span className="text-sm text-slate-500">划分方式</span>
                <select
                  className="mt-1 w-full border rounded px-2 py-1"
                  value={form.split_mode}
                  onChange={(e) => update('split_mode', e.target.value)}
                >
                  <option value="single">一次性训练（回测前窗口训练）</option>
                  <option value="custom">自定义滚动训练（每周期重训）</option>
                </select>
              </label>

              {form.split_mode === 'custom' && (
                <>
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <span className="text-sm text-slate-500">训练窗口</span>
                      <input
                        type="number"
                        min={1}
                        className="mt-1 w-full border rounded px-2 py-1"
                        value={form.train_win}
                        onChange={(e) => update('train_win', Number(e.target.value))}
                      />
                    </div>
                    <div>
                      <span className="text-sm text-slate-500">训练单位</span>
                      <select
                        className="mt-1 w-full border rounded px-2 py-1"
                        value={form.train_unit}
                        onChange={(e) => update('train_unit', e.target.value)}
                      >
                        <option value="day">天</option>
                        <option value="week">周</option>
                        <option value="month">月</option>
                      </select>
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <span className="text-sm text-slate-500">测试窗口</span>
                      <input
                        type="number"
                        min={1}
                        className="mt-1 w-full border rounded px-2 py-1"
                        value={form.test_win}
                        onChange={(e) => update('test_win', Number(e.target.value))}
                      />
                    </div>
                    <div>
                      <span className="text-sm text-slate-500">测试单位</span>
                      <select
                        className="mt-1 w-full border rounded px-2 py-1"
                        value={form.test_unit}
                        onChange={(e) => update('test_unit', e.target.value)}
                      >
                        <option value="day">天</option>
                        <option value="week">周</option>
                        <option value="month">月</option>
                      </select>
                    </div>
                  </div>
                </>
              )}
            </div>
            {form.split_mode === 'custom' && (
              <p className="mt-2 text-xs text-slate-400">
                滚动训练：每个测试窗口开始时，用「测试窗口起点往前 N 单位」的最新数据重新训练模型，
                再预测并回测该测试窗口。各段账户资金连续，净值曲线无缝衔接。训练集只使用当时已发生的数据，
                避免未来数据泄漏。
              </p>
            )}
          </div>

          <div className="mt-4 flex items-center gap-4">
            <button
              id="start-backtest-btn"
              onClick={startBacktest}
              disabled={loading || running}
              className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white font-semibold px-6 py-2 rounded-lg"
            >
              {loading || running ? '回测进行中...' : '开始回测'}
            </button>
            <span className="text-xs text-slate-400">
              已启用数据源:{' '}
              {Object.entries(dataSources)
                .filter(([, cap]) => cap.daily)
                .map(([name]) => name)
                .join(', ') || 'qlib'}
            </span>
          </div>

          {error && <p className="mt-3 text-red-500 text-sm whitespace-pre-wrap">{error}</p>}
        </section>

        {/* 进度 */}
        {task && (
          <section className="bg-white dark:bg-slate-800 rounded-xl shadow p-6">
            <div className="flex items-center justify-between mb-2">
              <h2 className="text-lg font-semibold">
                任务状态{' '}
                <span className="text-sm text-slate-500">{task.task_id}</span>
              </h2>
              <div className="flex items-center gap-2">
                <span
                  className={`px-3 py-1 rounded text-sm ${
                    task.status === 'success'
                      ? 'bg-green-100 text-green-700'
                      : task.status === 'failed'
                        ? 'bg-red-100 text-red-700'
                        : task.status === 'cancelled'
                          ? 'bg-gray-100 text-gray-600'
                          : 'bg-blue-100 text-blue-700'
                  }`}
                >
                  {task.status}
                </span>
                {running && (
                  <button
                    onClick={stopBacktest}
                    className="px-3 py-1 rounded text-sm bg-red-600 text-white hover:bg-red-700"
                  >
                    停止回测
                  </button>
                )}
              </div>
            </div>
            <div className="w-full bg-slate-200 rounded-full h-4">
              <div
                className="bg-blue-600 h-4 rounded-full transition-all"
                style={{ width: `${task.progress}%` }}
              />
            </div>
            <p className="mt-2 text-sm text-slate-500">
              {task.progress.toFixed(1)}% - {task.message}
            </p>
          </section>
        )}

        {/* 结果：实时任务成功 或 历史查看（有 result 或仅有模型产物都渲染） */}
        {(((task?.result && task.status === 'success') || viewResult?.result) ||
          viewArtifacts) && (() => {
          const r = (task?.status === 'success' ? task.result : viewResult?.result) || null
          const a = (task?.status === 'success' ? artifacts : viewArtifacts) || null
          return (
            <>
              {r ? (
                <>
                  <MetricCards result={r} />
                  <NavChart nav={r.nav} />
                  <LayerChart data={r.layer_returns} rebalance={form.layer_rebalance} />
                  <ICChart data={r.ic_analysis} />
                </>
              ) : (
                <div className="bg-white dark:bg-slate-800 rounded-xl shadow p-6 text-sm text-slate-400">
                  无回测记录
                </div>
              )}
              {a && <ModelArtifactsPanel artifacts={a} />}
              {/* 历史回测（复现模式）放在训练产物与调仓记录之间 */}
              <HistoryPanel
                onUseParams={handleUseParams}
                onReuseBacktest={handleReuseBacktest}
                onViewResult={handleViewResult}
              />
              {r?.trades && r.trades.length > 0 && <TradeLog trades={r.trades} />}
            </>
          )
        })()}

        {/* 既无结果也无模型产物时，历史回测仍显示（用于直接复用参数） */}
        {(!((task?.result && task.status === 'success') || viewResult?.result) && !viewArtifacts) && (
          <HistoryPanel
            onUseParams={handleUseParams}
            onReuseBacktest={handleReuseBacktest}
            onViewResult={handleViewResult}
          />
        )}
      </main>
    </div>
  )
}
