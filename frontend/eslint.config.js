import js from '@eslint/js'
import tseslint from 'typescript-eslint'
import vue from 'eslint-plugin-vue'

export default tseslint.config(
  {
    ignores: [
      '**/node_modules/**',
      '**/dist/**',
      'frontend/coverage/**',
      'frontend/test-results/**',
      'frontend/playwright-report/**',
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  ...vue.configs['flat/recommended'],
  {
    files: ['frontend/tests/**/*.ts'],
    rules: { 'vue/one-component-per-file': 'off' },
  },
  {
    files: ['**/*.vue'],
    languageOptions: { parserOptions: { parser: tseslint.parser, extraFileExtensions: ['.vue'] } },
    rules: {
      'no-undef': 'off',
      'vue/require-default-prop': 'off',
      'vue/multi-word-component-names': 'off',
      'vue/html-self-closing': 'off',
      'vue/max-attributes-per-line': 'off',
      'vue/singleline-html-element-content-newline': 'off',
      'vue/html-closing-bracket-newline': 'off',
      'vue/html-indent': 'off',
    },
  },
  {
    files: ['frontend/src/**/*.{ts,vue}', 'extensions/*/frontend/**/*.{ts,vue}'],
    ignores: ['frontend/src/components/ui/**'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          paths: ['reka-ui', '@lucide/vue', 'lucide-vue-next', '@tanstack/vue-table'],
          patterns: [
            { group: ['@/components/ui/*'], message: '基础组件使用统一出口 @/components/ui。' },
          ],
        },
      ],
    },
  },
  {
    files: [
      'frontend/src/{features,pages,stores}/**/*.{ts,vue}',
      'extensions/*/frontend/**/*.{ts,vue}',
    ],
    rules: {
      'no-restricted-globals': ['error', { name: 'fetch', message: '请求由 api 层处理。' }],
    },
  },
)
