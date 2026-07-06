import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { VitePWA } from 'vite-plugin-pwa'

// React Compiler experiment (Phase 3, Task 10): OFF by default. Build with
// `REACT_COMPILER=1 npm run build` (or `vite dev`) to try the babel
// auto-memoization transform. This file runs in Node at config-load time, so
// reading process.env here is safe and doesn't leak into client code. See
// frontend section of the repo README for the experiment's results.
const reactCompilerEnabled = process.env.REACT_COMPILER === '1'

export default defineConfig({
  plugins: [
    react({
      babel: reactCompilerEnabled
        ? { plugins: [['babel-plugin-react-compiler', {}]] }
        : undefined,
    }),
    tailwindcss(),
    VitePWA({
      registerType: 'autoUpdate',
      manifest: {
        name: 'Papyrus',
        short_name: 'Papyrus',
        description: 'Print and scan server',
        theme_color: '#3B82F6',
        background_color: '#F9FAFB',
        display: 'standalone',
        start_url: '/',
        icons: [
          { src: '/pwa-192.png', sizes: '192x192', type: 'image/png' },
          { src: '/pwa-512.png', sizes: '512x512', type: 'image/png' },
          { src: '/pwa-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
      },
      workbox: {
        globPatterns: ['**/*.{js,css,html,ico,png,svg}'],
        navigateFallback: '/index.html',
        navigateFallbackDenylist: [/^\/api\//],
        skipWaiting: true,
        clientsClaim: true,
      },
    }),
  ],
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          'react-vendor': ['react', 'react-dom', 'react-router-dom'],
          'query-vendor': ['@tanstack/react-query'],
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
      '/ws': {
        target: 'ws://localhost:8080',
        ws: true,
      },
    },
  },
})
