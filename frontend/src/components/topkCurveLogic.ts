// 展示层的**纯函数**（★ v2.11.59 从 `TopkCurveModal.tsx` 外提 ✓）。
//
// 为什么外提：这些函数**没有 JSX、没有 state** ✓，但以前埋在 1319 行组件里 ⇒
// `niceAxis()`（Y 轴刻度：整数百分比 + 3~7 段 + 必须含 0）这种**边界条件多**的逻辑
// **一条测试都没有** ✗ —— 刻度算错不会报错，只是图误导人 ✓（典型"静默错"✗）。
// 外提后可单独测 ✓：见 `src/__tests__/topkCurveLogic.test.ts`。

import type { TopKCurveItem } from '../api'

export const pct = (v: number | null | undefined, nd = 2) =>
  v == null || !Number.isFinite(v) ? '-' : `${(v * 100).toFixed(nd)}%`

/** 有符号百分数：原始小数 → `+134.22%` / `-3.10%`；空值给 `-`。 */
export const signedPct = (v: number | null | undefined, nd = 2) =>
  v == null || !Number.isFinite(v) ? '-' : `${v >= 0 ? '+' : ''}${(v * 100).toFixed(nd)}%`

/** 累计值 → **净值（从 1 开始）**；缺失保持 null（前端断线）。
 *  后端按口径给「复利累乘」（`curves_compound`/`cum_compound`，**默认**）或「算术累加」
 *  （`curves`/`cum`）的累计值（都是「净值 − 1」的形式），+1 只是为了读起来是净值口径。 */
export const nav = (v: number | null | undefined) =>
  v == null || !Number.isFinite(v) ? null : 1 + v

/** 把数据范围 `[lo, hi]` 化为「**整数百分比 + 均匀分段**」的 Y 轴（v1.19.9）。
 *
 *  用户要求：十分位图刻度要**整数百分比**且**等距**（不要 `−71%` / `128.8%` / `499.1%` 这种）。
 *  做法：先把上下界按 **1%** 向外取整，再选一个「好看的」步长（1/2/5/10/20/25/50/100…%）
 *  使刻度数落在 3~7 个；`domain` 与 `ticks` 都落在**整数百分比**上，且必然含 **0**
 *  （0 是任何 step 的整数倍）。返回的是**小数域**（`0.5` = 50%），recharts 直接可用。
 */
export const NICE_STEPS = [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000]
export function niceAxis(lo: number, hi: number) {
  const pLo = Math.floor(Math.min(0, lo) * 100)
  const pHi = Math.ceil(Math.max(0, hi) * 100)
  const span = Math.max(1, pHi - pLo)
  const step = NICE_STEPS.find((s) => span / s <= 6) ?? Math.ceil(span / 6)
  const t0 = Math.floor(pLo / step) * step
  let t1 = Math.ceil(pHi / step) * step
  if (t1 === t0) t1 = t0 + step                      // 数据恒为 0：给一段最小可视范围
  const ticks: number[] = []
  for (let v = t0; v <= t1; v += step) ticks.push(v / 100)
  return { domain: [t0 / 100, t1 / 100] as [number, number], ticks }
}

/** 明细档显示名：分位组标「最强分位组 Q几」；固定 K 档标「固定 K=…」（默认档加注）。 */
export function itemLabel(it: TopKCurveItem, defaultK: number | null) {
  if (it.kind === 'decile') return `最强分位组 Q${it.quantile}`
  const isDefault = defaultK != null && it.k === defaultK
  return `固定 K=${it.k}${isDefault ? '（默认 10% 档）' : ''}`
}

/** 无量纲比率显示（夏普/索提诺/卡玛）：2 位小数，缺失给 `-`。 */
export const fmtRatio = (v: number | null | undefined) =>
  v == null || !Number.isFinite(v) ? '-' : v.toFixed(2)
