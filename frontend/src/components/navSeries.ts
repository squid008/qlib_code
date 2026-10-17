/**
 * 净值曲线的**共享展示口径**（v1.19.60 从 `SignalTestPanel` 提取）。
 *
 * 为什么提取：单因子「事件研究」面板现在也要画同款净值曲线（两种资金方案 + 基准）⇒
 * 曲线名/配色/线型必须**只有一份**，否则两个页面同一条曲线会出现两种叫法或两种颜色
 * （本项目最忌讳的"同一件事两套口径"）。
 */

export const NAV_LABEL: Record<string, string> = {
  benchmark: '基准',
  nav_sim_fee: 'A 模拟它的成本',
  nav_my_cost: 'B 我的成本',
  nav_exact: 'C 精确（成交价+手续费）',
  nav_exact_min: 'C′ 现金非负下界口径',
  nav_official: '官方净值（聚宽）',
  nav_official_bench: '官方基准（聚宽）',
  event_even: '事件驱动再平衡（有信号/到期就调平）',
  cash_even: '现金等分（不主动再平衡）',
  // v1.19.76：按批次分片（同事的 W_0..W_4 口径）——每批固定份额、批内等权、只在名单刷新时动该批
  batch_even: '按批次分片（每批固定份额·批内等权·只动刷新的票）',
  // ★ v2.1.9：`period_roll` = **每期全仓滚动**（实测口径，含资金约束/整手/涨跌停 ✓）——
  //   拖自家导出文件（"每期一批组合"）进来时**默认看这条** ✓：它才是面板那条
  //   "每期等权换仓、持有到下一个调仓日"曲线的实盘版 ✓（见 backend/app/signals/period_roll.py ✓）。
  //   ⚠ `event_even`（重复出现的票被当成新买入 ✗）与 `batch_even`（每批固定 1/N 份额 ✗）
  //     都不是它的对应语义 ✓。
  period_roll: '每期全仓滚动（调仓日按当期名单等权重建 · 实盘口径）',
  // ★ 2026-10-14：**未触发组等权**参照线（用户："加一条未触发组等权参照线，我看看到底咋样"）
  //   为什么要它：事件研究的**真实基准**就是它（超额曲线用的就是它），但旧图里只有「沪深300」灰线
  //   ⇒ 实测把灰线当成"全 A 等权" ✗（沪深300 0.957 vs 池内等权 1.4125，差 48% ⇒ 结论看反）
  // ★★ v2.9.1（用户 2026-10-17 ✓）：**与策略同持有期** ✗→✓ ——
  //   用户原话：「那肯定是**同口径**才能比啊，未触发组也得是**同持有期**才行啊」✓。
  //   旧口径：未触发组每日等权、**只持 1 天**（天天换仓）✗ ⇒ 策略持 60 天、参照持 1 天
  //   ⇒ 两条线持有期差 60 倍 ⇒「策略 1.449 vs 参照 0.785」不是 alpha ✗（2026-10-17 结论 ✓）。
  bench_equal: '未触发组等权（参照 · 同持有期）',
}

export const NAV_COLOR: Record<string, string> = {
  benchmark: '#94a3b8',
  nav_sim_fee: '#0ea5e9',
  nav_my_cost: '#f59e0b',
  nav_exact: '#10b981',
  nav_exact_min: '#a855f7',
  nav_official: '#e11d48',
  nav_official_bench: '#9ca3af',
  event_even: '#7c3aed',
  cash_even: '#0ea5e9',
  batch_even: '#14b8a6',
  period_roll: '#f43f5e',                 // 实盘口径 ⇒ 用醒目色，别和模型曲线看混 ✓
  bench_equal: '#f59e0b',                 // ★ 未触发组等权（参照线）：橙色，别和灰的沪深300 混 ✓
}

/** 方案名 → 中文（`event_even_band5` 这类带死区的自动展开）。 */
export function allocLabel(mode: string): string {
  const m = /^event_even_band(.+)$/.exec(mode)
  if (m) return `事件等权 · 死区 ${m[1].replace('p', '.')}%（自动对比档）`
  return NAV_LABEL[mode] ?? mode
}

export function navLabel(k: string): string {
  return NAV_LABEL[k] ?? allocLabel(k)
}

