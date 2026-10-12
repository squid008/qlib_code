/**
 * 把页面上的图**抓成 PNG**（v2.1.0，零新依赖 ✓）。
 *
 * 为什么不用 html2canvas / dom-to-image：
 *   · 本项目所有图都是 **recharts ⇒ 真正的 `<svg>`** ✓ ⇒ 直接把 SVG 序列化 + 画进
 *     `<canvas>` 就能拿到**与页面像素一致**的图 ✓（不需要额外库、不需要网络安装 ✓）；
 *   · 用户 2026-10-11 的诉求就是"我参数选到那档就截图那档"⇒ 导出文件里必须是**他眼前的图** ✓。
 *
 * ⚠ 两个坑（都已处理）：
 *   ① **tailwind 类会丢** —— 克隆出来的 SVG 脱离文档后 `.text-slate-400` 这类类名没有 CSS
 *      作用（字体颜色/大小全变默认 ✗）⇒ 这里逐节点把"画图相关"的计算样式**内联**进克隆体 ✓；
 *   ② **透明底** —— Excel 单元格背景会透出来 ⇒ 先铺白底再画图 ✓。
 */

/** 需要内联的样式（只挑"影响画面"的，避免把无关属性也塞进去 ✓）。 */
const STYLE_KEYS = [
  'fill',
  'fill-opacity',
  'stroke',
  'stroke-width',
  'stroke-opacity',
  'stroke-dasharray',
  'stroke-linecap',
  'font-family',
  'font-size',
  'font-weight',
  'font-style',
  'opacity',
  'text-anchor',
  'dominant-baseline',
] as const

/** 把源节点（含子树）的计算样式内联到克隆节点（**结构一致 ⇒ 按下标一一对应** ✓）。 */
function inlineComputedStyles(src: Element, dst: Element): void {
  const a: Element[] = [src, ...Array.from(src.querySelectorAll('*'))]
  const b: Element[] = [dst, ...Array.from(dst.querySelectorAll('*'))]
  const n = Math.min(a.length, b.length)
  for (let i = 0; i < n; i += 1) {
    const cs = window.getComputedStyle(a[i])
    let css = ''
    for (const k of STYLE_KEYS) {
      const v = cs.getPropertyValue(k)
      if (v) css += `${k}:${v};`
    }
    const prev = b[i].getAttribute('style') || ''
    b[i].setAttribute('style', prev + css)
  }
}

/**
 * `svg` → PNG `Blob`。
 *
 * `scale = 2` ⇒ 2 倍分辨率（贴进 Excel 缩放后仍清晰 ✓；单张约 100~300KB ✓）。
 * 失败一律**抛异常**，由调用方决定"降级导出"还是"报错"（本工具选前者 ✓）。
 */
