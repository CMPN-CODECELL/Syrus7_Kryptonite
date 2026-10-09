import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { gzipSync } from 'node:zlib'
import { defineConfig, type Plugin, type ProxyOptions } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// API target for the dev/preview proxy. Tests may point at a separate backend (e.g. :8100).
const apiTarget = process.env.QF_API_TARGET ?? 'http://127.0.0.1:8000'

const proxy: Record<string, ProxyOptions> = {
  '/api': {
    target: apiTarget,
    changeOrigin: false,
    // keep SSE streaming unbuffered through the proxy
    configure: (p) => {
      p.on('proxyRes', (proxyRes) => {
        if (String(proxyRes.headers['content-type'] ?? '').includes('text/event-stream')) {
          proxyRes.headers['cache-control'] = 'no-cache'
          proxyRes.headers['x-accel-buffering'] = 'no'
        }
      })
    },
  },
}

const GZIP_TYPES: Record<string, string> = { '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8' }

// `vite preview` sends the build uncompressed, which no production static host does. With QF_PREVIEW_GZIP=1 the
// hashed JS/CSS assets are gzipped, so throttled-network measurements reflect a compressing host.
function gzipPreview(): Plugin {
  const cache = new Map<string, Buffer>()
  return {
    name: 'qf-gzip-preview',
    configurePreviewServer(server) {
      if (process.env.QF_PREVIEW_GZIP !== '1') return
      const dist = resolve(server.config.root, server.config.build.outDir)
      server.middlewares.use((req, res, next) => {
        const url = (req.url ?? '').split('?')[0]
        const type = GZIP_TYPES[url.slice(url.lastIndexOf('.'))]
        if (!type || !url.startsWith('/assets/') || url.includes('..') || !/\bgzip\b/.test(String(req.headers['accept-encoding'] ?? ''))) return next()
        const file = resolve(dist, `.${url}`)
        let body = cache.get(file)
        if (!body) {
          if (!existsSync(file)) return next()
          body = gzipSync(readFileSync(file))
          cache.set(file, body)
        }
        res.writeHead(200, { 'Content-Type': type, 'Content-Encoding': 'gzip', 'Content-Length': body.length, 'Cache-Control': 'public, max-age=31536000, immutable', Vary: 'Accept-Encoding' })
        res.end(body)
      })
    },
  }
}

export default defineConfig({
  plugins: [react(), tailwindcss(), gzipPreview()],
  server: { host: '127.0.0.1', port: 5173, strictPort: true, proxy },
  preview: { host: '127.0.0.1', port: 4173, strictPort: true, proxy },
  build: {
    target: 'es2022',
    sourcemap: false,
    // The 3D scene (three.js, about 0.9 MB) and the charts are only reachable through lazy routes, so the default
    // splitting keeps them out of the entry. Hand-made vendor groups caused import cycles between chunks.
    chunkSizeWarningLimit: 1000,
  },
})
