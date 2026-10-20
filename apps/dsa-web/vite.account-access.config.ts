import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { fileURLToPath, URL } from 'node:url';

// Release only the shared account form; preserve the deployed research bundle.
export default defineConfig({
  base: '/assets/account-access/',
  plugins: [react(), tailwindcss()],
  publicDir: false,
  build: {
    outDir: 'dist-account-access',
    emptyOutDir: true,
    rollupOptions: {
      input: fileURLToPath(new URL('./account-access.html', import.meta.url)),
      output: { assetFileNames: '[name]-[hash][extname]', entryFileNames: '[name]-[hash].js', chunkFileNames: '[name]-[hash].js' },
    },
  },
});
