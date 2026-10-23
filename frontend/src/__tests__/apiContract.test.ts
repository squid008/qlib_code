/**
 * `api.ts` 的**接口契约**测试（★ v2.11.51）。
 *
 * ## 为什么值得测这层
 * 这 40 来个函数只有两三行 ✓，看起来"没什么可测"✗ —— 但它们携带的是**前后端之间唯一的契约**：
 * 方法 / 路径 / 参数名 / 超时 ✓。写错一个（`/backtest/${id}` 写成 `/backtests/${id}` ✓、
 * `allow_duplicate` 拼成 `allowDuplicate` ✓）**类型系统一个都拦不住** ✗，
 * 只会在运行时 404 / 400 ✓ —— 而且往往是**只在那一条路径上**坏掉 ✓（别的功能照常 ✓）。
 *
 * ## 做法
 * 把 `axios.create()` 打桩 ⇒ 记录每次调用的 `(方法, 路径, 参数)` ✓，逐条断言 ✓。
 * ⚠ `vi.mock` 会被提升到文件顶部 ⇒ 桩必须用 `vi.hoisted` 建 ✓
 *   （否则会报 "Cannot access 'xxx' before initialization" ✗）。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const h = vi.hoisted(() => {
  const calls: Array<{ m: string; url: string; a?: unknown; b?: unknown }> = []
  const mk = (m: string) => async (url: string, a?: unknown, b?: unknown) => {
    calls.push({ m, url, a, b })
    const isBlob =
      (a as { responseType?: string } | undefined)?.responseType === 'blob' ||
      (b as { responseType?: string } | undefined)?.responseType === 'blob'
    return {
      data: { mock: true },
      headers: isBlob ? { 'x-export-filename': encodeURIComponent('结果.xlsx') } : {},
    }
  }
  const http = { get: mk('get'), post: mk('post'), put: mk('put'), delete: mk('delete') }
  return { calls, http }
})

vi.mock('axios', () => ({ default: { create: () => h.http } }))

import * as api from '../api'

beforeEach(() => {
  h.calls.length = 0
})

describe('回测相关（路径里带 task_id 的最容易写错）', () => {
  it('提交回测：POST /backtest', async () => {
    await api.submitBacktest({ foo: 1 } as never)
    expect(h.calls[0]).toMatchObject({ m: 'post', url: '/backtest' })
    expect(h.calls[0].a).toEqual({ foo: 1 })
  })

  it('任务详情/产物/特征/快照/结果/部分结果：GET /backtest/{id}/…', async () => {
    const id = 'T1'
    await api.getBacktestTask(id)
    await api.getBacktestArtifacts(id)
    await api.getBacktestFeatures(id)
    await api.getBacktestSnapshot(id)
    await api.getBacktestResult(id)
    await api.getBacktestPartial(id)
    expect(h.calls.map((c) => c.url)).toEqual([
      '/backtest/T1',
      '/backtest/T1/artifacts',
      '/backtest/T1/features',
      '/backtest/T1/snapshot',
      '/backtest/T1/result',
      '/backtest/T1/partial',
    ])
    expect(h.calls.every((c) => c.m === 'get')).toBe(true)
  })

  it('列表 / 历史 / 容量：都是复数路径（少个 s 就 404）', async () => {
    await api.listBacktests()
    await api.listBacktestsHistory()
    await api.getBacktestCapacity()
    expect(h.calls.map((c) => c.url)).toEqual(['/backtests', '/backtests/history', '/backtest/capacity'])
  })

  it('取消 / 强杀 / 恢复 / 删除', async () => {
    await api.cancelBacktest('T1')
    await api.forceCancelBacktest('T1')
    await api.resumeBacktest('T1')
    await api.deleteBacktest('T1')
    expect(h.calls.map((c) => [c.m, c.url])).toEqual([
      ['post', '/backtest/T1/cancel'],
      ['post', '/backtest/T1/force-cancel'],
      ['post', '/backtest/T1/resume'],
      ['delete', '/backtest/T1'],
    ])
  })

  it('图片 URL 是**纯字符串拼接**（不请求后端 ✓）', () => {
    expect(api.getBacktestImageUrl('T1', 'nav')).toBe('/api/backtest/T1/image/nav')
  })

  it('版本号', async () => {
    await api.getAppVersion()
    expect(h.calls[0]).toMatchObject({ m: 'get', url: '/version' })
  })
})

describe('因子目录与自定义公式', () => {
  it('目录：dataset 走 **query params**（不是 body）', async () => {
    await api.getFactorCatalog('Alpha360')
    expect(h.calls[0].url).toBe('/factors/catalog')
    expect(h.calls[0].a).toEqual({ params: { dataset: 'Alpha360' } })
  })

  it('目录默认 Alpha158', async () => {
    await api.getFactorCatalog()
    expect((h.calls[0].a as { params: { dataset: string } }).params.dataset).toBe('Alpha158')
  })

  it('⭐⭐ 新建：`allow_duplicate` 是**下划线**（拼成驼峰后端会静默忽略重复检查）', async () => {
    await api.createCustomFormula('$close', true)
    expect(h.calls[0]).toMatchObject({ m: 'post', url: '/factors/custom-formulas' })
    expect(h.calls[0].a).toEqual({ formula: '$close', allow_duplicate: true })
  })

  it('更新走 PUT、删除走 DELETE、置顶走 POST …/pin', async () => {
    await api.updateCustomFormula('id1', '$close')
    await api.deleteCustomFormula('id1')
    await api.setFormulaPinned('id1', true)
    expect(h.calls.map((c) => [c.m, c.url])).toEqual([
      ['put', '/factors/custom-formulas/id1'],
      ['delete', '/factors/custom-formulas/id1'],
      ['post', '/factors/custom-formulas/id1/pin'],
    ])
    expect(h.calls[2].a).toEqual({ pinned: true })
  })
})

describe('单因子测试', () => {
  it('提交 / 进度 / 取消 / 清理', async () => {
    await api.createSingleFactorTest({} as never)
    await api.getSingleFactorTestProgress('T2')
    await api.cancelSingleFactorTest('T2')
    await api.clearSingleFactorTest()
    expect(h.calls.map((c) => c.url)).toEqual([
      '/factors/single-factor-test',
      '/factors/single-factor-test/progress/T2',
      '/factors/single-factor-test/cancel/T2',
      '/factors/single-factor-test/clear',
    ])
  })

  it('任务列表：limit 走 query params，不给就是 undefined', async () => {
    await api.getSingleFactorTestTasks(5)
    expect(h.calls[0].a).toEqual({ params: { limit: 5 } })
    await api.getSingleFactorTestTasks()
    expect(h.calls[1].a).toEqual({ params: { limit: undefined } })
  })
})

describe('XLSX 导出（blob + 中文文件名）', () => {
  it('三个导出各走自己的路径，且 responseType=blob', async () => {
    await api.exportTopkCurveXlsx({} as never)
    await api.exportEventStudyXlsx({} as never)
    await api.exportExitCurveXlsx({} as never)
    expect(h.calls.map((c) => c.url)).toEqual([
      '/factors/export/topk-xlsx',
      '/factors/export/event-study-xlsx',
      '/factors/export/exit-curve-xlsx',
    ])
    for (const c of h.calls) {
      expect((c.b as { responseType?: string }).responseType).toBe('blob')
    }
  })

  it('⭐ 文件名从响应头 `x-export-filename` **解码**出来（中文要 encodeURIComponent 过）', async () => {
    const r = await api.exportTopkCurveXlsx({} as never)
    expect(r.filename).toBe('结果.xlsx')
  })
})

describe('blobErrorDetail（导出失败时要能说清原因）', () => {
  it('Blob 里是 JSON ⇒ 取 detail', async () => {
    const blob = new Blob([JSON.stringify({ detail: '后端说：公式不合法' })], { type: 'application/json' })
    expect(await api.blobErrorDetail({ response: { data: blob } })).toBe('后端说：公式不合法')
  })

  it('Blob 里不是 JSON ⇒ 原样截断返回（不能让用户看到 "{} 无法解析"）', async () => {
    const blob = new Blob(['内部错误'], { type: 'text/plain' })
    expect(await api.blobErrorDetail({ response: { data: blob } })).toBe('内部错误')
  })

  it('普通对象带 detail ⇒ 取出来', async () => {
    expect(await api.blobErrorDetail({ response: { data: { detail: 123 } } })).toBe('123')
  })

  it('字符串 ⇒ 截断', async () => {
    expect(await api.blobErrorDetail({ response: { data: 'x'.repeat(500) } })).toHaveLength(300)
  })

  it('什么都没有 ⇒ 给一句**能照做**的兜底（不是空串）', async () => {
    expect(await api.blobErrorDetail(new Error('boom'))).toContain('请查看后端日志')
  })
})

describe('事件研究与数据源', () => {
  it('事件研究：建/进度/取消', async () => {
    await api.createEventStudy({} as never)
    await api.getEventStudyProgress('T3')
    await api.cancelEventStudy('T3')
    expect(h.calls.map((c) => c.url)).toEqual([
      '/factors/event-study',
      '/factors/event-study/progress/T3',
      '/factors/event-study/cancel/T3',
    ])
  })

  it('事件净值：POST …/nav（超时 600s —— 这条路径本来就算得久）', async () => {
    await api.runEventNav({} as never)
    expect(h.calls[0].url).toBe('/factors/event-study/nav')
    expect((h.calls[0].b as { timeout?: number }).timeout).toBe(600000)
  })

  it('数据源 / 数据集 / 切换（`allow_while_running` 也是下划线）', async () => {
    await api.listDataSources()
    await api.getDatasets()
    await api.switchDataset('cn_data2', true)
    expect(h.calls.map((c) => c.url)).toEqual(['/data-sources', '/datasets', '/datasets/active'])
    expect(h.calls[2].a).toEqual({ name: 'cn_data2', allow_while_running: true })
  })
})

describe('交易信号测试', () => {
  it('options / parse（120s）/ run（600s）', async () => {
    await api.getSignalTestOptions()
    await api.parseSignalCsv({} as never)
    await api.runSignalTest({} as never)
    expect(h.calls.map((c) => c.url)).toEqual([
      '/signal-test/options',
      '/signal-test/parse',
      '/signal-test/run',
    ])
    expect((h.calls[1].b as { timeout?: number }).timeout).toBe(120000)
    expect((h.calls[2].b as { timeout?: number }).timeout).toBe(600000)
  })
})
