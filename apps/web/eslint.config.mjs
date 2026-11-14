import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

// Light design system: colours come only from the Signal Intel tokens (bg-surface, text-ink-muted,
// si-fam-* families…). Raw Tailwind palette colours and classes the theme does not define fail lint.
const OFF_SYSTEM =
  "/(^|[\\s:'\"`])(bg|text|border|ring|from|to|via|fill|stroke|outline|decoration|divide|shadow)-(slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-[0-9]{2,3}|(^|[\\s:'\"`])(border-border|bg-accent|text-accent|border-accent|bg-surface-raised|text-white|bg-white|text-black|bg-black)([\\s'\"`]|$)/";
const OFF_SYSTEM_MESSAGE =
  "Use the Signal Intel design-system colours (bg-surface, text-ink, text-ink-muted, border-line, si-fam-*), not raw Tailwind colours.";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    files: ["src/**/*.{ts,tsx}"],
    rules: {
      "no-restricted-syntax": [
        "error",
        { selector: `Literal[value=${OFF_SYSTEM}]`, message: OFF_SYSTEM_MESSAGE },
        { selector: `TemplateElement[value.raw=${OFF_SYSTEM}]`, message: OFF_SYSTEM_MESSAGE },
      ],
    },
  },
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
]);

export default eslintConfig;
