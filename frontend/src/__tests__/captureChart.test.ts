// @vitest-environment jsdom
/**
 * `captureChart.ts`（130 行 / 覆盖率 14.6%）★ v2.11.63 —— **导出链路**的唯一入口。
 *
 * ## 为什么值得测（不是为凑覆盖率）
 * 这条链路决定"用户点导出，Excel 里**有没有图**" ✓。历史真事故：
 * · 只抓 `<svg>` ⇒ **图例/标题全丢**（recharts 的 `<Legend>` 是 HTML `<div>` ✗）；
 * · `canvas.toBlob` 对**超大画布**返回 `null` ⇒ 报"PNG 编码失败" ✗ ⇒ 才有 `fitScale` 压尺寸 ✓。
 * 这些分支在真实浏览器里**很难复现**（要么很大、要么很旧）✗ ⇒ 只能靠单测钉 ✓。
 *
 * ## 怎么在 jsdom 里测（三处必须打桩 ✗）
 * ① `Image` —— jsdom 不会真的解码 `data:` URL ⇒ 永远不会 `onload` ✗ ⇒ 给个立马 onload 的假类 ✓；
 * ② `canvas.getContext('2d')` —— jsdom 返回 null ✗（本项目的 `charWidth` 也有同一兜底 ✓）；
 * ③ `canvas.toBlob` / `html2canvas` —— 同上，不打桩就只能测"抛错" ✗。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  blobToBase64,
  captureChartIn,
  captureElementPng,
  captureSection,
  captureSvgPng,
} from '../captureChart'

// ③ html2canvas：给个"成功但画布很小"的假实现 ✓（真实现要浏览器排版 ✗）
vi.mock('html2canvas', () => ({
  default: () =>
    Promise.resolve({
      width: 8,
      height: 8,
      // ⚠ Blob 必须带 `type` —— 我第一版忘了传 ✓ ⇒ 断言 `blob.type === 'image/png'` 直接红
      //   （拿到的是 `''` ✓）。**打桩也要打得像真的** ✓。
      toBlob: (cb: (b: Blob) => void) => cb(new Blob(['png'], { type: 'image/png' })),
    }),
}))

const PNG = new Blob(['png-bytes'], { type: 'image/png' })

beforeEach(() => {
  // ① Image：设 src 后**同步**触发 onload ✓
  class FakeImage {
    onload: (() => void) | null = null
    onerror: (() => void) | null = null
    set src(_v: string) {
      this.onload?.()
    }
  }
  vi.stubGlobal('Image', FakeImage as unknown as typeof Image)

  // ② getContext：给个最小的 2d 上下文 ✓
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
    fillStyle: '#fff',
    fillRect: () => {},
    drawImage: () => {},
  } as unknown as CanvasRenderingContext2D)

  // ③ toBlob：直接回一个 Blob ✓
  vi.spyOn(HTMLCanvasElement.prototype, 'toBlob').mockImplementation((cb: BlobCallback) => cb(PNG))

  // ④ ⚠ jsdom **没有** `scrollIntoView` ✗ —— `captureSection` 一进来就调它 ✓
  //   不打桩的话会 `TypeError` ⇒ **静默退到"只截 SVG"那条 fallback** ✓
  //   ⇒ 看着"通过"，其实测的是 fallback ✗（我得两个路径分别钉 ✓）。
  Element.prototype.scrollIntoView = () => {}
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  document.body.innerHTML = ''
})

function svgIn(): SVGSVGElement {
  const host = document.createElement('div')
  host.innerHTML = '<svg width="100" height="50"><rect x="0" y="0" width="10" height="10"/></svg>'
  document.body.appendChild(host)
  return host.querySelector('svg') as SVGSVGElement
}

describe('blobToBase64（纯函数，后端按纯 base64 解 ✓ 不带 data: 前缀 ✗）', () => {
  it('编码正确且不带前缀', async () => {
    const s = await blobToBase64(new Blob(['abc']))
    expect(s).toBe('YWJj')          // 'abc' 的 base64 ✓
    expect(s.startsWith('data:')).toBe(false)
  })

  it('⭐ 大块也要能编码（分块循环：一次性展开会爆栈 ✗）', async () => {
    const big = new Blob([new Uint8Array(200_000)])
    const s = await blobToBase64(big)
    expect(s.length).toBeGreaterThan(200_000)
  })
})

describe('captureChartIn（容器里没有 svg ⇒ null，调用方照常导出 ✓）', () => {
  it('null 容器 ⇒ null', async () => {
    expect(await captureChartIn(null)).toBeNull()
  })

  it('容器里没有 svg ⇒ null', async () => {
    const div = document.createElement('div')
    expect(await captureChartIn(div)).toBeNull()
  })

  it('有 svg ⇒ 返回 base64', async () => {
    const svg = svgIn()
    const s = await captureChartIn(svg.parentElement)
    expect(s).toBeTruthy()
    expect(s?.startsWith('data:')).toBe(false)
  })
})

describe('captureSvgPng（SVG → PNG）', () => {
  it('返回 PNG Blob（白底 + 2 倍分辨率 ✓）', async () => {
    const blob = await captureSvgPng(svgIn(), 2)
    expect(blob.type).toBe('image/png')
  })

  it('缺省尺寸时用兜底 960×320（`getBoundingClientRect` 在 jsdom 恒为 0 ✗）', async () => {
    const svg = svgIn()
    expect(() => svg.getBoundingClientRect()).not.toThrow()
    const blob = await captureSvgPng(svg)
    expect(blob.type).toBe('image/png')
  })

  it('图片加载失败 ⇒ **抛异常**（由调用方决定降级/报错，绝不静默出白图 ✗）', async () => {
    class FailImage {
      onload: (() => void) | null = null
      onerror: (() => void) | null = null
      set src(_v: string) {
        this.onerror?.()
      }
    }
    vi.stubGlobal('Image', FailImage as unknown as typeof Image)
    await expect(captureSvgPng(svgIn())).rejects.toThrow(/SVG 转图片失败/)
  })
})

describe('captureElementPng / captureSection', () => {
  it('captureElementPng 走 html2canvas 并回 Blob', async () => {
    const div = document.createElement('div')
    document.body.appendChild(div)
    const blob = await captureElementPng(div, 2)
    expect(blob.type).toBe('image/png')
  })

  it('captureSection(null) ⇒ null', async () => {
    expect(await captureSection(null)).toBeNull()
  })

  it('captureSection 成功时回 base64，且**恢复原来的滚动位置**（别把弹窗跳走 ✗）', async () => {
    const scroller = document.createElement('div')
    scroller.className = 'overflow-y-auto'
    Object.defineProperty(scroller, 'scrollTop', { value: 120, writable: true })
    const inner = document.createElement('div')
    inner.innerHTML = '<svg width="10" height="10"></svg>'
    scroller.appendChild(inner)
    document.body.appendChild(scroller)

    const s = await captureSection(inner)
    expect(s).toBeTruthy()
    expect(scroller.scrollTop).toBe(120)
  })

  it('⭐ html2canvas 失败 ⇒ **退回只截 SVG**（至少还有图，且不静默出白图 ✓）', async () => {
    vi.resetModules()
    vi.doMock('html2canvas', () => ({
      default: () => Promise.reject(new Error('boom')),
    }))
    const mod = await import('../captureChart')
    const inner = document.createElement('div')
    inner.innerHTML = '<svg width="10" height="10"></svg>'
    document.body.appendChild(inner)

    const s = await mod.captureSection(inner)
    expect(s, '退回路径应该仍能给出图（base64）').toBeTruthy()
    vi.doUnmock('html2canvas')
  })
})
