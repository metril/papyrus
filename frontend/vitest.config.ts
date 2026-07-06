import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// Mirrors the same flag in vite.config.ts, so `REACT_COMPILER=1 npm test`
// exercises the exact transform the production build would use.
const reactCompilerEnabled = process.env.REACT_COMPILER === '1';

export default defineConfig({
  plugins: [
    react({
      babel: reactCompilerEnabled
        ? { plugins: [['babel-plugin-react-compiler', {}]] }
        : undefined,
    }),
  ],
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    globals: false,
    css: false,
    include: ['src/**/*.test.{ts,tsx}'],
  },
});