/**
 * 方案名 → **"它到底怎么调仓"的一句话说明**（v2.1.10；用户 2026-10-11：
 * "几个图例鼠标放上去弹出的 TAG 解释一下他们具体是怎么调仓的吧，我都分不清有啥区别"）。
 *
 * ⚠ 这里只写**规则本身**，不写"哪条更好" —— 优劣取决于你的用法 ✓。
 */
export const NAV_DESC: Record<string, string> = {
  event_even:
    '事件驱动再平衡：**只要当天有买入成交**，就把**全部持仓**重新调平到等权（卖出不触发调平 ✓，' +
    '期中不动 ✓）。逐票按持有天数计寿：到期就卖；' +
    '⚠ **期间再收到买入信号 ⇒ 该票的持有天数【重置为 N 天】**（不是"加 N 天"✗，' +
    '并撤回已挂的到期卖单 ✓，计数见 `hold_refreshes`）。' +
    '⚠ 因此本模式读到的"样本均值"（事件研究里**每次触发**算一个样本 ✓）与' +
    '**组合净值**不是一回事 ✗：同一只票可能被重复触发 2~3 次（实测 1 : 2.65 ✓）' +
    '而组合里它只持一轮 ⇒ 请对照结果里的「首次触发 / 续期再触发」分组看 ✓。' +
    '⚠ 它调平的是"当时手上的所有票"（含上一期遗留、还没到期的），且到期日会与调仓日错开 ' +
    '⇒ 交易与手续费明显高于每期全仓滚动（实测同一份数据：手续费 28.9% vs 9.1%）。',
  cash_even:
    '现金等分：新买入只用当时可用的现金等分买入，从不做再平衡（老仓一直拿着，不再调整权重）。',
  batch_even:
    '按批次分片：信号里带批次（例如一周 5 批 W_0…W_4）时，资金按批数平分、批间互不挪用，' +
    '只在本批名单刷新时动本批的票。⚠ 单一批次（每期一批）的文件不该用它 ⇒ 每批固定 1/N，' +
    '旧批到期回款会变成不再投入的现金。',
  period_roll:
    '每期全仓滚动（实盘口径）：只在调仓日把当时的全部权益按当期名单等权重建' +
    '（先卖不在名单的、再把在名单里的调到等权），期中完全不动；' +
    '卖不掉（跌停/停牌）顺延、买不进（涨停）不追、整手 100 股、资金不足按可用现金缩量。' +
    '⇒ 面板那条每期等权换仓曲线的实盘版（多了涨跌停与资金约束）。',
  benchmark: '基准：同期基准指数的净值（与其它曲线同一起点刻度）。',
  bench_equal:
    '未触发组等权（参照线 · **与策略同持有期**）：每个交易日把「全池 − 当天触发股」等权买入、' +
    '**持有「持仓周期」那么多天**、到期卖出（重叠批次按每只票等权合并）；收盘对收盘、不含成本 ✓。' +
    '⚠ 旧版是"每日等权、只持 1 天、天天换仓" ✗ ⇒ 策略持 60 天、参照持 1 天 ⇒ 持有期差 60 倍 ⇒ ' +
    '两条线的差距**大多不是 alpha** ✗（2026-10-17 定为口径 bug ✓）；现在同口径才是"去掉信号会怎样" ✓。' +
    '⚠ 它与灰色那条「基准」（默认沪深300）**不是一回事** ✗：一个是可买 ETF、一个是池内等权 ✓。',
}

/** 取方案说明（`event_even@0.05` 这类带死区的自动展开 ✓）。 */
export function navDesc(k: string): string | undefined {
  if (NAV_DESC[k]) return NAV_DESC[k]
  const m = /^event_even_band(.+)$/.exec(k)
  if (m) {
    return (
      `事件等权 · 死区 ${m[1].replace('p', '.')}%（自动对比档）：规则与事件驱动再平衡相同，` +
      '但只有当某只票偏离等权超过该比例才动手 —— 用来对比死区到底能省多少手续费（' +
      '实测死区 0 的全仓调平会吃掉可观本金）。'
    )
  }
  return undefined
}

export function navColor(k: string, i: number): string {
  return NAV_COLOR[k] ?? ['#0ea5e9', '#f59e0b', '#10b981', '#ef4444'][i % 4]
}

export function navDash(k: string): string | undefined {
  if (k === 'nav_exact_min' || k === 'nav_official_bench') return '5 3'
  if (k === 'benchmark' || k === 'baseline' || k === 'baseline_median') return '4 4'
  if (k === 'cash_even') return '5 3'
  return undefined
}
