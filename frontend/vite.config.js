import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

// Dev server proxies API calls to the backend, so the browser sees one origin (no CORS setup).
const API_PATHS = ['/health', '/auth', '/records', '/findings', '/verify', '/discovery', '/metrics', '/admin'];

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const target = env.VITE_BACKEND_URL || 'https://52-41-36-135.sslip.io';
  return {
    base: './', // works at / or under /ui/
    plugins: [react()],
    server: { proxy: Object.fromEntries(API_PATHS.map((p) => [p, { target, changeOrigin: true }])) },
  };
});
