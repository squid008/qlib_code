/**
 * `sftLogic`（单因子测试面板的**纯逻辑**）的判据测试（★ v2.11.51）。
 *
 * ## 为什么优先测这个文件
 * 它是面板里唯一"**决定这个因子算不算有效**"的地方 ✓ ——
 * 结论错了不会报错，只会让人**拿一个无效因子去实盘** ✗。
 * 而且它是纯函数（无 JSX / 无 state ✓）⇒ 测起来最便宜 ✓。
 *
 * ## 口径提醒（写测试时踩的）
 * · `verdictOf` 对 **0/1 信号**走「事件研究」分支（`esPointOf` 有值才进 ✓），
 *   对**连续因子**走 diff/IC 分支 ✓ —— 两条路的判据**完全不同** ✓，别混着断言 ✗。
 * · `stable` 的默认是 **true**：`daily_t_hac` 与 `daily_t` 都为空时视为"无法证伪"✓
 *   （不是"通过"✓，是**不加这道门槛** ✓）。
 */
import { describe, expect, it } from 'vitest'

import type { TestResult } from '../components/sftLogic'
import {
  esExcessOf,
  esPointOf,
  exportHeaders,
  horizonHint,
  kLabel,
  parseHorizons,
  reverseNoteOf,
  reverseTitleOf,
  verdictOf,
  watchReasonOfBinary,
  watchReasonOfContinuous,
} from '../components/sftLogic'

/** 造一个最小可用的测试结果（字段太多，用 as 收口 ✓ 只在测试里这么干）。 */
function mk(over: Record<string, unknown> = {}): TestResult {
  return {
    error: null,
    diff: 0.02,
    p_value: 0.01,
    is_binary: false,
    ic: 0.03,
    icir: 0.2,
    daily_t_hac: null,
    daily_t: null,
    daily_win: null,
    n_obs: 200,
    trigger: undefined,
    event_study: undefined,
    nonzero_ratio: null,
    horizon: 5,
    ...over,
  } as unknown as TestResult
}

describe('esPointOf / esExcessOf（事件研究取点）', () => {
  it('没有事件研究 ⇒ null', () => {
    expect(esPointOf(mk())).toBeNull()
    expect(esPointOf(mk({ event_study: { curve: [] } }))).toBeNull()
  })

  it('命中当前持有期就返回它；否则退化为最长持有期', () => {
    const r = mk({
      horizon: 5,
      event_study: { curve: [{ k: 1, median: 0.01 }, { k: 5, median: 0.02 }, { k: 10, median: 0.03 }] },
    })
    expect(esPointOf(r)).toMatchObject({ k: 5 })
    const r2 = mk({ horizon: 7, event_study: (r as unknown as { event_study: unknown }).event_study })
    expect(esPointOf(r2)).toMatchObject({ k: 10 })
  })

  it('超额：无 baseline / 无 ks / 找不到 k / 值为 null 都返回 null', () => {
    expect(esExcessOf(mk())).toBeNull()
    expect(esExcessOf(mk({ event_study: { baseline: {} } }))).toBeNull()
    const r = mk({ event_study: { baseline: { ks: [1, 5], excess: [0.001, 0.02] } } })
    expect(esExcessOf(r, 5)).toBe(0.02)
    expect(esExcessOf(r, 99)).toBeNull()      // k 不在 ks 里 ✓
    expect(esExcessOf(r)).toBe(0.02)          // k 缺省 ⇒ 取最后一个 ✓
    const r2 = mk({ event_study: { baseline: { ks: [5], excess: [null] } } })
    expect(esExcessOf(r2, 5)).toBeNull()
  })
})

