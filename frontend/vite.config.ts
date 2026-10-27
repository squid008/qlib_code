import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Vite 开发服务器配置
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // ★ v2.11.99：**显式绑 IPv4** ✗→✓（2026-10-27 实测）
    //   不写 `host` 时 vite 只监听 **`::1`**（IPv6 only）⇒
    //   `http://127.0.0.1:5173/` **连不上**（`localhost` 能通 ✓）✗
    //   ⇒ IDE 内置预览 / 一些只走 IPv4 的客户端会白屏 ✓。
    //   写死 IPv4 与下面 proxy 的 `target: 127.0.0.1:8001` 口径一致 ✓。
    host: '127.0.0.1',
    proxy: {
      // 将 /api 请求代理到 FastAPI 后端
      // ★ v2.11.50：目标用 **`127.0.0.1`** 而不是 `localhost` ✓ ——
      //   后端现在默认只绑回环地址（`scripts/restart_backend.ps1` ✓），
      //   而 `localhost` 在 Windows 上可能**先解析到 `::1`** ⇒ 后端只监听 IPv4 时打不通 ✗。
      //   写死 IPv4 两边才对齐 ✓。
      '/api': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
    },
  },
})