export async function captureSvgPng(svg: SVGSVGElement, scale = 2): Promise<Blob> {
  const rect = svg.getBoundingClientRect()
  const width = Math.max(1, Math.round(rect.width || svg.clientWidth || 960))
  const height = Math.max(1, Math.round(rect.height || svg.clientHeight || 320))

  const clone = svg.cloneNode(true) as SVGSVGElement
  clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg')
  clone.setAttribute('xmlns:xlink', 'http://www.w3.org/1999/xlink')
  clone.setAttribute('width', String(width))
  clone.setAttribute('height', String(height))
  if (!clone.getAttribute('viewBox')) clone.setAttribute('viewBox', `0 0 ${width} ${height}`)
  inlineComputedStyles(svg, clone)

  const xml = new XMLSerializer().serializeToString(clone)
  const url = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(xml)}`

  const img = new Image()
  await new Promise<void>((resolve, reject) => {
    img.onload = () => resolve()
    img.onerror = () => reject(new Error('SVG 转图片失败（浏览器未加载成功）'))
    img.src = url
  })

  const canvas = document.createElement('canvas')
  canvas.width = Math.round(width * scale)
  canvas.height = Math.round(height * scale)
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error('无法创建画布（canvas 2d 不可用）')
  ctx.fillStyle = '#ffffff' // 白底 ⇒ Excel 里不透底 ✓
  ctx.fillRect(0, 0, canvas.width, canvas.height)
  ctx.drawImage(img, 0, 0, canvas.width, canvas.height)

  return await new Promise<Blob>((resolve, reject) => {
    canvas.toBlob((b) => (b ? resolve(b) : reject(new Error('PNG 编码失败'))), 'image/png')
  })
}

/** Blob → base64（**不含** `data:` 前缀 —— 后端按纯 base64 解 ✓）。 */
export async function blobToBase64(blob: Blob): Promise<string> {
  const buf = new Uint8Array(await blob.arrayBuffer())
  let bin = ''
  const CHUNK = 0x8000 // 分块拼接：一次性 `String.fromCharCode(...)` 在大图下会爆栈 ✗
  for (let i = 0; i < buf.length; i += CHUNK) {
    bin += String.fromCharCode(...buf.subarray(i, i + CHUNK))
  }
  return btoa(bin)
}

/** 容器里第一张图 → base64 PNG（容器里没有 `<svg>` ⇒ 返回 null ✓，调用方照常导出 ✓）。 */
export async function captureChartIn(
  container: HTMLElement | null,
  scale = 2,
): Promise<string | null> {
  const svg = container?.querySelector('svg')
  if (!svg) return null
  return await blobToBase64(await captureSvgPng(svg as SVGSVGElement, scale))
}

/**
 * 把**整个区块**（标题 + 图 + 图例 + 数值行）截成 PNG —— v2.1.1 起的主路径 ✓。
 *
 * ★ 为什么必须换掉"只抓 `<svg>`"（用户 2026-10-11 实测指出 ✓）：
 *   · **recharts 的 `<Legend>` 是 HTML `<div>`（不是 SVG 元素）** ✗ ⇒ 只抓 SVG 必然丢图例，
 *     用户："你看XLSX里图例、标题都没有" ✓；
 *   · 图上的标注（如「最强 Q2」）也常挂在 SVG **容器外层** ✗ ⇒ 一并丢 ⇒ "不知道看的是哪档" ✓；
 *   · 图下方的收益/回撤统计行同理 ✗。
 *   ⇒ 用 `html2canvas` 对**区块元素**做栅格化 ⇒ 所见即所得 ✓（代价：多一个 1.4MB 的依赖 ✓，
 *     且走**动态 import** ⇒ 只有点导出时才加载 ✓）。
 *
 * ⚠ 两个实测坑：
 *   ① 元素在**弹窗内滚动区**里 ⇒ 先 `scrollIntoView` 让浏览器真正布局它，截完**恢复原滚动位置** ✓；
 *   ② html2canvas 失败（老浏览器/CSS 太新）⇒ 退回"只抓 SVG"（至少还有图 ✓），
 *      由调用方在文件说明里体现"图例可能缺失" ✓（绝不静默出一张白图 ✗）。
 */
/** 画布尺寸上限（浏览器 canvas 有硬上限：单边 ~16k px、总面积亦有上限 ✗）。 */
const MAX_SIDE = 8000
const MAX_AREA = 40_000_000

/** 按元素实际尺寸把缩放**压到安全范围**（否则 `toBlob` 会**返回 null** ⇒ "PNG 编码失败" ✗）。 */
function fitScale(el: HTMLElement, want: number): number {
  const w = Math.max(1, el.scrollWidth || el.clientWidth || 1)
  const h = Math.max(1, el.scrollHeight || el.clientHeight || 1)
  const s = Math.min(want, MAX_SIDE / w, MAX_SIDE / h, Math.sqrt(MAX_AREA / (w * h)))
  return Math.max(0.5, Math.min(s, want))
}

export async function captureElementPng(el: HTMLElement, scale = 2): Promise<Blob> {
  const mod = (await import('html2canvas')) as unknown as {
    default: (e: HTMLElement, o?: Record<string, unknown>) => Promise<HTMLCanvasElement>
  }
  const useScale = fitScale(el, scale)
  const canvas = await mod.default(el, {
    backgroundColor: '#ffffff',           // 白底（Excel 里不透底 ✓）
    scale: useScale,
    logging: false,
    useCORS: true,
    scrollX: 0,
    scrollY: 0,
    windowWidth: Math.max(document.documentElement.clientWidth, el.scrollWidth),
    windowHeight: Math.max(document.documentElement.clientHeight, el.scrollHeight),
  })
  const px = `${canvas.width}×${canvas.height}`
  return await new Promise<Blob>((resolve, reject) => {
    canvas.toBlob(
      (b) => (b ? resolve(b) : reject(new Error(`PNG 编码失败（画布 ${px}，可能超出浏览器上限）`))),
      'image/png',
    )
  })
}

/** 区块 → base64 PNG（主路径 html2canvas ✓，失败退回只抓 SVG ✓，都没有 ⇒ null ✓）。 */
export async function captureSection(
  container: HTMLElement | null,
  scale = 2,
): Promise<string | null> {
  if (!container) return null
  const scroller = container.closest('[class*="overflow"]') as HTMLElement | null
  const keepTop = scroller?.scrollTop ?? 0
  try {
    container.scrollIntoView({ block: 'nearest' })
    return await blobToBase64(await captureElementPng(container, scale))
  } catch (e) {
    console.warn('整块截图失败（html2canvas），退回只截 SVG：', e)
    try {
      return await captureChartIn(container, scale)
    } catch {
      return null
    }
  } finally {
    if (scroller) scroller.scrollTop = keepTop      // 恢复用户原来的滚动位置 ✓（别把弹窗跳走 ✗）
  }
}