describe('parseHorizons（预测周期语法）', () => {
  it('单个数字 / 逗号枚举', () => {
    expect(parseHorizons('5').horizons).toEqual([5])
    expect(parseHorizons('1,2,5').horizons).toEqual([1, 2, 5])
  })

  it('全角逗号 / 空格 / 分号都当分隔符', () => {
    expect(parseHorizons('1，2 5;7').horizons).toEqual([1, 2, 5, 7])
  })

  it('⭐⭐ 两段 a:b = 起点:终点（步长 1），**不是**每隔 b', () => {
    expect(parseHorizons('1:5').horizons).toEqual([1, 2, 3, 4, 5])
  })

  it('三段 a:b:c = 起点:步长:终点（含终点）', () => {
    expect(parseHorizons('1:5:20').horizons).toEqual([1, 6, 11, 16])
  })

  it('去重且保持顺序', () => {
    expect(parseHorizons('3,1,3,2').horizons).toEqual([3, 1, 2])
  })

  it('各种非法输入都要给**能照做**的错', () => {
    expect(parseHorizons('abc').error).toContain('无法识别')
    expect(parseHorizons('5:0:9').error).toContain('步长必须为正整数')
    expect(parseHorizons('9:3').error).toContain('不能大于终点')
    expect(parseHorizons('0').error).toContain('1~250')
    expect(parseHorizons('251').error).toContain('1~250')
  })

  it('⭐⭐ `0` 非法值必须被抓到（`out.find` 返回 0 是假值 —— 这里钉住那个坑）', () => {
    const r = parseHorizons('1,0,3')
    expect(r.error, '写 `if (bad)` 会让 0 静默通过 ✗').toContain('1~250')
    expect(r.horizons).toEqual([])
  })

  it('空串 ⇒ 空列表且不报错（由调用方提示"请填写"）', () => {
    expect(parseHorizons('')).toEqual({ horizons: [] })
  })
})

describe('horizonHint（悬停实时预览）', () => {
  it('有错就报错', () => {
    expect(horizonHint('abc')).toContain('⚠')
  })

  it('空输入给明确提示', () => {
    expect(horizonHint('')).toContain('空')
  })

  it('⭐ 展开超过 10 个要**截断**（`1:60` 会展开 60 个数，全列进 tooltip 没法读 ✗）', () => {
    const t = horizonHint('1:60')
    expect(t).toContain('60 个周期')
    expect(t).toContain('…')
  })

  it('少量就全列出来', () => {
    expect(horizonHint('1,2,3')).toContain('1, 2, 3')
  })
})

describe('kLabel / exportHeaders', () => {
  it('k<1 按百分比显示（0.1 ⇒ 10%）', () => {
    expect(kLabel(0.1)).toBe('10%')
    expect(kLabel(0.2)).toBe('20%')
    expect(kLabel(5)).toBe('5')
  })

  it('导出表头随分位组数变长（qCols=10 比 qCols=5 多 5 列）', () => {
    expect(exportHeaders(10).length).toBe(exportHeaders(5).length + 5)
  })
})

describe('verdictOf —— 连续因子（diff / IC 分支）', () => {
  it('diff>0 且无稳定性证据 ⇒ 有效', () => {
    expect(verdictOf(mk()).kind).toBe('good')
  })

  it('⭐⭐ IC 与 diff 方向相反且 ICIR 够大 ⇒ 方向矛盾（优先于"有效"）', () => {
    const r = mk({ ic: -0.05, icir: -0.2, diff: 0.02 })
    const v = verdictOf(r)
    expect(v.conflicting).toBe(true)
    expect(v.kind).toBe('conflicting')
  })

  it('连续因子 diff<0 + IC<0 + 显著 + ICIR 稳定 ⇒ 有效(反向)', () => {
    const r = mk({ diff: -0.02, ic: -0.05, icir: -0.2, p_value: 0.01 })
    expect(verdictOf(r).kind).toBe('goodReverse')
  })

  it('⭐ 基础条件成立但**日配对不稳定** ⇒ 时间集中（不是"有效" ✗）', () => {
    // diff>0 但 daily_win<0.5 且 |t|<2 ⇒ stable=false（
    const r = mk({ diff: 0.02, daily_t_hac: 0.5, daily_win: 0.4 })
    expect(verdictOf(r).kind).toBe('timeConcentrated')
  })

  it('什么都不满足 ⇒ 待观察，且给出**逐条原因**', () => {
    const r = mk({ diff: -0.01, ic: 0.01, icir: 0.01 })
    const v = verdictOf(r)
    expect(v.kind).toBe('watch')
    expect(v.watchReason).toContain('待观察')
    expect(v.watchReason).toContain('未通过')
  })

  it('测试本身报错 ⇒ 待观察且原因里带错误信息', () => {
    const v = verdictOf(mk({ error: '数据不足' }))
    expect(v.kind).toBe('watch')
    expect(v.watchReason).toContain('数据不足')
  })
})

