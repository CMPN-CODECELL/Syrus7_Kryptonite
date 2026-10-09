import { defineConfig, mergeConfig } from 'vitest/config'
import viteConfig from './vite.config.ts'

export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      environment: 'jsdom',
      globals: true,
      setupFiles: ['./src/test/setup.ts'],
      include: ['src/**/*.test.{ts,tsx}'],
      css: false,
      pool: 'threads',
      maxWorkers: 2,
      reporters: ['default', 'junit'],
      outputFile: { junit: '../evidence/e2e/vitest-junit.xml' },
    },
  }),
)
