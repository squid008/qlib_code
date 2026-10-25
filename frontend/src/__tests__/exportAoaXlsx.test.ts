/**
 * `api/exportXlsx.exportAoaXlsx`（★ v2.11.80）—— v2.11.68 新增的接口函数，**一条测试都没有** ✗。
 *
 * ## 它为什么重要
 * 前端**唯一**的 Excel 出口（xlsx 依赖已删 ⇒ 一切导出走后端 openpyxl ✓）。
 * 这条坏掉不会有任何编译错误 ✓，只表现为"点了导出没反应/文件名乱码"✗：
 * · 路径写错 ⇒ 404（但代码看着完全正常 ✓）；
 * · 忘了 `responseType: 'blob'` ⇒ 拿回的是 **JSON 文本** ⇒ `saveBlob` 存出个坏文件 ✗；
 * · `x-export-filename` 是 **URL 编码**的中文 ⇒ 不解码就是 `%E5%8D%95...xlsx` ✗；
 * · 解码失败（后端给了裸中文）⇒ 必须**回落原文**，不能抛 ✗。
 */
import { describe, expect, it, vi } from 'vitest'

const h = vi.hoisted(() => ({ post: vi.fn() }))

vi.mock('axios', () => ({
  default: {
    create: () => ({ get: vi.fn(), post: h.post, put: vi.fn(), delete: vi.fn() }),
  },
}))

import { exportAoaXlsx } from '../api/exportXlsx'

const PAYLOAD = {
  filename: '单因子测试结果.xlsx',
  sheets: [
    { name: '因子指标', rows: [['因子', 'IC'], ['mom20', 0.031]] },
    { name: '因子与公式', rows: [['因子', '公式'], ['mom20', 'Mean($close,20)']] },
  ],
}

describe('exportAoaXlsx', () => {
  it('打到 `/factors/export/aoa-xlsx`，且**必须** responseType=blob', async () => {
    const blob = new Blob(['PK'], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' })
    h.post.mockResolvedValue({ data: blob, headers: {} })

    const r = await exportAoaXlsx(PAYLOAD)

    const [path, body, cfg] = h.post.mock.calls[0]
    expect(path).toBe('/factors/export/aoa-xlsx')
    expect(body).toEqual(PAYLOAD)
    // ⚠ 少了这行 ⇒ axios 当文本解析 ⇒ 存出来是个坏文件 ✗
    expect((cfg as { responseType?: string }).responseType).toBe('blob')
    expect(r.blob).toBe(blob)
  })

  it('解码 `X-Export-Filename`（后端给的是 URL 编码的中文）', async () => {
    h.post.mockResolvedValue({
      data: new Blob(['PK']),
      headers: { 'x-export-filename': encodeURIComponent('单因子测试结果_20260101.xlsx') },
    })
    const r = await exportAoaXlsx(PAYLOAD)
    expect(r.filename).toBe('单因子测试结果_20260101.xlsx')
  })

  it('没有该响应头 ⇒ filename = null（调用方用自己的兜底名 ✓）', async () => {
    h.post.mockResolvedValue({ data: new Blob(['PK']), headers: {} })
    expect((await exportAoaXlsx(PAYLOAD)).filename).toBeNull()
  })

  it('⭐ 解码失败（后端给了裸中文）⇒ **回落原文**，不能抛 ✗', async () => {
    h.post.mockResolvedValue({
      data: new Blob(['PK']),
      headers: { 'x-export-filename': '裸中文名.xlsx' },
    })
    expect((await exportAoaXlsx(PAYLOAD)).filename).toBe('裸中文名.xlsx')
  })

  it('空字符串响应头 ⇒ null（不是空串 ✓）', async () => {
    h.post.mockResolvedValue({ data: new Blob(['PK']), headers: { 'x-export-filename': '' } })
    expect((await exportAoaXlsx(PAYLOAD)).filename).toBeNull()
  })

  it('网络错误向上抛（让调用方 setError 出可读提示 ✓，不能静默吞 ✗）', async () => {
    h.post.mockRejectedValue(new Error('boom'))
    await expect(exportAoaXlsx(PAYLOAD)).rejects.toThrow('boom')
  })
})
