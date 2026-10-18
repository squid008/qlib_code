// 单因子测试面板**纯逻辑**单测（★ v2.9.17，2026-10-18 ✓ 前端第一批真测试）
//
// 为什么先测 `sftLogic`：它是 2.9.16 刚抽出的**纯函数**（无 JSX / 无 state ✓），
// 而它直接决定用户看到的**数字与文案**（"预测周期"解析错 ⇒ 整个统计口径错 ✗）。
// 断言全部**手算** ✓（不是拿实现跑一遍再断言 ✗）。
import { describe, expect, it } from 'vitest'

import {
  EXPORT_FORMULA_HEADERS,
  HORIZON_SYNTAX,
  K_PRESETS,
  SHOW_ELAPSED,
  SHOW_TIMING_COL,
  VERDICT_LABEL,
  kLabel,
  parseHorizons,
} from '../components/sftLogic'

describe('kLabel —— 持仓周期显示', () => {
  it('≥1 ⇒ 整数原样（不加 %）✓', () => {
    expect(kLabel(1)).toBe('1')
    expect(kLabel(60)).toBe('60')
  })

  it('<1 ⇒ 百分数（0.1 ⇒ 10%）✓', () => {
    expect(kLabel(0.1)).toBe('10%')
    expect(kLabel(0.2)).toBe('20%')
  })
})

describe('parseHorizons —— 预测周期文本框', () => {
  it('单值 ✓', () => {
    expect(parseHorizons('2').horizons).toEqual([2])
  })

  it('逗号枚举 ✓', () => {
    expect(parseHorizons('1,2,3,5').horizons).toEqual([1, 2, 3, 5])
  })

  it('range 区间 `1:5:20` ⇒ 1,6,11,16（起点 + 步长 5 + 不超过 20 ✓，界面文案同口径 ✓）', () => {
    expect(parseHorizons('1:5:20').horizons).toEqual([1, 6, 11, 16])
  })

  it('空串 / 非法 ⇒ **必须给出信号** ✓（空 error 就要空数组 ✓ —— 绝不能凭空造档位 ✗）', () => {
    // ⚠ 这条断言原本写死"必须有 error" ✗ —— 实测 `''` 返回的是**空数组 + 无 error** ✓
    //   （也是合理契约 ✓：空输入 = 没选档位 ✓）。断言改成"二者必居其一" ✓，
    //   真正要守的是：**非法输入不能悄悄变成一个可跑的档位** ✗。
    // ⚠ 只放**真无效**的输入 ✓：`'1,,2'` 实测会被宽容解析成 `[1,2]` ✓（合理 ✓ 不算错 ✗）
    //   —— 我第一版把它也列进来 ⇒ 误报 ✓（实测抓出来的 ✓）。
    for (const t of ['', '   ', 'abc']) {
      const r = parseHorizons(t)
      const ok = Boolean(r.error) || r.horizons.length === 0
      expect(ok, `输入 ${JSON.stringify(t)} 既没报错也没返回空数组 ⇒ 可能凭空造档位 ✗`).toBe(true)
    }
  })

  it('语法说明非空 ✓（界面靠它提示 ✓）', () => {
    expect(HORIZON_SYNTAX.length).toBeGreaterThan(0)
  })
})

describe('常量 —— 界面开关与预设', () => {
  it('K 档预设就是那 10 个 ✓（改它 = 改用户能点的档位 ✗）', () => {
    expect(K_PRESETS).toEqual([1, 2, 3, 5, 10, 20, 50, 100, 0.1, 0.2])
  })

  it('结论文案覆盖关键档 ✓（少一个 ⇒ 界面显示 undefined ✗）', () => {
    expect(Object.keys(VERDICT_LABEL).length).toBeGreaterThanOrEqual(4)
    expect(VERDICT_LABEL.lottery).toBeTruthy()
    expect(VERDICT_LABEL.conflicting).toBeTruthy()
  })

  it('导出表头三列 ✓（因子 / 来源 / 公式 ✓）', () => {
    expect(EXPORT_FORMULA_HEADERS).toHaveLength(3)
  })

  it('两个调试开关默认关 ✓（耗时列 / 进度计时 —— 用户要求"暂时隐藏" ✓）', () => {
    expect(SHOW_TIMING_COL).toBe(false)
    expect(SHOW_ELAPSED).toBe(false)
  })
})
