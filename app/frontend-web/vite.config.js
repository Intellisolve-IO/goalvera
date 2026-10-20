import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    open: false,
    // Pipeline sessions come from the local read-only review service ().
    proxy: {
      '/api': 'http://127.0.0.1:8787',
    },
    fs: {
      // Allow importing fixtures from ../samples
      allow: [path.resolve(__dirname, '../..')],
    },
  },
  test: {
    // UI checks rendered with react-dom/server (no browser needed). pipelineFilters.test.js runs under node --test.
    include: ['src/**/*.spec.{js,jsx}'],
    environment: 'node',
  },
  resolve: {
    alias: {
      '@samples': path.resolve(__dirname, '../samples'),
    },
  },
});
