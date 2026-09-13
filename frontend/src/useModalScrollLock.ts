import { useEffect } from 'react'

/**
 * 弹窗打开期间**锁定页面滚动**（v1.19.18）。
 *
 * 修的 bug（用户报）：打开「事件研究」/「持仓曲线」弹窗后，滚轮滚到弹窗内容底部，**继续滚就
 * 会带动后面的主页面滚动**（scroll chaining / 滚动穿透），很影响体验。
 *
 * 为什么不能只给弹窗加 `overscroll-contain`（`overscroll-behavior: contain`）：
 *   它只在**指针位于可滚动容器内部**时生效。指针落在遮罩上、或弹窗内容没超出（没有内部滚动条）
 *   时，滚轮事件仍会被页面接走 ⇒ 锁 `body` 才是彻底方案。两者一起加（防御性：万一别处改回
 *   body 的 overflow，`overscroll-contain` 还能兜住）。
 *
 * 细节：
 * - 关闭时按**打开前的原值**还原（不写死 `''`），避免覆盖别处/其他弹窗对 body 的样式设置；
 * - 补偿滚动条宽度（`innerWidth - documentElement.clientWidth`），否则隐藏滚动条会让整页
 *   横向抖动一下；
 * - ⚠ 两个弹窗同时打开时，先关的那个会提前解锁（本工具里弹窗不会互相嵌套，暂不处理）。
 */
export function useModalScrollLock(open: boolean) {
  useEffect(() => {
    if (!open || typeof document === 'undefined') return
    const body = document.body
    const prevOverflow = body.style.overflow
    const prevPadRight = body.style.paddingRight
    const scrollbarW = window.innerWidth - document.documentElement.clientWidth
    body.style.overflow = 'hidden'
    if (scrollbarW > 0) body.style.paddingRight = `${scrollbarW}px`
    return () => {
      body.style.overflow = prevOverflow
      body.style.paddingRight = prevPadRight
    }
  }, [open])
}
