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

export function navColor(k: string, i: number): string {
  return NAV_COLOR[k] ?? ['#0ea5e9', '#f59e0b', '#10b981', '#ef4444'][i % 4]
}

export function navDash(k: string): string | undefined {
  if (k === 'nav_exact_min' || k === 'nav_official_bench') return '5 3'
  if (k === 'benchmark' || k === 'baseline' || k === 'baseline_median') return '4 4'
  if (k === 'cash_even') return '5 3'
  return undefined
}
