import path from 'node:path';
import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

import tailwindcss from '@tailwindcss/vite';
import { tanstackRouter } from '@tanstack/router-plugin/vite';

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
const env = loadEnv(mode, process.cwd(), '');
const proxyTarget = env.VITE_API_URL;
const port = env.PORT === undefined || env.PORT.length === 0 ? undefined : Number(env.PORT);

return {
  // Must match ChatConfig.ui_endpoint on the backend serving this build (AUTOCHAT_UI_ENDPOINT,
  // default /chat/ui) and src/app/router.tsx's `basepath` — kept in step by hand, same as before.
  base: '/chat/ui/',
  plugins: [
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
  resolve: {
    alias: {
      '@': path.resolve(import.meta.dirname, './src'),
    },
  },
  server: {
    port,
    strictPort: true,
    proxy: {
      '/bedrock-chat': { target: proxyTarget, changeOrigin: false, ws: true },
      // '/chat' also matches this app's own base ('/chat/ui/'); bypass the proxy for that
      // prefix so Vite serves the SPA locally instead of forwarding it to the backend.
      '/chat': {
        target: proxyTarget,
        changeOrigin: false,
        bypass: (req) => (req.url?.startsWith('/chat/ui') ? req.url : undefined),
      },
      '/api': { target: proxyTarget, changeOrigin: false },
    },
  },
  build: {
    sourcemap: true,
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
          chunk.facadeModuleId?.includes('/src/routes/admin/') === true
            ? 'assets/admin.[name]-[hash].js'
            : 'assets/[name]-[hash].js',
      },
    },
  },
};
})
