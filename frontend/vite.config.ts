import path from 'node:path';
import { defineConfig, loadEnv, type Plugin, type ProxyOptions } from 'vite';
import react from '@vitejs/plugin-react';

import tailwindcss from '@tailwindcss/vite';
import { tanstackRouter } from '@tanstack/router-plugin/vite';

// Dev server only: dashboard URLs are answered by the SPA's own index.html and chat URLs by Vite
// itself; everything else under /chat is proxied to the backend.
const serveSpaRoutesLocally = (req: { url?: string }, uiEndpoint: string): string | undefined => {
  const url = req.url ?? ''
  const ui = uiEndpoint.replace(/\/$/, '')
  const dashboard = `${ui.slice(0, -'/ui'.length)}/dashboard`
  if (url === dashboard || url.startsWith(`${dashboard}/`) || url.startsWith(`${dashboard}?`)) return `${ui}/`
  // Vite 404s the bare base path; the backend serves it, so match that here.
  if (url === ui || url.startsWith(`${ui}?`)) return url.replace(ui, `${ui}/`)
  if (url.startsWith(`${ui}/`)) return url
  return undefined
}

const isDashboardUrl = (url: string, uiEndpoint: string): boolean => {
  const dashboard = `${uiEndpoint.slice(0, -'/ui'.length)}/dashboard`
  return url === dashboard || url.startsWith(`${dashboard}/`) || url.startsWith(`${dashboard}?`)
}

// A custom UI prefix may be outside the API proxy prefix; serve its dashboard
// sibling locally while preserving the browser URL for TanStack Router.
const dashboardFallbackPlugin = (uiEndpoint: string): Plugin => ({
  name: 'autochat-dashboard-dev-fallback',
  configureServer(server) {
    server.middlewares.use((req, _res, next) => {
      if (isDashboardUrl(req.url ?? '', uiEndpoint)) req.url = `${uiEndpoint}/`
      next()
    })
  },
})

const devBaseTagPlugin = (uiEndpoint: string): Plugin => ({
  name: 'autochat-dev-base',
  apply: 'serve',
  transformIndexHtml: () => [{ tag: 'base', attrs: { href: `${uiEndpoint}/` }, injectTo: 'head-prepend' }],
})

const devProxy = (
  proxyTarget: string,
  chatBase: string,
  uiEndpoint: string,
  ssoCallbackPath: string,
): Record<string, ProxyOptions> => ({
  [chatBase]: {
    target: proxyTarget,
    changeOrigin: false,
    ws: true,
    bypass: (req) => serveSpaRoutesLocally(req, uiEndpoint),
  },
  [ssoCallbackPath]: { target: proxyTarget, changeOrigin: false },
  '/api': { target: proxyTarget, changeOrigin: false },
})

// Dev only: pre-transform the split dashboard routes so a first visit is not a compile waterfall.
const WARMUP_FILES = ['./src/routes/dashboard/*.tsx', './src/domains/*/presentation/*.tsx']

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
const env = loadEnv(mode, process.cwd(), '');
const proxyTarget = env.VITE_API_URL;
const uiEndpoint = (env.VITE_UI_ENDPOINT || '/chat/ui').replace(/\/$/, '');
const chatBase = (env.VITE_CHAT_BASE || '/chat').replace(/\/$/, '');
// The IdP may still redirect to a legacy callback outside the chat API prefix.
// Forward only that endpoint; the UI itself remains served by Vite at uiEndpoint.
const ssoCallbackPath = env.VITE_SSO_CALLBACK_PATH || '/chat/auth/callback';
const port = env.PORT === undefined || env.PORT.length === 0 ? undefined : Number(env.PORT);
// Deployed builds set SOURCEMAP=hidden so bundles don't reference their maps (FR-TOOL-004).
const sourcemap = env.SOURCEMAP === 'hidden' ? 'hidden' : true;

return {
  // Assets are served at the chat mount; the router also serves dashboard routes beside it.
  // Production assets are relative to the <base> injected by FastAPI at runtime.
  base: mode === 'development' ? `${uiEndpoint}/` : './',
  plugins: [
    dashboardFallbackPlugin(uiEndpoint),
    devBaseTagPlugin(uiEndpoint),
    // Must precede @vitejs/plugin-react: it generates routeTree.gen.ts from src/routes/ and
    // rewrites route modules for code splitting before React's transform runs (FR-TOOL-005).
    // Specs live in tests/ (STD-002 §8.1); the ignore pattern is a backstop for one filed by
    // mistake under src/routes/, which the generator would otherwise turn into a route.
    tanstackRouter({
      target: 'react',
      autoCodeSplitting: true,
      routeFileIgnorePattern: '\\.(test|type-test)\\.tsx?$',
    }),
    react(),
    tailwindcss(),
  ],
  resolve: { alias: { '@': path.resolve(import.meta.dirname, './src') } },
  server: {
    port,
    strictPort: true,
    warmup: { clientFiles: WARMUP_FILES },
    proxy: devProxy(proxyTarget, chatBase, uiEndpoint, ssoCallbackPath),
  },
  build: {
    sourcemap,
    // Read by scripts/check-chunk-split.mjs to assert the admin split (FR-SHELL-015) from the
    // build's own static/dynamic import graph rather than by grepping bundles.
    manifest: true,
    rollupOptions: {
      output: {
        // FR-SHELL-015: the router plugin already splits every route; naming the admin ones
        // `admin.*` gives size-limit a stable path to budget (STD-002 §7.1) and the split check
        // something to assert against. Grouping them into one chunk instead was rejected — it
        // duplicated React, the router and Zod into the admin bundle.
        chunkFileNames: (chunk) =>
          chunk.facadeModuleId?.includes('/src/routes/dashboard/') === true
            ? 'assets/admin.[name]-[hash].js'
            : 'assets/[name]-[hash].js',
      },
    },
  },
};
})
