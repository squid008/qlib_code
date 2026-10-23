import axios from 'axios'

/**
 * 后端 API 的**唯一** axios 实例（★ v2.11.58 从 `api.ts` 抽出）。
 *
 * 为什么单独一个文件：`api.ts` 拆成多块后，每块都要这个实例 ✓ ——
 * 各建一个会让**超时 / 拦截器 / baseURL** 三处漂 ✓（真实事故形态：只有某一块改过超时 ✓）。
 * ⚠ 这个文件**不许** import 任何其它 api 模块 ✓（否则成环 ✗）。
 */
const http = axios.create({
  baseURL: '/api',
  timeout: 30000,
})

export default http
