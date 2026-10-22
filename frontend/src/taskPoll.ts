/**
 * 任务轮询的**纯逻辑**（★ v2.11.10 从 `App.tsx` 抽出）。
 *
 * ## 为什么抽
 * 轮询写在 `App.tsx` 的两个 `setInterval` 里 ✗（1716 行的文件 + 全 App 状态）⇒
 * **一行都测不了** ✓。而这里恰好有一个**真实的线上坑**（v1.19.10 ✓），
 * 它值得被单独钉住 ✓ —— 抽出来以后既能单测 ✓，也让 `App.tsx` 少 20 行 ✓。
 *
 * ## ⭐⭐ 那个坑：404 不能"静默保留旧任务"
 * 后端重启会**清空内存任务表** ✓ ⇒ 轮询拿到 **404**。
 * 原写法直接 `return t` ✗ ⇒ 卡片**永远冻结在最后一帧进度**：
 * 用户看到"初始化 Qlib 3% 特别慢" ✓ —— 其实是任务早被后端重启杀掉、
 * 而轮询把这个 404 **静默吞掉**了 ✓（真因 ✓）。
 *
 * ⇒ 现在：404 ⇒ 标记 `cancelled` 并**写清原因** ✓
 * （文案里点明"产物文件仍在磁盘，可在「历史」查看" ✓ —— 否则用户以为白跑了 ✗）；
 * 其它错误 ⇒ **保持原样** ✓（网络抖动不该把在跑的任务标成中止 ✗，
 * 下一轮轮询会自愈 ✓）。
 */

// ⚠ `BacktestTask` 在 **`./types`** ✗→✓ —— `api.ts` 只是 `import` 它（没 re-export ✓），
//   写成 `from './api'` 会 `TS2724: has no exported member` ✓（tsc 抓到 ✓，vitest 不查类型 ✗）。
import type { BacktestTask } from './types'

/** 终态：到了这三种状态就不再轮询它 ✓（`cancelling` **不算**终态 ✗ —— 后端还在收尾 ✓）。 */
export function isTerminal(status: string): boolean {
  return status === 'success' || status === 'failed' || status === 'cancelled'
}

/** 404 时替换的文案（单独导出，测试与 UI 用同一份 ✓，避免两处文案漂移 ✗）。 */
export const TASK_LOST_MESSAGE =
  '任务已中止：后端服务重启后任务丢失（产物文件仍在磁盘，可在「历史」查看）'

/**
 * 刷新**一个**任务的进度 ⇒ 新的任务对象（不修改入参 ✓）。
 *
 * · 已是终态 ⇒ 原样返回 ✓（省一次请求 ✓）；
 * · **404** ⇒ 标成 `cancelled` + 写清原因 ✓（见文件头 ✓）；
 * · 其它错误 ⇒ 原样返回 ✓（网络抖动下一轮自愈 ✓）。
 */
export async function pollOneTask(
  t: BacktestTask,
  getTask: (id: string) => Promise<BacktestTask>,
): Promise<BacktestTask> {
  if (isTerminal(t.status)) return t
  try {
    return await getTask(t.task_id)
  } catch (e) {
    const code = (e as { response?: { status?: number } })?.response?.status
    if (code === 404) {
      return { ...t, status: 'cancelled', message: TASK_LOST_MESSAGE }
    }
    return t
  }
}
