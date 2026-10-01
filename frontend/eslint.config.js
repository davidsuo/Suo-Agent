import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist', 'node_modules', '**/*.bak.tsx']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      globals: globals.browser,
    },
    rules: {
      // ============================================================
      // 【US-09 技术债 B-002】以下规则暂时放宽，下一轮集中清理
      // 详见 docs/BACKLOG.md
      // ============================================================

      // 30+ 条 any，需类型重构，暂关
      '@typescript-eslint/no-explicit-any': 'off',

      // 未使用变量降级为 warning
      '@typescript-eslint/no-unused-vars': 'warn',
      'no-unused-vars': 'off',

      // 代码风格，降级为 warning
      'prefer-const': 'warn',
      'no-useless-escape': 'warn',

      // React Compiler 新规则太严，先关
      'react-hooks/immutability': 'off',

      // hooks 依赖提示保留为 warning（不阻塞 CI）
      'react-hooks/exhaustive-deps': 'warn',

      // react-refresh 只在 dev 有意义，CI 不阻塞
      'react-refresh/only-export-components': 'off',
    },
  },
])