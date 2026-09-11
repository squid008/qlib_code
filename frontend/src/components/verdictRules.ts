/**
 * 0/1 信号「有效✓」判定规则（v1.18.32）。
 *
 * 背景：v1.18.20 起「有效」要求日配对超额 >0，但实测出现 0.081% 这类量级也被判有效 ——
 * 它连一次往返交易成本（约 0.2~0.35%）都覆盖不了，且与噪声无异，门槛明显过松。
 *
 * 现规则（表格「结论」列与事件研究弹窗提示共用，杜绝两处口径漂移）：
 *   ① 事件研究中位数 ≥ 0.50%（事件级口径）
 *   ② 绝对收益胜率 ≥ 55%（事件级口径）
 *   ③ 日配对均值超额 ≥ 门槛(k)，其中 门槛(k) = max(0.50%, 0.025% × k)
 *      - 0.50%：约等于覆盖一次往返交易成本后仍有富余
 *      - 0.025%/交易日：约合年化 6% 的机会成本门槛（k ≤ 20 日时被 0.50% 覆盖）
 *      - 例：k=20 → 0.50%；k=40 → 1.00%；k=60 → 1.50%
 *   ④ 日配对稳定：|HAC t| ≥ 2 或 日胜率 ≥ 55%（反向：|HAC t| ≥ 2 或 日胜率 ≤ 45%）
 *      - 两者皆缺失（配对日 < 2）→ 不通过（无法验证稳定性，宁严勿松）
 *
 * 「有效(反向)✓」为镜像：中位数 ≤ −0.50%、绝对收益胜率 ≤ 45%、超额 ≤ −门槛(k)、稳定性反向通过。
 * 无基准（旧结果 / 基准计算失败）时第③条不启用（保持向后兼容），但第④条仍然要求。
 */
export const EXCESS_THRESHOLD_BASE = 0.005
export const EXCESS_THRESHOLD_PER_DAY = 0.00025

/** 超额门槛（按持有期缩放）：max(0.5%, 0.025% × k)。 */
export function excessThresholdOf(k?: number | null): number {
  const kk = k == null || !Number.isFinite(k) || k <= 0 ? 1 : k
  return Math.max(EXCESS_THRESHOLD_BASE, EXCESS_THRESHOLD_PER_DAY * kk)
}

export interface PairStats {
  /** 日配对差值 t（优先 HAC 稳健 t） */
  t?: number | null
  /** 日配对相对收益胜率（0-1） */
  win?: number | null
}

export interface PairStability {
  ok: boolean
  tOk: boolean
  winOk: boolean
  /** 两项都缺失（配对日 < 2），无法验证 */
  missing: boolean
  tVal: number | null
  winVal: number | null
}

/** 日配对稳定性判定（方案 B 第④条）。reverse=true 用于反向有效（胜率 ≤45%）。 */
export function pairStabilityOf(p: PairStats, reverse = false): PairStability {
  const tVal = p.t == null || !Number.isFinite(p.t) ? null : p.t
  const winVal = p.win == null || !Number.isFinite(p.win) ? null : p.win
  const tOk = tVal != null && Math.abs(tVal) >= 2
  const winOk = winVal != null && (reverse ? winVal <= 0.45 : winVal >= 0.55)
  const missing = tVal == null && winVal == null
  return { ok: !missing && (tOk || winOk), tOk, winOk, missing, tVal, winVal }
}
