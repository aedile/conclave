/**
 * ESLint 9.x flat configuration for the Conclave Engine UI.
 *
 * Enforces TypeScript, React, React Hooks, and WCAG accessibility (jsx-a11y)
 * rules across all TypeScript and TSX source files. Test files receive a
 * relaxed subset of rules that would otherwise generate false positives in
 * testing contexts (e.g. @typescript-eslint/no-non-null-assertion).
 *
 * Plugin versions pinned in package.json:
 *   @typescript-eslint/parser + @typescript-eslint/eslint-plugin  8.x
 *   eslint-plugin-react                                            7.x
 *   eslint-plugin-react-hooks                                      7.x
 *   eslint-plugin-jsx-a11y                                         6.x
 */

import js from "@eslint/js";
import tsPlugin from "@typescript-eslint/eslint-plugin";
import tsParser from "@typescript-eslint/parser";
import reactPlugin from "eslint-plugin-react";
import reactHooksPlugin from "eslint-plugin-react-hooks";
import jsxA11yPlugin from "eslint-plugin-jsx-a11y";
import globals from "globals";

// ---------------------------------------------------------------------------
// Shared file patterns
// ---------------------------------------------------------------------------

const TS_FILES = ["src/**/*.ts", "src/**/*.tsx"];
const TEST_FILES = ["src/__tests__/**/*.ts", "src/__tests__/**/*.tsx"];

// ---------------------------------------------------------------------------
// Configuration array
// ---------------------------------------------------------------------------

export default [
  // 1. ESLint core recommended rules as baseline (applies to all files).
  //    TypeScript files override no-undef (TypeScript's type checker handles this).
  js.configs.recommended,

  // 2. TypeScript + React source files.
  {
    files: TS_FILES,
    languageOptions: {
      parser: tsParser,
      parserOptions: {
        ecmaVersion: "latest",
        sourceType: "module",
        ecmaFeatures: { jsx: true },
        // project: true would enable type-checked rules but requires tsconfig
        // path resolution; omitted here to keep CI fast (no type-checked lint).
      },
      globals: {
        // Full browser globals — covers window, document, localStorage,
        // EventSource, EventListener, JSX namespace, Storage, fetch, etc.
        ...globals.browser,
        // ES2020 globals (Promise, Map, Set, etc.)
        ...globals.es2020,
      },
    },
    plugins: {
      "@typescript-eslint": tsPlugin,
      react: reactPlugin,
      "react-hooks": reactHooksPlugin,
      "jsx-a11y": jsxA11yPlugin,
    },
    settings: {
      react: {
        // Automatically detect React version from node_modules.
        version: "detect",
      },
    },
    rules: {
      // ------------------------------------------------------------------
      // Disable no-undef for TypeScript files.
      // TypeScript's own type-checker (tsc --strict) enforces undefined
      // references more accurately than ESLint can without full type info.
      // Keeping no-undef on TS files causes false positives for TypeScript
      // namespaces (JSX, React.ReactNode) and global browser types.
      // ------------------------------------------------------------------
      "no-undef": "off",

      // ------------------------------------------------------------------
      // TypeScript — recommended rules (no type-checking required)
      // ------------------------------------------------------------------
      ...tsPlugin.configs["recommended"].rules,

      // Prefer const over let where variable is not reassigned.
      "prefer-const": "error",

      // No unused variables — TypeScript's own noUnusedLocals handles this;
      // keep as warning here to surface it in the editor without blocking build.
      "@typescript-eslint/no-unused-vars": [
        "warn",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],

      // Allow explicit `any` as a warning — TypeScript strict mode enforced by tsc.
      "@typescript-eslint/no-explicit-any": "warn",

      // ------------------------------------------------------------------
      // React — recommended rules + JSX transform (React 18, no React import needed)
      // ------------------------------------------------------------------
      ...reactPlugin.configs.recommended.rules,
      ...reactPlugin.configs["jsx-runtime"].rules,

      // ------------------------------------------------------------------
      // React Hooks — enforce rules of hooks and exhaustive deps.
      // ------------------------------------------------------------------
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "warn",

      // ------------------------------------------------------------------
      // jsx-a11y — WCAG 2.1 AA accessibility rules (recommended set)
      // ------------------------------------------------------------------
      ...jsxA11yPlugin.flatConfigs.recommended.rules,
    },
  },

  // 3. Test file overrides — relax rules that produce false positives in tests.
  {
    files: TEST_FILES,
    rules: {
      // Tests routinely use non-null assertions for brevity.
      "@typescript-eslint/no-non-null-assertion": "off",
      // Tests cast unknown types freely.
      "@typescript-eslint/no-explicit-any": "off",
      // Tests may use console.log/warn for debugging output.
      "no-console": "off",
      // Tests use require() style dynamic imports in some cases.
      "@typescript-eslint/no-require-imports": "off",
    },
  },

  // 4. Ignore patterns — generated/vendored directories.
  {
    ignores: [
      "dist/**",
      "coverage/**",
      "node_modules/**",
      "playwright-report/**",
      "test-results/**",
    ],
  },
];
