// @vitest-environment jsdom
/**
 * `ModelParamsForm` 的**交互级**测试（★ 2026-10-19 ✓）—— 本仓库第一批"真 DOM"用例 ✓。
 *
 * ## 为什么不用 `@testing-library/react`
 *
 * 它是 v16 ✓，但 `@testing-library/dom` 是它的 **peer 依赖** ✗，而本仓库的 lockfile 是带
 * `--legacy-peer-deps` 装的 ✓ ⇒ 那个包**没被装进来** ✓
 * （实测：`Cannot find module '@testing-library/dom'` ✓，整个文件 0 个用例被收集 ✗）。
 * ⇒ 为了**不再往用户机器上加安装步骤** ✗，这里只用 **`react-dom/client` + 原生事件** ✓：
 *   `createRoot` 渲染 ✓、`act` 包住 ✓、改值用 **`HTMLInputElement.prototype` 的原生 setter** ✓
 *   （React 只听 `input` 事件 ✓，直接赋 `el.value` 它收不到 ✗ —— 这是最经典的坑 ✓）。
 *
 * ## 守什么（都是真实会犯的错 ✓）
 *
 * 1. **字段集合按模型走** ✓：LightGBM 有 `num_leaves` ✓、XGBoost **没有**（用 `min_child_weight` ✓）
 *    —— 传错参数会让训练报错或被静默忽略 ✗；
 * 2. **输入真的回调** ✓：`onUpdate(key, 原始字符串)` ✓（空串要能表达"用默认值"✓，转数字是调用方的事 ✓）；
 * 3. **值回填** ✓：`modelParams` 里已有的值必须出现在输入框里 ✓（否则"复用历史参数"是假的 ✗）；
 * 4. **未知模型返回 null** ✓（Linear 无树参数 ✓ —— 绝不能渲染一排空输入框 ✗）。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot } from 'react-dom/client'

import ModelParamsForm from '../components/ModelParamsForm'

const mounted: Array<() => void> = []

afterEach(() => {
  while (mounted.length) {
    try {
      mounted.pop()!()
    } catch {
      /* 清理失败不影响判定 ✓ */
    }
  }
  document.body.innerHTML = ''
})

function renderInto(node: React.ReactElement) {
  const el = document.createElement('div')
  document.body.appendChild(el)
  const root = createRoot(el)
  act(() => {
    root.render(node)
  })
  mounted.push(() => {
    act(() => root.unmount())
    el.remove()
  })
  return el
}

function inputs(el: HTMLElement): HTMLInputElement[] {
  return Array.from(el.querySelectorAll('input')) as HTMLInputElement[]
}

/** 每个输入框**自己的标签文案** ✓（⚠ 不能拿整段 `textContent` 判 ✗ ——
 *  底部提示语里本来就会提到"无 num_leaves"✓，实测就是这么误报的 ✓）。 */
function labels(el: HTMLElement): string[] {
  return inputs(el).map((i) => (i.closest('label')?.textContent || '').trim())
}

/** 触发 React 的 onChange ✓（必须走原生 setter + input 事件 ✓）。 */
function type(el: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype, 'value')!.set!
  act(() => {
    setter.call(el, value)
    el.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

const noop = () => {}

describe('ModelParamsForm（jsdom 交互）', () => {
  it('LightGBM：10 个数字输入，标签含 num_leaves、不含 min_child_weight', () => {
    const el = renderInto(<ModelParamsForm model="lightgbm" modelParams={{}} onUpdate={noop} />)
    const ins = inputs(el)
    expect(ins).toHaveLength(10)
    expect(ins.every((i) => i.type === 'number')).toBe(true)
    const ls = labels(el).join(' | ')
    expect(ls).toContain('num_leaves')
    expect(ls).not.toContain('min_child_weight')
    expect(el.textContent).toContain('留空 = lightgbm 默认值')
  })

  it('XGBoost：字段集与 LightGBM **不同**（标签有 min_child_weight、无 num_leaves）', () => {
    const el = renderInto(<ModelParamsForm model="xgboost" modelParams={{}} onUpdate={noop} />)
    expect(inputs(el)).toHaveLength(10)
    const ls = labels(el).join(' | ')
    expect(ls).toContain('min_child_weight')
    expect(ls).not.toContain('num_leaves')
  })

  it('改学习率 ⇒ onUpdate("learning_rate", "0.05")（原始字符串，不擅自转数字）', () => {
    const onUpdate = vi.fn()
    const el = renderInto(<ModelParamsForm model="lightgbm" modelParams={{}} onUpdate={onUpdate} />)
    type(inputs(el)[4], '0.05')                 // 第 5 个 = learning_rate ✓
    expect(onUpdate).toHaveBeenCalledTimes(1)
    expect(onUpdate).toHaveBeenCalledWith('learning_rate', '0.05')
  })

  it('清空输入也必须回调（空串 = 用默认值，不能被吞 ✗）', () => {
    const onUpdate = vi.fn()
    const el = renderInto(
      <ModelParamsForm model="lightgbm" modelParams={{ seed: 42 }} onUpdate={onUpdate} />)
    const seed = inputs(el)[0]
    expect(seed.value).toBe('42')               // 回填 ✓
    type(seed, '')
    expect(onUpdate).toHaveBeenCalledWith('seed', '')
  })

  it('未知模型（linear）⇒ 什么都不渲染', () => {
    const el = renderInto(<ModelParamsForm model="linear" modelParams={null} onUpdate={noop} />)
    expect(el.innerHTML).toBe('')
  })

  it('modelParams 为 null 也不炸，placeholder 仍在（提示默认值 ✓）', () => {
    const el = renderInto(<ModelParamsForm model="lightgbm" modelParams={null} onUpdate={noop} />)
    const ins = inputs(el)
    expect(ins).toHaveLength(10)
    expect(ins[0].value).toBe('')
    expect(ins[0].placeholder).toContain('默认 0')
  })
})
