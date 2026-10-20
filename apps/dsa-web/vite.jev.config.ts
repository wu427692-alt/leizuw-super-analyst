import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { fileURLToPath, URL } from 'node:url';

// Separate artifact and URL namespace: never empties or replaces the main
// production frontend, which may be deployed from a different source snapshot.
export default defineConfig({
  base: '/jev-backtest-assets/',
  plugins: [tailwindcss(), react()],
  publicDir: false,
  build: {
    outDir: 'dist-jev',
    emptyOutDir: true,
    rollupOptions: {
      input: fileURLToPath(new URL('./jev-backtest.html', import.meta.url)),
    },
  },
});
