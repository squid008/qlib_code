import { useState, useEffect, useRef, useCallback } from 'react'
import {
  submitBacktest,
  getBacktestTask,
  listDataSources,
  getBacktestArtifacts,
  getBacktestResult,
  getBacktestPartial,
  cancelBacktest,
  resumeBacktest,
  getFactorCatalog,
  getBacktestSnapshot,
  getBacktestCapacity,
  listBacktests,
  getAppVersion,
  listCustomFormulas,
  createCustomFormula,
  updateCustomFormula,
  deleteCustomFormula,
  getSingleFactorTestTasks,
} from './api'
import type { BacktestCapacity, CustomFormula } from './api'
import type { BacktestRequest, BacktestTask, DataSourceInfo, ModelArtifacts, FactorCatalog } from './types'
import MetricCards from './components/MetricCards'
import NavChart from './components/NavChart'
import LayerChart from './components/LayerChart'
import ICChart from './components/ICChart'
import TradeLog from './components/TradeLog'
import ModelArtifactsPanel from './components/ModelArtifacts'
import HistoryPanel from './components/HistoryPanel'
import FormulaPanel from './components/FormulaPanel'
import FeatureSelectPanel from './components/FeatureSelectPanel'
import SingleFactorTestPanel from './components/SingleFactorTestPanel'
import DateInput, { type DateInputHandle } from './components/DateInput'
import ModelParamsForm from './components/ModelParamsForm'
import TaskStatusPanel from './components/TaskStatusPanel'

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
    custom_formulas: null,
    bins: 5,
    deal_price: 'close',
    price_adjust: 'forward',
    open_cost: 0.0005,
    close_cost: 0.0015,
    min_cost: 5,
    impact_cost: 0.002,
    volume_threshold: null,
    limit_threshold: 0.095,
    trade_unit: 100,
    exclude_st: false,
    exclude_stock_gem: false,
    exclude_stock_kcb: false,
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
  const [capacity, setCapacity] = useState<BacktestCapacity | null>(null)
  const [version, setVersion] = useState('')
  // 触发历史回测面板刷新：递增该 key 即可让 HistoryPanel 自动 load（用于"任务取消/完成后自动更新"）
  const [historyRefreshKey, setHistoryRefreshKey] = useState(0)
  const [tasks, setTasks] = useState<BacktestTask[]>([])  // 所有活跃任务（支持多任务并行显示与取消）
  const tasksRef = useRef<BacktestTask[]>([])  // 用于轮询闭包读取最新 tasks（避免闭包陷阱）
  tasksRef.current = tasks
  // 用户主动"刷新"清除的已完成/失败/已停止任务 ID 集合：轮询拉取后端任务时过滤掉，避免再次出现
  // 持久化到 localStorage，刷新页面后仍生效（避免"刷新页面+复用回测"又把已清除的任务拉回来）
  const LOCAL_STORAGE_KEY = 'cleared_backtest_task_ids'
  const [clearedTaskIds, setClearedTaskIds] = useState<Set<string>>(() => {
    try {
      const raw = localStorage.getItem(LOCAL_STORAGE_KEY)
      if (!raw) return new Set()
      const arr = JSON.parse(raw) as string[]
      return new Set(Array.isArray(arr) ? arr : [])
    } catch {
      return new Set()
    }
  })
  const clearedTaskIdsRef = useRef<Set<string>>(clearedTaskIds)
  clearedTaskIdsRef.current = clearedTaskIds
  // 每次变更时同步写入 localStorage
  const persistCleared = (next: Set<string>) => {
    try {
      localStorage.setItem(LOCAL_STORAGE_KEY, JSON.stringify(Array.from(next)))
    } catch {
      // 忽略存储异常（隐私模式/配额满等）
    }
  }
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // 因子库目录与特征勾选
  const [factorCatalog, setFactorCatalog] = useState<FactorCatalog | null>(null)
  const [customFeatures, setCustomFeatures] = useState<string[]>([]) // 已勾选的特征名
  const [showFeaturePanel, setShowFeaturePanel] = useState(false) // 是否展开特征选择面板

  // 自定义公式因子（M2）：益盟/通达信公式 → 特征（后端持久化，刷新不丢失）
  const [customFormulas, setCustomFormulas] = useState<CustomFormula[]>([])
  const [selectedFormulaIds, setSelectedFormulaIds] = useState<Set<string>>(new Set()) // 勾选进入回测的公式 id
  const [formulaInput, setFormulaInput] = useState('') // 公式输入框
  const [showFormulaPanel, setShowFormulaPanel] = useState(false) // 是否展开公式编辑面板
  const [formulaError, setFormulaError] = useState('') // 公式编译错误提示
  const [formulaTranslating, setFormulaTranslating] = useState(false)
  // 编辑状态：editingId 非空时对应公式进入编辑模式
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editingText, setEditingText] = useState('')
  // 单因子测试面板（不训练模型，勾选因子后快速诊断）
  const [showSingleTestPanel, setShowSingleTestPanel] = useState(false)
  // 刷新后若后台仍有运行中的单因子测试任务：自动展开面板并滚动定位（仅"恢复"场景触发一次）
  const panelScrollRef = useRef(false)
  // 日期三段输入：开始日期"填到完整"后自动跳到结束日期的年份框
  const startDateRef = useRef<DateInputHandle>(null)
  const endDateRef = useRef<DateInputHandle>(null)

  // 加载数据源能力信息
  useEffect(() => {
    listDataSources().then(setDataSources).catch(() => {})
  }, [])

  // 刷新后恢复：检测后台是否有 running 的单因子测试任务，有则自动展开单因子面板
  useEffect(() => {
    let disposed = false
    ;(async () => {
      try {
        const res = await getSingleFactorTestTasks()
        if (!disposed && res.tasks?.some((t) => t.status === 'running')) {
          panelScrollRef.current = true
          setShowSingleTestPanel(true)
        }
      } catch {
        // 后端不支持该接口时静默
      }
    })()
    return () => {
      disposed = true
    }
  }, [])

  // 面板展开后滚动定位（仅刷新恢复场景触发，手动展开不滚动）
  useEffect(() => {
    if (panelScrollRef.current) {
      panelScrollRef.current = false
      const t = window.setTimeout(() => {
        document.getElementById('single-factor-test-panel')?.scrollIntoView({
          behavior: 'smooth',
          block: 'start',
        })
      }, 150)
      return () => window.clearTimeout(t)
    }
  }, [showSingleTestPanel])

  // 加载版本号
  useEffect(() => {
    getAppVersion().then((v) => setVersion(v.version)).catch(() => {})
  }, [])

  // 加载已保存的自定义公式（后端持久化），默认全选
  useEffect(() => {
    listCustomFormulas()
      .then(({ items }) => {
        setCustomFormulas(items)
        if (items.length > 0) {
          setSelectedFormulaIds(new Set(items.map((x) => x.id)))
          setForm((f) => ({ ...f, custom_formulas: items.map((x) => x.text) }))
        }
      })
      .catch(() => {})
  }, [])

  // 加载并发回测能力信息（并发上限 / 运行数 / 硬件资源）
  useEffect(() => {
    getBacktestCapacity().then(async (cap) => {
      setCapacity(cap)
      // 如果 capacity 显示有运行中的任务，但本地 tasks 为空（页面刷新后），）
      // 从后端拉取所有任务，把正在运行/排队/取消中的任务加到本地 tasks，
      // 这样用户能看到"那些被隐藏的、还在跑的任务"（尤其是刷新页面后）
      if (cap && cap.running > 0) {
        try {
          const all = await listBacktests()
          const active = Object.values(all).filter(
            (t) =>
              t.status === 'running' ||
              t.status === 'pending' ||
              t.status === 'cancelling',
          )
          if (active.length > 0) {
            setTasks((prev) => {
              // 合并：保留本会话已有的，加上后端拉到的活跃任务（去重）
              const ids = new Set(prev.map((t) => t.task_id))
              const merged = [...prev, ...active.filter((t) => !ids.has(t.task_id))]
              tasksRef.current = merged
              return merged
            })
            // 有活跃任务时启动轮询，持续刷新进度 / 状态（含 cancelling），否则刷新页面后看不到更新
            startPolling()
          }
        } catch {}
      }
    }).catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 定时刷新并发能力：单因子测试/回测/排队等任何占用配额的任务，
  // 都会在几秒内反映到"并发: x/y"显示，无需手动刷新页面。
  // 仅在 running/queued/max_concurrent 实际变化时才 setState，避免无谓的整树重渲染。
  useEffect(() => {
    const t = window.setInterval(async () => {
      try {
        const cap = await getBacktestCapacity()
        setCapacity((prev) => {
          if (
            prev &&
            prev.running === cap.running &&
            prev.queued === cap.queued &&
            prev.max_concurrent === cap.max_concurrent
          ) {
            return prev // 数值没变，复用旧引用，不触发重渲染
          }
          return cap
        })
      } catch {
        // 网络抖动忽略，下一轮再试
      }
    }, 3000)
    return () => window.clearInterval(t)
  }, [])

  // 立即刷新并发能力显示（供单因子面板在任务提交/结束/取消时调用，即时反馈 +1 / -1）
  const refreshCapacity = useCallback(() => {
    getBacktestCapacity().then(setCapacity).catch(() => {})
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

  const update = (k: keyof BacktestRequest, v: string | number | boolean | null) =>
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

  // 把"勾选中的公式原文"同步到 form.custom_formulas（回测时后端按此编译特征）
  const syncFormulasToForm = (items: CustomFormula[], ids: Set<string>) => {
    const texts = items.filter((x) => ids.has(x.id)).map((x) => x.text)
    setForm((f) => ({ ...f, custom_formulas: texts.length > 0 ? texts : null }))
  }

  // 同步历史参数的自定义特征/公式勾选状态（"查看"与"复用参数"共用）：
  // 历史任务用了什么就勾什么；历史没用的（null/空）一律清空勾选，避免把本地自定义误勾上。
  const syncCustomSelections = async (params: BacktestRequest) => {
    // 若当前已加载的目录与历史 feature 不一致（如历史是 mixed / Alpha360），先重拉对应目录，
    // 保证勾选面板的目录与 feature 匹配（Alpha158/Alpha360/mixed）。
    let catalog = factorCatalog
    const want = String(params.feature || 'Alpha158').toLowerCase()
    const cur = (factorCatalog?.dataset || '').toLowerCase()
    if (cur !== want && (want === 'alpha158' || want === 'alpha360' || want === 'mixed')) {
      try {
        catalog = await getFactorCatalog(String(params.feature || 'Alpha158'))
        setFactorCatalog(catalog)
      } catch {
        catalog = factorCatalog
      }
    }
    // 特征勾选
    if (params.selected_features?.length) {
      setCustomFeatures(params.selected_features)
      setShowFeaturePanel(true)
    } else if (catalog) {
      setCustomFeatures(catalog.flat.map((f) => f.name))
    }
    // 公式勾选
    if (params.custom_formulas?.length) {
      const savedTexts = new Set(customFormulas.map((x) => x.text))
      const missing = params.custom_formulas.filter((t) => !savedTexts.has(t))
      const tempItems: CustomFormula[] = missing.map((t) => ({
        id: t, // 临时 id 用原文，仅本次会话存在，不落盘
        name: t.split(';')[0] || '公式',
        text: t,
        expression: '',
        created_at: '',
        updated_at: '',
      }))
      const all = [...customFormulas, ...tempItems]
      setCustomFormulas(all)
      // 只勾选历史任务实际用到的公式（按文本匹配），避免把本地其他公式也一并全选
      const usedIds = new Set<string>()
      for (const t of params.custom_formulas) {
        const hit = all.find((x) => x.text === t)
        if (hit) usedIds.add(hit.id)
      }
      setSelectedFormulaIds(usedIds)
      syncFormulasToForm(all, usedIds)
      setShowFormulaPanel(true)
    } else {
      // 历史任务未用公式：清空勾选，保持面板状态与提交内容一致
      setSelectedFormulaIds(new Set())
      syncFormulasToForm(customFormulas, new Set())
    }
  }

  // 添加自定义公式：编译并保存到后端，默认勾选
  const addCustomFormula = async () => {
    const text = formulaInput.trim()
    if (!text) {
      setFormulaError('请先输入公式')
      return
    }
    if (customFormulas.some((f) => f.text.trim() === text)) {
      setFormulaError('该公式已存在，可直接勾选使用或点编辑修改')
      return
    }
    setFormulaTranslating(true)
    setFormulaError('')
    try {
      const item = await createCustomFormula(text)
      const next = [...customFormulas, item]
      setCustomFormulas(next)
      const ids = new Set(selectedFormulaIds).add(item.id)
      setSelectedFormulaIds(ids)
      syncFormulasToForm(next, ids)
      setFormulaInput('')
    } catch (e: unknown) {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const detail = (e as any)?.response?.data?.detail
      setFormulaError(detail ? String(detail) : '公式编译失败，请检查语法')
    } finally {
      setFormulaTranslating(false)
    }
  }

  // 删除自定义公式（同时从后端删除）
  const removeCustomFormula = async (id: string) => {
    try {
      await deleteCustomFormula(id)
    } catch {
      // 删除失败不阻塞本地移除（下次刷新会自动纠正）
    }
    const next = customFormulas.filter((x) => x.id !== id)
    setCustomFormulas(next)
    const ids = new Set(selectedFormulaIds)
    ids.delete(id)
    setSelectedFormulaIds(ids)
    syncFormulasToForm(next, ids)
    if (editingId === id) {
      setEditingId(null)
      setEditingText('')
    }
  }

  // 勾选 / 取消勾选单个自定义公式
  const toggleFormula = (id: string) => {
    const ids = new Set(selectedFormulaIds)
    if (ids.has(id)) ids.delete(id)
    else ids.add(id)
    setSelectedFormulaIds(ids)
    syncFormulasToForm(customFormulas, ids)
  }

  // 全选 / 全不选
  const toggleAllFormulas = (select: boolean) => {
    const ids = select ? new Set(customFormulas.map((x) => x.id)) : new Set<string>()
    setSelectedFormulaIds(ids)
    syncFormulasToForm(customFormulas, ids)
  }

  // 开始编辑某个公式（把原文放入编辑框，该项进入编辑模式）
  const startEditFormula = (f: CustomFormula) => {
    setEditingId(f.id)
    setEditingText(f.text)
    setFormulaError('')
  }

  // 保存编辑：重新编译并写回后端
  const saveEditFormula = async () => {
    if (!editingId) return
    const text = editingText.trim()
    if (!text) {
      setFormulaError('公式不能为空')
      return
    }
    setFormulaTranslating(true)
    setFormulaError('')
    try {
      const item = await updateCustomFormula(editingId, text)
      const next = customFormulas.map((x) => (x.id === editingId ? item : x))
      setCustomFormulas(next)
      syncFormulasToForm(next, selectedFormulaIds)
      setEditingId(null)
      setEditingText('')
    } catch (e: unknown) {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const detail = (e as any)?.response?.data?.detail
      setFormulaError(detail ? String(detail) : '公式编译失败，请检查语法')
    } finally {
      setFormulaTranslating(false)
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
    // 老任务（v1.5.0 之前）没有 price_adjust 字段，统一视为"不复权"；
    // 复用旧模型权重时复权方式必须与源一致，否则特征不匹配。
    merged.price_adjust = (params as BacktestRequest).price_adjust ?? 'none'
    // 复用参数 ≠ 续测：清掉可能残留的 resume_task_id（如之前"查看"过续测任务把它填进了表单），
    // 否则提交会被当成"续测源任务"，复用源目录导致秒完成 + 产物写进源目录污染它。
    merged.resume_task_id = null
    setForm(merged)
    setCapitalWan((params.initial_capital || 0) / 10000)
    setError('')
    // 同步历史参数的自定义特征/公式勾选状态（历史没用的全部清空，避免误勾本地自定义）
    syncCustomSelections(params)
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
    // 完全用历史任务的参数覆盖当前表单（用户在表单上改的参数全部丢弃），
    // 这样"复用回测"等同于"用历史参数去跑"，不会被表单残留改动影响。
    // 保留 load_model_task_id（复用模型权重，不重新训练）。
    // 只覆盖历史参数中"有定义"的字段：旧任务可能缺某些字段（如 label_horizon），
    // 直接 {...params} 会把表单字段覆盖成 undefined，导致输入框 value=undefined 失控不能改。
    const merged: BacktestRequest = { ...form }
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null) (merged as unknown as Record<string, unknown>)[k] = v
    }
    // 显式覆盖"允许为 null"的字段：复用回测必须完全用源任务配置。
    // 否则 form 残留（如页面加载时 useEffect 自动填充的本地自定义公式）会让
    // payload 与复用源不一致，触发 submitAndRun 的"自定义公式不同 → 自动改新训练"。
    merged.custom_formulas = params.custom_formulas ?? null
    merged.selected_features = params.selected_features ?? null
    // 老任务（v1.4.9 之前）没有 price_adjust 字段，统一视为"不复权"；
    // 复用旧模型权重时复权方式必须与源一致，否则特征不匹配。
    merged.price_adjust = (params as BacktestRequest).price_adjust ?? 'none'
    merged.load_model_task_id = taskId
    // 复用回测 ≠ 续测：不保留源任务的 resume_task_id（同上，避免误当续测）
    merged.resume_task_id = null
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

  // 断点续跑：从"未完成"的滚动回测继续（后端复用源 artifacts 目录，跳过已完成段）
  const handleResume = async (taskId: string) => {
    setError('')
    try {
      const { task_id } = await resumeBacktest(taskId)
      setTask(null)
      setArtifacts(null)
      setViewResult(null)
      setViewArtifacts(null)
      setTasks((prev) => {
        const newTask: BacktestTask = {
          task_id,
          status: 'pending',
          progress: 0,
          message: '已提交（续跑）',
          created_at: new Date().toISOString(),
        }
        const next = [...prev, newTask]
        tasksRef.current = next
        return next
      })
      startPolling()
      // 续测后立即刷新历史：源目录被续测任务占用（is_task_running=true），删除按钮禁用、未完成标签更新
      setHistoryRefreshKey((k) => k + 1)
    } catch (e) {
      setError('续跑提交失败，请确认后端服务已启动')
    }
  }

  // 点击任务状态卡片：选中并展示该任务的曲线/已跑段（类似历史"查看"），再点一次取消展示，点其他任务切换
  const handleSelectTask = async (taskId: string) => {
    setError('')
    // 已选中同一任务 → 取消展示
    if (task?.task_id === taskId) {
      setTask(null)
      setArtifacts(null)
      setViewResult(null)
      setViewArtifacts(null)
      return
    }
    try {
      const t = await getBacktestTask(taskId)
      setTask(t)
      setViewResult(null)
      setViewArtifacts(null)
      // 成功任务：加载训练产物；运行中任务由结果区自动展示已跑段 partial
      if (t.status === 'success') {
        try {
          const a = await getBacktestArtifacts(taskId)
          setArtifacts(a)
        } catch {
          setArtifacts(null)
        }
      } else {
        setArtifacts(null)
      }
    } catch {
      setError('任务状态获取失败')
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
      } else if (t?.status === 'running' || t?.status === 'cancelling') {
        // 运行中任务：用任务本身展示已跑段 partial（保留 running 状态，文案显示"滚动训练进行中"）
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
    // 任务未完成（无 result.json）但滚动训练已跑过若干段：读 partial_result.json 展示已跑段结果
    if (!loadedResult) {
      try {
        const partial = await getBacktestPartial(taskId)
        if (partial?.nav?.length) {
          // 判断该历史任务是否正被运行（直接运行中，或被某个续测任务占用源目录）：
          // 运行中 → 文案显示"滚动训练进行中"；已停止 → 显示"回测未完成（已停止/中断）"
          let runningNow = false
          try {
            const all = await listBacktests()
            for (const t of Object.values(all)) {
              if (t.status === 'running' || t.status === 'cancelling') {
                // 直接是它，或续测任务的 display_name 以该 task_id 结尾（续测复用源目录，任务名沿用源）
                if (t.task_id === taskId || (t.display_name && t.display_name.endsWith(taskId))) {
                  runningNow = true
                  break
                }
              }
            }
          } catch {}
          const t = {
            task_id: taskId,
            status: runningNow ? 'running' : 'cancelled',
            progress: 100,
            partial_result: partial,
          } as BacktestTask
          setViewResult(t)
          loadedResult = true
        }
      } catch {
        // 无 partial_result.json：忽略
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
        // 显式覆盖"允许为 null"的字段：查看历史回测必须完全用源任务配置，
        // 否则 form 残留（页面加载自动填充的本地公式）会让表单参数与面板勾选不一致
        merged.custom_formulas = snap.params.custom_formulas ?? null
        merged.selected_features = snap.params.selected_features ?? null
        setForm(merged)
        setCapitalWan((snap.params.initial_capital || 0) / 10000)
        // 同步自定义特征/公式勾选状态（历史没用的清空，避免把本地自定义误勾上）
        syncCustomSelections(snap.params)
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

  // 启动全局任务轮询（只启动一次）：逐个刷新 tasksRef 里的活跃任务进度 + 刷新 capacity。
  // 供提交任务、以及"页面刷新后恢复正在运行的任务"时调用。
  const startPolling = () => {
    if (pollRef.current) return
    pollRef.current = setInterval(async () => {
      const curr = tasksRef.current
      if (curr.length === 0) return
      try {
        const updated = await Promise.all(
          curr.map(async (t) => {
            if (t.status === 'success' || t.status === 'failed' || t.status === 'cancelled') {
              return t
            }
            try {
              return await getBacktestTask(t.task_id)
            } catch {
              return t
            }
          }),
        )
        setTasks(updated)
        try {
          const cap = await getBacktestCapacity()
          setCapacity(cap)
        } catch {}
        let anyDone = false
        for (let i = 0; i < updated.length; i++) {
          const was = curr[i]
          const now = updated[i]
          const wasDone = was && (was.status === 'success' || was.status === 'failed' || was.status === 'cancelled')
          const isDone = now.status === 'success' || now.status === 'failed' || now.status === 'cancelled'
          if (!wasDone && isDone) anyDone = true
          if (!wasDone && isDone && now.status === 'success') {
            try {
              const a = await getBacktestArtifacts(now.task_id)
              setArtifacts(a)
            } catch {
              setArtifacts(null)
            }
            setTask(now)
            break
          }
        }
        // 本轮有任务结束（success/failed/cancelled）就刷新历史面板。
        // 修复：原实现把刷新放在 success 分支的 break 之前，同一轮多个任务同时完成时，
        // 第一个 success 的 break 会让其余刚完成任务的刷新被跳过 → 历史列表漏显示。
        if (anyDone) {
          setHistoryRefreshKey((k) => k + 1)
        }
        tasksRef.current = updated
        const hasActive = updated.some(
          (t) => t.status === 'running' || t.status === 'pending' || t.status === 'cancelling',
        )
        if (!hasActive) {
          try {
            const cap = await getBacktestCapacity()
            setCapacity(cap)
          } catch {}
          if (pollRef.current) {
            clearInterval(pollRef.current)
            pollRef.current = null
          }
        }
      } catch {}
    }, 1500)
  }

  // 提交回测并轮询进度（可复用于"开始回测"和"复用回测"）
  const submitAndRun = async (payload: BacktestRequest, capitalYuan: number) => {
    // 若用户在使用复用权重（load_model_task_id 有值），但改了关键参数（股票池/特征集/特征选择），
    // 自动清掉 load_model_task_id 改为新训练，避免"复用权重时特征不匹配"的错误。
    // 复用源参数从后端 snapshot 拉取（不依赖前端 state，避免复用回测时 setState 未生效导致漏检）。
    let payloadAdj = payload
    // 提交表单 ≠ 续测：兜底清掉 resume_task_id（续测走独立 resume 接口，不经过这里）。
    // 防止"查看过续测任务"把 resume_task_id 残留进表单后被误当成续测提交，
    // 导致复用源目录秒完成 + 污染源任务产物。
    payloadAdj = { ...payload, resume_task_id: null }
    if (payloadAdj.load_model_task_id) {
      let src: BacktestRequest | null = null
      try {
        const snap = await getBacktestSnapshot(payloadAdj.load_model_task_id)
        src = snap.params || null
      } catch {
        src = null
      }
      if (src) {
        const universeChanged = (payload.universe || '') !== (src.universe || '')
        const featureChanged = (payload.feature || '') !== (src.feature || '')
        // selected_features 比较：顺序无关，只要内容集合不同就算改
        const a = (payload.selected_features || []).slice().sort().join(',')
        const b = (src.selected_features || []).slice().sort().join(',')
        const selectedChanged = a !== b
        // custom_formulas 比较：公式文本与顺序都敏感（顺序变化会导致特征顺序变化，不能复用权重）
        const formulasChanged =
          ((payload.custom_formulas || []) as string[]).join('\x01') !==
          ((src.custom_formulas || []) as string[]).join('\x01')
        // 训练/测试划分方式不同也不能复用：滚动任务的模型按段保存，single 模式加载不到；
        // 反之 single 的单一模型用于滚动段也无意义。统一改为新训练。
        const splitChanged = (payload.split_mode || 'single') !== (src.split_mode || 'single')
        // 复权方式不同也会改变特征分布（复权价 ≠ 原始价），旧模型权重不匹配 → 改新训练
        const adjustChanged = (payload.price_adjust || 'none') !== (src.price_adjust || 'none')
        if (universeChanged || featureChanged || selectedChanged || formulasChanged || splitChanged || adjustChanged) {
          const changed: string[] = []
          if (universeChanged) changed.push('股票池')
          if (featureChanged) changed.push('特征集')
          if (selectedChanged) changed.push('自定义特征')
          if (formulasChanged) changed.push('自定义公式')
          if (splitChanged) changed.push('训练划分方式')
          if (adjustChanged) changed.push('复权方式')
          payloadAdj = { ...payload, load_model_task_id: null }
          setError(
            `检测到 ${changed.join('、')} 与复用源不同，已自动改为【新训练】（不再复用 task ${payload.load_model_task_id} 的模型权重）。`,
          )
        }
      }
    }
    // 并发上限检查：达到上限则提示，不再提交
    try {
      const cap = await getBacktestCapacity()
      setCapacity(cap)
      if (cap.available <= 0) {
        setError(`已达并发回测上限（${cap.max_concurrent} 个），请等待现有任务完成`)
        return
      }
    } catch {
      // 查询并发能力失败不阻塞提交
    }
    setLoading(true)
    try {
      const body: BacktestRequest = {
        ...payloadAdj,
        initial_capital: capitalYuan,
      }
      const { task_id } = await submitBacktest(body)
      // 提交成功后：清掉"复用模型权重"标记，隐藏提示条。
      // 后续再改参数不再提示，直到用户再次从历史回测点"复用参数"才重新出现。
      setForm((f) => ({ ...f, load_model_task_id: null }))
      // 新任务加入任务列表（用于多任务并行显示 + 各自取消）
      setTasks((prev) => {
        const newTask: BacktestTask = {
          task_id,
          status: 'pending',
          progress: 0.0,
          message: '已提交',
          created_at: new Date().toISOString(),
        }
        const next: BacktestTask[] = [...prev, newTask]
        // 立即同步 tasksRef，避免轮询读取到旧快照而把新任务覆盖掉
        tasksRef.current = next
        return next
      })
      // 启动全局轮询（只启动一次，后续提交复用同一个轮询，刷新本会话提交的所有任务）
      startPolling()
      // 立即刷新并发显示（留 200ms 给后端登记任务占位/排队，反映 +1，与单因子测试一致）
      window.setTimeout(() => {
        getBacktestCapacity().then(setCapacity).catch(() => {})
      }, 200)
      // 提交后立即刷新历史列表（新任务尽早出现在历史面板，避免要手动点"刷新"）
      setHistoryRefreshKey((k) => k + 1)
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

  return (
    <div className="min-h-screen">
      <header className="bg-slate-900 text-white py-4 px-6 shadow">
        <div className="flex items-center justify-between">
          <h1 className="text-xl font-bold">Qlib 量化回测平台</h1>
          {version && (
            <span className="text-sm text-slate-400 font-mono">v{version}</span>
          )}
        </div>
        <p className="text-sm text-slate-400">
          Alpha158 因子 + LightGBM 模型 + TopK 策略 | 多数据源支持
        </p>
      </header>

      <main className="max-w-6xl mx-auto p-6 space-y-6">
        {/* 参数表单 */}
        <section className="bg-white dark:bg-slate-800 rounded-xl shadow p-6">
          <div className="flex items-center justify-between mb-4 gap-3 flex-wrap">
            <h2 className="text-lg font-semibold">回测参数</h2>
            {/* 复用模型权重的提示：放在标题旁边，一行写下，不挤压表单 */}
            {form.load_model_task_id && (
              <div className="rounded bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-700 px-3 py-1.5 text-xs text-amber-800 dark:text-amber-200 flex items-center gap-2">
                <span>⚠ 正在复用模型权重（task <span className="font-mono">{form.load_model_task_id}）</span></span>
                <span className="text-amber-600 dark:text-amber-300">· 修改股票池/特征后会自动改为新训练</span>
              </div>
            )}
          </div>

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
                <option value="mixed">混合(158+360+公式)</option>
              </select>
              {/* 特征选择面板（展开时显示在 Row 2 第 4 列 button 下方） */}
              {showFeaturePanel && factorCatalog && (
                <FeatureSelectPanel
                  factorCatalog={factorCatalog}
                  customFeatures={customFeatures}
                  selected={!!form.selected_features}
                  onToggle={toggleFeature}
                  onToggleAll={toggleAllFeatures}
                />
              )}
            </div>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mt-4">
            <label className="block">
              <span className="text-sm text-slate-500">开始日期</span>
              <DateInput
                ref={startDateRef}
                className="mt-1 w-full"
                value={form.start_date}
                onChange={(v) => update('start_date', v)}
                onComplete={() => endDateRef.current?.focusYear()}
              />
            </label>

            <label className="block">
              <span className="text-sm text-slate-500">结束日期</span>
              <DateInput ref={endDateRef} className="mt-1 w-full" value={form.end_date} onChange={(v) => update('end_date', v)} />
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

            {/* 第 4 列：3 个 button（row-span-2 跨两行，不撑高输入框行高） */}
            <div className="flex flex-col gap-2 md:row-span-2">
              <button
                type="button"
                onClick={() => setShowFeaturePanel(!showFeaturePanel)}
                className="w-full text-xs border rounded px-2 py-1.5 text-blue-600 hover:bg-blue-50 dark:text-blue-400"
              >
                {showFeaturePanel ? '收起特征选择 ▲' : '自定义筛选特征 ▼'}
              </button>
              <button
                type="button"
                onClick={() => setShowFormulaPanel(!showFormulaPanel)}
                className={`w-full text-xs border rounded px-2 py-1.5 ${
                  customFormulas.length > 0
                    ? 'bg-emerald-600 text-white border-emerald-600'
                    : 'text-emerald-600 hover:bg-emerald-50 dark:text-emerald-400'
                }`}
              >
                {showFormulaPanel
                  ? '收起自定义公式 ▲'
                  : `使用自定义公式因子 ▼${customFormulas.length > 0 ? `（${customFormulas.length}）` : ''}`}
              </button>
              <button
                type="button"
                onClick={() => setShowSingleTestPanel(!showSingleTestPanel)}
                className={`w-full text-xs border rounded px-2 py-1.5 ${
                  showSingleTestPanel
                    ? 'bg-violet-600 text-white border-violet-600'
                    : 'text-violet-600 hover:bg-violet-50 dark:text-violet-400'
                }`}
              >
                {showSingleTestPanel ? '收起单因子测试 ▲' : '单因子测试 ▼'}
              </button>
            </div>

            <label className="flex flex-col">
              <span className="text-sm text-slate-500">预测周期(天)</span>
              <input
                type="number"
                min={1}
                className="mt-1 w-full border rounded px-2 py-1"
                value={form.label_horizon}
                onChange={(e) => update('label_horizon', Number(e.target.value))}
              />
              <span className="text-[10px] text-slate-400 mt-1">模型预测未来N日收益</span>
            </label>

            <label className="flex flex-col">
              <span className="text-sm text-slate-500">分层持仓周期(天)</span>
              <input
                type="number"
                min={1}
                className="mt-1 w-full border rounded px-2 py-1"
                value={form.layer_rebalance}
                onChange={(e) => update('layer_rebalance', Number(e.target.value))}
              />
              <span className="text-[10px] text-slate-400 mt-1">1=每日重排，&gt;1=调仓日分组持有</span>
            </label>

            <label className="flex flex-col">
              <span className="text-sm text-slate-500">持仓周期(天)</span>
              <input
                type="number"
                className="mt-1 w-full border rounded px-2 py-1"
                value={form.n_days_hold}
                onChange={(e) => update('n_days_hold', Number(e.target.value))}
              />
            </label>
          </div>

          {/* 自定义公式编辑面板（独立整行，位于 Row3 之后、模型超参数之前） */}
          
{showFormulaPanel && (
            <FormulaPanel
              customFormulas={customFormulas}
              selectedFormulaIds={selectedFormulaIds}
              formulaInput={formulaInput}
              formulaError={formulaError}
              formulaTranslating={formulaTranslating}
              editingId={editingId}
              editingText={editingText}
              onInputChange={setFormulaInput}
              onEditingTextChange={setEditingText}
              onAdd={addCustomFormula}
              onToggle={toggleFormula}
              onToggleAll={toggleAllFormulas}
              onStartEdit={startEditFormula}
              onSaveEdit={saveEditFormula}
              onCancelEdit={() => {
                setEditingId(null)
                setEditingText('')
                setFormulaError('')
              }}
              onRemove={removeCustomFormula}
            />
          )}

          {/* 单因子测试面板（独立整行）：收起=CSS 隐藏而非卸载——结果/勾选/运行中任务都保留，
              再次展开时上次展示仍在；需要彻底清空时用面板内"清理结果"按钮 */}
          <div id="single-factor-test-panel" className={showSingleTestPanel ? '' : 'hidden'}>
            <SingleFactorTestPanel
              customFormulas={customFormulas}
              defaultUniverse={form.universe}
              defaultStartDate={form.start_date}
              defaultEndDate={form.end_date}
              defaultLabelHorizon={form.label_horizon}
              onCapacityChange={refreshCapacity}
            />
          </div>

          <ModelParamsForm
            model={form.model}
            modelParams={form.model_params}
            onUpdate={updateModelParam}
          />

          {/* 交易成本与成交设置 */}
          <div className="mt-5 pt-4 border-t border-slate-200 dark:border-slate-700">
            <h3 className="text-sm font-semibold text-slate-600 dark:text-slate-300 mb-3">
              交易设置
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
                  <option value="vwap">均价(成交量加权)</option>
                  <option value="avg_co">均价(开盘+收盘)/2</option>
                  <option value="avg_ohlc">均价(开收高低)/4</option>
                </select>
              </label>

              <label className="block" title="前复权与后复权在比率类因子/收益率上数学等价（仅价格绝对值不同）">
                <span className="text-sm text-slate-500">复权方式</span>
                <select
                  className="mt-1 w-full border rounded px-2 py-1"
                  value={form.price_adjust || 'none'}
                  onChange={(e) => update('price_adjust', e.target.value)}
                >
                  <option value="none">不复权</option>
                  <option value="forward">前复权</option>
                  <option value="backward">后复权</option>
                </select>
              </label>

              <label className="block">
                <span className="text-sm text-slate-500">买入手续费(%)</span>
                <input
                  type="number"
                  step="0.01"
                  className="mt-1 w-full border rounded px-2 py-1"
                  value={form.open_cost * 100}
                  onChange={(e) => update('open_cost', Number(e.target.value) / 100)}
                />
              </label>

              <label className="block">
                <span className="text-sm text-slate-500">卖出手续费(%)</span>
                <input
                  type="number"
                  step="0.01"
                  className="mt-1 w-full border rounded px-2 py-1"
                  value={form.close_cost * 100}
                  onChange={(e) => update('close_cost', Number(e.target.value) / 100)}
                />
              </label>

              <label className="block">
                <span className="text-sm text-slate-500">滑点/冲击成本(%)</span>
                <input
                  type="number"
                  step="0.01"
                  className="mt-1 w-full border rounded px-2 py-1"
                  value={form.impact_cost * 100}
                  onChange={(e) => update('impact_cost', Number(e.target.value) / 100)}
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

              <div className="block">
                <span className="text-sm text-slate-500">涨跌停限制</span>
                <label
                  className="mt-1 flex items-center gap-1.5 text-sm cursor-pointer border rounded px-2 h-[30px] bg-white dark:bg-slate-900"
                  title="自动识别：主板 10%、创业/科创 20%、北交 30%；数据有交易所涨跌停价标签时直接按标签（ST 5%/退市整理 10% 也自动正确）。取消=不设涨跌停（理想成交）"
                >
                  <input
                    type="checkbox"
                    checked={form.limit_threshold != null}
                    onChange={(e) => update('limit_threshold', e.target.checked ? 0.1 : null)}
                  />
                  <span>封板不可交易</span>
                </label>
              </div>

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

            {/* 日截面剔除（调仓当日状态判定，无未来函数；与单因子测试"剔除ST(T+1)/创/科"同口径，
                但回测以调仓当日为准） */}
            <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-2">
              <span className="text-sm text-slate-500">日截面剔除：</span>
              <label
                className="flex items-center gap-1.5 text-sm cursor-pointer"
                title="调仓当日处于 ST/*ST/退市整理的股票不买入（模型打分时直接剔除，已持有的也会随调仓卖出）"
              >
                <input type="checkbox" checked={!!form.exclude_st} onChange={(e) => update('exclude_st', e.target.checked)} />
                <span>剔除ST/退市</span>
              </label>
              <label
                className="flex items-center gap-1.5 text-sm cursor-pointer"
                title="剔除创业板（SZ30 号段，20% 涨跌幅），全程不参与选股"
              >
                <input type="checkbox" checked={!!form.exclude_stock_gem} onChange={(e) => update('exclude_stock_gem', e.target.checked)} />
                <span>剔除创业板</span>
              </label>
              <label
                className="flex items-center gap-1.5 text-sm cursor-pointer"
                title="剔除科创板（SH688，20% 涨跌幅），全程不参与选股"
              >
                <input type="checkbox" checked={!!form.exclude_stock_kcb} onChange={(e) => update('exclude_stock_kcb', e.target.checked)} />
                <span>剔除科创板</span>
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
                <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
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
              disabled={loading || (capacity ? capacity.available <= 0 : false)}
              className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white font-semibold px-6 py-2 rounded-lg"
            >
              {loading ? '提交中...' : capacity && capacity.available <= 0 ? '已达并发上限' : '开始回测'}
            </button>
            <span className="text-xs text-slate-400">
              已启用数据源:{' '}
              {Object.entries(dataSources)
                .filter(([, cap]) => cap.daily)
                .map(([name]) => name)
                .join(', ') || 'qlib'}
            </span>
            {capacity && (
              <span
                className={`text-xs ${capacity.available <= 0 ? 'text-red-500 font-semibold' : 'text-slate-400'}`}
              >
                并发: {capacity.running}/{capacity.max_concurrent}
                {capacity.queued > 0 && `（${capacity.queued} 排队）`}
                {capacity.available <= 0 && ' 已达上限'}
              </span>
            )}
          </div>

          {error && <p className="mt-3 text-red-500 text-sm whitespace-pre-wrap">{error}</p>}
        </section>

        {/* 进度：多任务并行显示（每个任务一张卡片，状态+进度条+单独取消） */}
                {tasks.length > 0 && (
          <TaskStatusPanel
            tasks={tasks}
            onRefresh={() => {
              const removed = tasks.filter(
                (t) =>
                  t.status === 'success' ||
                  t.status === 'failed' ||
                  t.status === 'cancelled',
              )
              const next = new Set(clearedTaskIdsRef.current)
              removed.forEach((t) => next.add(t.task_id))
              setClearedTaskIds(next)
              persistCleared(next)
              setTasks((prev) =>
                prev.filter(
                  (t) =>
                    t.status === 'running' ||
                    t.status === 'pending' ||
                    t.status === 'cancelling',
                ),
              )
            }}
            onCancelAll={async () => {
              const active = tasks.filter(
                (t) => t.status === 'running' || t.status === 'pending' || t.status === 'cancelling',
              )
              await Promise.all(
                active.map(async (t) => {
                  try {
                    await cancelBacktest(t.task_id)
                  } catch {
                    /* 单个失败不阻塞其他 */
                  }
                }),
              )
              // 乐观更新：立即把全部任务标记为 cancelling（后端已受理，不依赖轮询才显示）
              setTasks((prev) =>
                prev.map((t) =>
                  t.status === 'running' || t.status === 'pending'
                    ? { ...t, status: 'cancelling' as const, message: '正在取消...' }
                    : t,
                ),
              )
            }}
            onCancelOne={async (taskId) => {
              try {
                await cancelBacktest(taskId)
              } catch {
                /* 单个任务取消失败不阻塞其他 */
              }
              // 乐观更新：立即标记该任务为 cancelling（后端已受理，马上有反馈）
              setTasks((prev) =>
                prev.map((t) =>
                  t.task_id === taskId && t.status !== 'cancelling'
                    ? { ...t, status: 'cancelling' as const, message: '正在取消...' }
                    : t,
                ),
              )
            }}
            onResume={handleResume}
            onSelectTask={handleSelectTask}
            selectedTaskId={task?.task_id ?? null}
          />
        )}

        {/* 结果：实时任务成功 或 历史查看（有 result 或仅有模型产物都渲染）；
            滚动训练运行中展示"已跑段"的部分结果（实时刷新） */}
        {(((task?.result && task.status === 'success') || viewResult?.result) ||
          viewArtifacts || (task?.status === 'running' && task.partial_result?.nav?.length) ||
          viewResult?.partial_result?.nav?.length) && (() => {
          const r = (task?.status === 'success' ? task.result : viewResult?.result) || null
          const a = (task?.status === 'success' ? artifacts : viewArtifacts) || null
          // 运行中：实时任务 partial；查看历史：viewResult 携带的 partial（任务已停止但保留已跑段）
          const partial =
            task?.status === 'running'
              ? task.partial_result
              : (viewResult?.partial_result ?? null)
          return (
            <>
              {r ? (
                <>
                  <MetricCards result={r} />
                  <NavChart nav={r.nav} />
                  <LayerChart data={r.layer_returns} rebalance={form.layer_rebalance} />
                  <ICChart data={r.ic_analysis} />
                </>
              ) : partial?.nav?.length ? (
                <>
                  <div className="bg-white dark:bg-slate-800 rounded-xl shadow p-6 text-sm">
                    <span className="text-emerald-600 dark:text-emerald-400 font-medium">
                      {task?.status === 'running' || viewResult?.status === 'running'
                        ? '⏳ 滚动训练进行中'
                        : '⏸ 回测未完成（已停止/中断）'}
                    </span>
                    <span className="ml-2 text-slate-500 dark:text-slate-300">
                      已跑 {partial.segments_done}/{partial.segments_total} 段，以下为已完成部分的结果
                      {task?.status !== 'running' && viewResult?.status !== 'running' && '；如需继续，可在下方历史回测中点该任务的"续测"'}
                    </span>
                  </div>
                  <NavChart nav={partial.nav} endDate={partial.end_date} />
                  <LayerChart data={partial.layer_returns} rebalance={form.layer_rebalance} />
                  <ICChart data={partial.ic_analysis} />
                </>
              ) : (
                <div className="bg-white dark:bg-slate-800 rounded-xl shadow p-6 text-sm text-slate-400">
                  无回测记录
                </div>
              )}
              {a && <ModelArtifactsPanel artifacts={a} />}
              {/* 历史回测（复现模式）放在训练产物与调仓记录之间；运行中 partial 时走下方独立块，避免重复。
                  查看"未完成但已跑段"的历史任务时也显示历史表格，便于继续续测/查看其他任务 */}
              {(!partial || viewResult?.partial_result) && (
                <HistoryPanel
                  onUseParams={handleUseParams}
                  onReuseBacktest={handleReuseBacktest}
                  onResume={handleResume}
                  onViewResult={handleViewResult}
                  refreshKey={historyRefreshKey}
                  capacity={capacity}
                />
              )}
              {r?.trades && r.trades.length > 0 && <TradeLog trades={r.trades} />}
            </>
          )
        })()}

        {/* 既无结果也无模型产物时，历史回测仍显示（用于直接复用参数） */}
        {(!((task?.result && task.status === 'success') || viewResult?.result) && !viewArtifacts) && (
          <HistoryPanel
                        onUseParams={handleUseParams}
                        onReuseBacktest={handleReuseBacktest}
                        onResume={handleResume}
                        onViewResult={handleViewResult}
                        refreshKey={historyRefreshKey}
                        capacity={capacity}
                      />
        )}
      </main>
    </div>
  )
}
