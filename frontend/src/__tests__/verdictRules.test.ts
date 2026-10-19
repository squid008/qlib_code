/**
 * `verdictRules` 测试（★ v2.9.79，2026-10-19 ✓ —— 纯逻辑，此前零测试 ✗）。
 *
 * ## 为什么这块最该有测试
 *
 * 它是「**这只信号算不算有效**」的**唯一判定** ✓（表格「结论」列与事件研究弹窗共用 ✓，
 * 注释写明"杜绝两处口径漂移" ✓）。判定松一点 ⇒ 一堆噪声信号被标成"有效✓"✗；
 * 紧一点 ⇒ 真信号被埋 ✗。而**用户只看那个 ✓** ✓。
 *
 * 本文件钉三类东西 ✓：
 * 1. **按持有期缩放的门槛** ✓（`max(0.5%, 0.025%×k)` ✓，k=20/40/60 ⇒ 0.5%/1%/1.5% ✓）；
 *    非法 k（null/0/负/NaN）一律按 **k=1** 处理 ✓（回落而不是抛 ✗）；
 * 2. **边界值必须"含等号"** ✓（`med >= 0.005` ✓、`win >= 0.55` ✓）——
 *    写 `>` 会让"刚好 0.50%"这类完全不违规的样本被判否 ✗，而用户以为是数据问题 ✗；
 * 3. **无基准（`ex == null`）时第③条不启用** ✓（旧结果向后兼容 ✓），但**稳定性仍必须过** ✓ ——
 *    "无法验证稳定性"时要**判否**（宁严勿松 ✓）。
 */
import { describe, expect, it } from 'vitest'
import {
  EXCESS_THRESHOLD_BASE,
  EXCESS_THRESHOLD_PER_DAY,
  excessThresholdOf,
  isReverseValidAt,
  isValidAt,
  pairStabilityOf,
} from '../components/verdictRules'

describe('excessThresholdOf（超额门槛按持有期缩放）', () => {
  it('常量本身', () => {
    expect(EXCESS_THRESHOLD_BASE).toBe(0.005)
    expect(EXCESS_THRESHOLD_PER_DAY).toBe(0.00025)
  })

  it('k=20 ⇒ 0.5%；k=40 ⇒ 1%；k=60 ⇒ 1.5%（与注释里的例子逐一核对 ✓）', () => {
    expect(excessThresholdOf(20)).toBeCloseTo(0.005, 12)
    expect(excessThresholdOf(40)).toBeCloseTo(0.01, 12)
    expect(excessThresholdOf(60)).toBeCloseTo(0.015, 12)
  })

  it('非法 k（null / 0 / 负数 / NaN / undefined）一律按 k=1 ⇒ 回落到 0.5%（不抛 ✗）', () => {
    for (const k of [null, undefined, 0, -5, Number.NaN]) {
      expect(excessThresholdOf(k as number | null)).toBeCloseTo(0.005, 12)
    }
  })
})

describe('pairStabilityOf（第④条：日配对稳定性）', () => {
  it('|t| ≥ 2 或 胜率 ≥ 55% 任一即可', () => {
    expect(pairStabilityOf({ t: -3, win: null }).ok).toBe(true)     // 只看 t ✓
    expect(pairStabilityOf({ t: 0.5, win: 0.6 }).ok).toBe(true)    // 只看胜率 ✓
    expect(pairStabilityOf({ t: 0.5, win: 0.5 }).ok).toBe(false)   // 都不够 ✓
  })

  it('两项都缺失（配对日 < 2）⇒ 判否（宁严勿松 ✓）', () => {
    const r = pairStabilityOf({ t: null, win: null })
    expect(r.missing).toBe(true)
    expect(r.ok).toBe(false)
  })

  it('反向口径：胜率 ≤ 45% 才算稳（50% 不算 ✓）', () => {
    expect(pairStabilityOf({ t: null, win: 0.44 }, true).ok).toBe(true)
    expect(pairStabilityOf({ t: null, win: 0.5 }, true).ok).toBe(false)
    expect(pairStabilityOf({ t: null, win: 0.5 }, false).ok).toBe(false)
  })

  it('NaN/Infinity 当作缺失（不参与判定 ✗）', () => {
    const r = pairStabilityOf({ t: Number.NaN, win: Number.POSITIVE_INFINITY })
    expect(r.tVal).toBeNull()
    expect(r.winVal).toBeNull()
    expect(r.missing).toBe(true)
  })
})

describe('isValidAt（正向「有效✓」）', () => {
  const okArgs = [20, 0.01, 0.6, 0.02, true] as const

  it('四项都满足 ⇒ 有效', () => {
    expect(isValidAt(...okArgs)).toBe(true)
  })

  it('⭐ 边界值含等号：中位数正好 0.5%、胜率正好 55% ⇒ 仍然有效', () => {
    expect(isValidAt(20, 0.005, 0.55, 0.02, true)).toBe(true)
  })

  it('任一条不足 ⇒ 无效', () => {
    expect(isValidAt(20, 0.0049, 0.6, 0.02, true)).toBe(false)   // 中位数差一点 ✓
    expect(isValidAt(20, 0.01, 0.549, 0.02, true)).toBe(false)   // 胜率差一点 ✓
    expect(isValidAt(40, 0.01, 0.6, 0.005, true)).toBe(false)    // 超额 < 1%（k=40 门槛 ✓）
    expect(isValidAt(20, 0.01, 0.6, 0.02, false)).toBe(false)    // 稳定性不过 ✓
  })

  it('⭐ 无基准（ex = null）⇒ 第③条不启用（向后兼容 ✓），但稳定性仍必须过', () => {
    expect(isValidAt(20, 0.01, 0.6, null, true)).toBe(true)
    expect(isValidAt(20, 0.01, 0.6, null, false)).toBe(false)
  })

  it('缺中位数或胜率 ⇒ 无效（null 不能当 0 用 ✗）', () => {
    expect(isValidAt(20, null, 0.6, 0.02, true)).toBe(false)
    expect(isValidAt(20, 0.01, null, 0.02, true)).toBe(false)
  })
})

describe('isReverseValidAt（反向「有效(反向)✓」）', () => {
  it('镜像条件四项都满足 ⇒ 反向有效', () => {
    expect(isReverseValidAt(20, -0.01, 0.4, -0.02, true)).toBe(true)
  })

  it('⭐ 边界含等号：中位数正好 −0.5%、胜率正好 45%、超额正好 −门槛 ⇒ 仍有效', () => {
    expect(isReverseValidAt(20, -0.005, 0.45, -excessThresholdOf(20), true)).toBe(true)
  })

  it('方向不镜像 ⇒ 无效（正的中位数不能算反向 ✓）', () => {
    expect(isReverseValidAt(20, 0.01, 0.4, -0.02, true)).toBe(false)
    expect(isReverseValidAt(20, -0.01, 0.6, -0.02, true)).toBe(false)
    expect(isReverseValidAt(20, -0.01, 0.4, 0.02, true)).toBe(false)  // 超额是正的 ✗
  })

  it('无基准时同正向：第③条不启用，稳定性仍需过', () => {
    expect(isReverseValidAt(20, -0.01, 0.4, null, true)).toBe(true)
    expect(isReverseValidAt(20, -0.01, 0.4, null, false)).toBe(false)
  })
})
