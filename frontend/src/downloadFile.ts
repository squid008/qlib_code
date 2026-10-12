/**
 * 文件下载小工具（v2.1.0）。
 *
 * 为什么单独一个模块：导出按钮会出现在多处（持仓曲线 / 事件研究 / 交易信号测试 ✓），
 * 而"Blob + createObjectURL + a.download + revoke"这套样板在本项目里已被内联过好几遍 ✗
 * （CSV 导出、模型权重下载…）。这里只做一件事：**把二进制落地成文件**，并保证回收 URL ✓。
 */

/** 把 blob 存成文件（`filename` 建议带扩展名 ✓）。 */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  try {
    const a = document.createElement('a')
    a.href = url
    a.download = filename || 'download'
    a.rel = 'noopener'
    document.body.appendChild(a)
    a.click()
    a.remove()
  } finally {
    // ⚠ 立刻 revoke 会让部分浏览器**取消下载** ✗ ⇒ 延后一拍再回收（内存与稳定性兼顾 ✓）
    window.setTimeout(() => URL.revokeObjectURL(url), 10_000)
  }
}

/** 时间戳后缀（文件名用）：`20261011_1530` ✓。 */
export function stamp(d = new Date()): string {
  const p = (n: number) => String(n).padStart(2, '0')
  return (
    `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}` +
    `_${p(d.getHours())}${p(d.getMinutes())}`
  )
}