describe('verdictOf —— 0/1 信号（事件研究分支）', () => {
  const curve = (med: number, win: number, mean = 0.03) => ({
    curve: [{ k: 5, median: med, win, mean, n: 100 }],
    baseline: { ks: [5], excess: [0.02] },
  })

  it('中位数/胜率/超额都达标 + 日配对稳定 ⇒ 有效', () => {
    const r = mk({ is_binary: true, horizon: 5, daily_t_hac: 3, event_study: curve(0.02, 0.6) })
    expect(verdictOf(r).kind).toBe('good')
  })

  it('⭐⭐ 中位数≈0 但均值很高 ⇒ **彩票型**（收益靠极少数事件撑起）', () => {
    const r = mk({
      is_binary: true, horizon: 5, daily_t_hac: 3,
      event_study: curve(0.001, 0.5, 0.2),
    })
    expect(verdictOf(r).kind).toBe('lottery')
  })

  it('反向条件成立 ⇒ 有效(反向)（但必须带"这不是好消息"的说明）', () => {
    const r = mk({
      is_binary: true, horizon: 5, daily_t_hac: -3, n_obs: 200,
      trigger: { count: 2 },
      event_study: { curve: [{ k: 5, median: -0.02, win: 0.3, mean: -0.02, n: 100 }],
        baseline: { ks: [5], excess: [-0.02] } },
    })
    const v = verdictOf(r)
    expect(v.kind).toBe('goodReverse')
    expect(reverseTitleOf(r)).toContain('这不是"好消息"')
  })

  it('待观察时说明里要带**差多少**（用户要能照做）', () => {
    // ⚠ mean 要**小**：给 0.03 会满足「彩票型」（均值 > max(0.5%, 中位数×3)）⇒ 判成 lottery ✗
    //   —— 我第一版就是这么写的，实测报 `expected 'lottery' to be 'watch'` ✓（逻辑没错，是我数据凑巧 ✗）
    const r = mk({
      is_binary: true, horizon: 5, daily_t_hac: 0.5,
      event_study: curve(0.001, 0.5, 0.002),
    })
    const v = verdictOf(r)
    expect(v.kind).toBe('watch')
    expect(v.watchReason).toContain('中位数')
    expect(v.watchReason).toContain('差')
    expect(v.watchReason).toContain('「有效✓」需同时满足')
  })
})

describe('watchReasonOf*（原因文案）', () => {
  it('binary：无基准时明确说"未参与判定"（不是默默当通过 ✓）', () => {
    const txt = watchReasonOfBinary(
      { k: 5, median: 0.001, win: 0.5, n: 100 } as never,
      null, 0.005,
      { ok: false, tOk: false, winOk: false, missing: false, tVal: 0.5, winVal: 0.4 } as never,
    )
    expect(txt).toContain('无基准，未参与判定')
    expect(txt).toContain('日配对不稳定')
  })

  it('continuous：diff 为空 ⇒ 说"无触发/未触发差值"', () => {
    expect(watchReasonOfContinuous(mk({ diff: null }), false, false)).toContain('无触发/未触发差值')
  })

  it('continuous：is_binary 且不显著 ⇒ 明确点出 p 值门槛', () => {
    const txt = watchReasonOfContinuous(mk({ is_binary: true, p_value: 0.3 }), false, true)
    expect(txt).toContain('p 值 ≥0.05')
  })
})

describe('reverseNoteOf（反向说明必须说清"可不可落地"）', () => {
  it('触发占比 ≤5% ⇒ 未触发组≈全池，反向只是拿 beta', () => {
    const txt = reverseNoteOf(mk({ n_obs: 1000, trigger: { count: 20 } }))
    expect(txt).toContain('2.000%')
    expect(txt).toContain('beta')
  })

  it('占比 >5% ⇒ 只在能反向交易时才有意义（A 股空头不可得）', () => {
    const txt = reverseNoteOf(mk({ n_obs: 1000, trigger: { count: 200 } }))
    expect(txt).toContain('空头收益不可得')
  })

  it('占比未知时要如实说"未知"，不能编一个数', () => {
    expect(reverseNoteOf(mk())).toContain('触发占比未知')
  })

  it('⭐ 优先用 count/n_obs（nonzero_ratio 只有 4 位小数，会把 0.028% 显示成 0.050%）', () => {
    const txt = reverseNoteOf(mk({ n_obs: 10000, trigger: { count: 3 }, nonzero_ratio: 0.0005 }))
    expect(txt).toContain('0.030%')
  })
})
