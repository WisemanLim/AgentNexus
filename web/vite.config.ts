/// <reference types="vitest/config" />
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'

// 개발 서버는 API·IdP·프로토콜 경로를 api 로 프록시 (Host 유지 → IdP redirect_uri 가 5173 origin 으로 검증됨)
const target = process.env.VITE_API_TARGET || 'http://127.0.0.1:8800'
const proxied = ['/api', '/idp', '/v1', '/v0', '/mcp', '/a2a', '/health', '/metrics', '/.well-known', '/openapi.json']

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  build: { assetsDir: '_app', chunkSizeWarningLimit: 2000 },
  esbuild: { legalComments: 'eof' }, // 의존성 라이선스 주석(@license·@preserve·/*!)을 번들 끝에 보존 (D17-③)
  server: { host: '0.0.0.0', port: 5173, proxy: Object.fromEntries(proxied.map((p) => [p, { target }])) },
  test: { globals: true, environment: 'jsdom', setupFiles: ['src/test/setup.ts'], css: false, include: ['src/**/*.test.{ts,tsx}'] },
})
