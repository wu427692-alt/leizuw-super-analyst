import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { fileURLToPath, URL } from 'node:url';

// This isolated artifact cannot empty or overwrite the existing production SPA.
export default defineConfig({
  base: '/assets/ai-news/',
  plugins: [react(), tailwindcss()],
  // This entry reuses the real workspace shell. Other workspaces remain in the
  // existing production SPA: do not compile or preload their private chunks.
  resolve: { alias: [{
    find: /^.*\/utils\/route(?:Data)?Preload$/,
    replacement: fileURLToPath(new URL('./src/utils/documentRoutePreload.ts', import.meta.url)),
  }] },
  publicDir: false,
  build: {
    outDir: 'dist-ai-news',
    emptyOutDir: true,
    rollupOptions: {
      input: fileURLToPath(new URL('./ai-news.html', import.meta.url)),
      output: { assetFileNames: '[name]-[hash][extname]', entryFileNames: '[name]-[hash].js', chunkFileNames: '[name]-[hash].js' },
    },
  },
});
