import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Vite 开发服务器配置
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
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
