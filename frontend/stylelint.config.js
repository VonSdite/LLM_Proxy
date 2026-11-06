export default {
  extends: ['stylelint-config-standard'],
  overrides: [{ files: ['**/*.vue'], customSyntax: 'postcss-html' }],
  rules: {
    'selector-class-pattern': null,
    'custom-property-pattern': null,
    'declaration-empty-line-before': null,
    'no-descending-specificity': null,
    'color-named': 'never',
    'color-no-hex': true,
    'selector-pseudo-class-no-unknown': [
      true,
      { ignorePseudoClasses: ['deep', 'global', 'slotted'] },
    ],
  },
  ignoreFiles: ['src/styles/tokens.css', 'node_modules/**'],
}
