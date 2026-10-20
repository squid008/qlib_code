// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'

/**
 * `EventStudyModal` 的**骨架**测试（★ v2.10.31，2026-10-20 ✓）。
 *
 * ## 这一轮钉的是什么（**不需要造 `EventStudyResult`** ✓）
 *
 * 那 1512 行里最深的一坨是"有 `data` 时的图表/榜单渲染"✗ —— 它依赖 `EventStudyResult`
 * 的**四层嵌套**（`curve[]` / `prob[]` / `upside` / 按 k 分的榜单 ✓），
 * 凭猜造夹具必然红一片 ✓（`TopkCurveModal` 那次差点把 `kind` 猜错 ✓，是读了 `api.ts` 才救回来 ✓）。
 *
 * 所以本轮只钉**不依赖结果形状**、但有真实业务后果的两条 ✓：
 *
 * ① `open=false` ⇒ **不占 DOM** ✓；
 * ② **`req=null` 时打开弹窗 ⇒ 绝对不能提交任务** ✓✓ ——
 *    组件注释写的是「参数快照…**open 时提交一次**」✓，而 `req` 可为 `null`（面板没准备好 ✓）
 *    ⇒ 若这里没判空，用户**每开一次弹窗就在后端跑一个事件研究任务** ✗
 *    （慢、占并发、还会在任务列表里堆一坨 ✗）。
 *    这条用 `vi.mock('../api')` 的 spy 直接验 ✓ —— **造夹具不需要知道结果长什么样** ✓。
 * ③ `open=true` + `req=null` + `data=null` ⇒ **不抛异常**（空态也不能白屏崩 ✗）。
 */
// ⚠ `vi.mock` 的工厂会被**提升到文件顶部**执行 ✗ ⇒ 不能直接引用外部 `const` ✓
//   （会报 `Cannot access 'spies' before initialization` ✓ —— 本轮实测踩到 ✓）
//   ⇒ 必须用 `vi.hoisted` 把它一起提上去 ✓。
const spies = vi.hoisted(() => ({
  createEventStudy: vi.fn(async () => ({ task_id: 'T1' })),
  getEventStudyProgress: vi.fn(async () => ({ status: 'success', progress: 100, result: null })),
  cancelEventStudy: vi.fn(async () => ({ status: 'ok' })),
  runEventNav: vi.fn(async () => ({})),
  exportEventStudyXlsx: vi.fn(async () => ({ blob: new Blob(), filename: 'x.xlsx' })),
  blobErrorDetail: vi.fn(async () => 'err'),
}))

vi.mock('../api', () => spies)

import EventStudyModal from '../components/EventStudyModal'

let root: Root | null = null
let host: HTMLDivElement | null = null

function mount(ui: React.ReactElement) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  act(() => root!.render(ui))
  return host
}

beforeEach(() => {
  Object.values(spies).forEach((s) => s.mockClear())
})

afterEach(() => {
  act(() => root?.unmount())
  host?.remove()
  root = null
  host = null
})

describe('EventStudyModal（骨架）', () => {
  it('`open=false` ⇒ 什么都不渲染', () => {
    const h = mount(
      <EventStudyModal open={false} onClose={() => {}} factorName="f" req={null} />,
    )
    expect(h.innerHTML).toBe('')
  })

  it('⭐⭐ `req=null` 时打开 ⇒ **绝不提交任务**（否则每开一次弹窗就在后端跑一个任务 ✗）', () => {
    mount(<EventStudyModal open onClose={() => {}} factorName="f" req={null} data={null} />)
    expect(spies.createEventStudy).not.toHaveBeenCalled()
  })

  it('⭐⭐ 有 `data` 时打开 ⇒ 也**不提交任务**（秒开路径：直接用面板算好的结果 ✓）', () => {
    // 这里**不需要** data 形状正确 ✓ —— 断言的是"有没有发请求" ✓
    mount(
      <EventStudyModal
        open
        onClose={() => {}}
        factorName="f"
        req={null}
        data={{ not: 'a real result' } as never}
      />,
    )
    expect(spies.createEventStudy).not.toHaveBeenCalled()
  })

  it('`open=true` 且无数据 ⇒ 不抛（空态不能白屏崩 ✗）', () => {
    expect(() => {
      mount(<EventStudyModal open onClose={() => {}} factorName="f" req={null} data={null} />)
    }).not.toThrow()
  })
})

/**
 * ⚠ **留给下一轮的硬骨头**（写在这里，别丢 ✓）：
 *
 * `defaultK`（v1.18.63 ✓）—— 注释写明用户报过「点『周期 3』那一行，弹窗却默认显示 7 日口径」✗
 * ⇒ 断言应该是：传 `defaultK=3`（而 `params.max_k=7`）时，**默认展示的是 3 日口径** ✓。
 * 这需要一份**完整合法**的 `EventStudyResult`（`curve/prob/upside/榜单` 四层 ✓）——
 * 得先把这几个子类型从 `api.ts` 逐个读准 ✓，属**专门一轮** ✓（不猜 ✗）。
 */
