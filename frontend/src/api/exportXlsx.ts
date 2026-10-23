import http from './http'

// ★ v2.11.58：从 `api.ts` 外提（原 L737–871 ✓）—— 见 `api.ts` 头部说明。
// ---------- XLSX 导出（v2.1.0）----------
// ⚠ 由**后端**生成（不是前端 SheetJS）：① 面板要"把曲线画出来" ⇒ 需要原生 Excel 图表，
//   SheetJS 社区版画不了图 ✗；② 「每期持仓明细」根本没下发给前端（测试进程内算完即丢 ✓）
//   ⇒ 只能后端取；③ 文件还能被「交易信号测试」拖回去复现净值 ⇒ 读写共用同一份契约 ✓。
export interface TopkCurveExportRequest {
  /** 单因子测试任务 id（拿曲线 / 参数 / 每期持仓索引 ✓） */
  task_id: string
  /** 因子 id / 名称 / 表达式（三者任一 ✓） */
  factor_id: string
  horizon?: number | null
  /** 当前选中的档位：decile（最强分位组）/ topk（固定 K） */
  item_kind: 'decile' | 'topk'
  item_k?: number | null
  item_quantile?: number | null
  /** 曲线口径：compound（复利，页面默认）/ arithmetic（算术累加） */
  basis?: 'compound' | 'arithmetic'
  /** 基准代码（空 ⇒ 不导基准列；「池内等权」是前端算的伪基准 ⇒ 也传空 ✓） */
  benchmark?: string
  /** 绩效指标用哪一档成本（"0.0000"/"0.0040"/"0.0080" ✓） */
  cost_tier?: string
  /**
   * 面板截图（**base64 PNG**，键 `nav`/`decile` ✓）—— 由 `captureChart.ts` 把当前渲染的
   * recharts `<svg>` 抓成 PNG ⇒ 文件里的图 = 你眼前的图（档位/基准/显隐 全部一致 ✓）。
   * 抓图失败时**不传**即可（后端会写明"未附图"并指路数值页 ✓）。
   */
  shots?: { nav?: string; decile?: string }
}

/** 事件研究导出的请求（v2.1.0 ✓）。 */
export interface EventStudyExportRequest {
  task_id: string
  /** 因子表达式/id（后端按它在任务里找触发明细与事件研究结果 ✓） */
  factor_id?: string | null
  /** 净值曲线的持有周期（第 2 页每期持仓也按它派生 ✓） */
  hold_days?: number
  cost?: number
  capital?: number
  benchmark?: string
  /** 面板的「最长持有」（回显用 ✓） */
  max_k?: number | null
  /** 面板截图（base64 PNG）：摘要卡片 / 主曲线 / 超额 / 净值 / 分布 ✓（整块截图，含图例与数值行 ✓） */
  shots?: { kpi?: string; curve?: string; excess?: string; nav?: string; dist?: string }
}

/**
 * 导出接口共用：**POST 取 xlsx，并读回后端建议的文件名**（响应头 `X-Export-Filename` ✓）。
 *
 * ⚠ 两个导出（持仓曲线 / 事件研究）走同一个后端形态 ⇒ 只维护这一处（否则超时、错误体解析
 *   这类细节会在两处各写一遍、慢慢就不一样了 ✗）。
 */
async function postXlsx(path: string, body: unknown): Promise<{ blob: Blob; filename: string | null }> {
  const res = await http.post(path, body, {
    responseType: 'blob',
    // 生成工作簿要几秒（含几千行持仓解析 + 贴图）⇒ 单独放宽，不用全局 30s ✓
    timeout: 180000,
  })
  const raw = res.headers?.['x-export-filename']
  let filename: string | null = null
  if (typeof raw === 'string' && raw) {
    try {
      filename = decodeURIComponent(raw)
    } catch {
      filename = raw
    }
  }
  return { blob: res.data as Blob, filename }
}

/** 导出「持仓期收益曲线」为 XLSX：返回 blob + 后端建议的文件名（响应头 ✓）。 */
export async function exportTopkCurveXlsx(
  req: TopkCurveExportRequest,
): Promise<{ blob: Blob; filename: string | null }> {
  return postXlsx('/factors/export/topk-xlsx', req)
}

/** 导出「事件研究」为 XLSX（第 1 页参数与面板截图，第 2 页每期持仓 ✓）。 */
export async function exportEventStudyXlsx(
  req: EventStudyExportRequest,
): Promise<{ blob: Blob; filename: string | null }> {
  return postXlsx('/factors/export/event-study-xlsx', req)
}

/**
 * ★★ v2.8.8b（用户 2026-10-16 ✓）：**离场曲线导出** ——
 * 「第一页是截图，后面有几个离场因子，就加几个页面导出」✓（用户要「方便我检查离场逻辑」✓）。
 * · 第 1 页 = **曲线截图**（前端按当前显隐抓的 PNG ✓）+ 参数/口径；
 * · 其后每个离场一页（含「对照（不离场）」✓）= 该次「每期全仓滚动」的**调仓记录** ✓
 *   （`原因` 列会明写「★离场卖出（离场信号触发）」⇒ 一眼看出离场何时生效 ✓）。
 */
export interface ExitCurveExportRequest {
  task_id: string
  /** 因子 id / 名称 / 表达式（三者任一，后端按它定位那一行 ✓） */
  factor_id: string
  horizon?: number | null
  /** `null`/不传 = 全部离场 + 对照（从基础行点进来 ✓）；数字 = 只导该条离场 ✓ */
  ei?: number | null
  /** 前端按**当前显隐**抓的曲线 PNG（base64 ✓，键 `curve` ✓） */
  shots?: Record<string, string>
}

export async function exportExitCurveXlsx(
  req: ExitCurveExportRequest,
): Promise<{ blob: Blob; filename: string | null }> {
  return postXlsx('/factors/export/exit-curve-xlsx', req)
}

/** 后端出错时**响应体是 JSON**（blob 请求下要手动解一下）⇒ 取出可读 detail ✓。 */
export async function blobErrorDetail(err: unknown): Promise<string> {
  const anyErr = err as { response?: { data?: unknown; status?: number } }
  const data = anyErr?.response?.data
  try {
    if (data instanceof Blob) {
      const text = await data.text()
      // ★ v2.11.51（写契约测试时抓到的真 bug ✗→✓）：**`JSON.parse` 必须单独 try** ——
      //   原来它和外层共用一个 try ✓ ⇒ 后端返回**纯文本**错误（不是 JSON ✓）时
      //   `JSON.parse` 抛错 ⇒ 直接跳到外层 catch ✗ ⇒ 下面那行"原样截断"**是死代码** ✗
      //   ⇒ 用户看到的是"导出失败（后端未返回可读原因）"✗，而后端其实**给了原因** ✓。
      //   现在：解析不出来就退回原文 ✓（截 300 字 ✓ 防超长）。
      try {
        const obj = JSON.parse(text) as { detail?: string }
        if (obj?.detail) return obj.detail
      } catch {
        // 不是 JSON ⇒ 往下走，用原文 ✓
      }
      return text.slice(0, 300)
    }
    if (typeof data === 'object' && data && 'detail' in data) {
      return String((data as { detail?: unknown }).detail)
    }
    if (typeof data === 'string' && data) return data.slice(0, 300)
  } catch {
    /* 解析失败就走兜底文案 ✓ */
  }
  return '导出失败（后端未返回可读原因，请查看后端日志）'
}
