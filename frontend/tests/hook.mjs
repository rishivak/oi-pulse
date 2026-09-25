// Module resolution for `node --test` over the terminal's pure TypeScript.
//
// Node 24 strips TypeScript types natively, so the logic layer runs under
// `node:test` with **no packages installed**. That matters here: this environment
// has no reachable package registry, so a test suite that needed `vitest` could be
// written but never run, and an unrun suite proves nothing. What this hook adds is
// only module resolution — the two things Next.js's bundler does that bare Node
// does not:
//
//   1. the `@/` path alias from `tsconfig.json`,
//   2. extensionless imports (`@/lib/terminal/time` -> `.../time.ts`).
//
// Deliberately a test-only hook rather than a change to `next.config.mjs` or
// `tsconfig.json`: the production build's resolution stays exactly as Next ships it,
// so nothing here can make the build behave differently from a stock Next project.
import { existsSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

const FRONTEND_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const HAS_EXTENSION = /\.(ts|tsx|mjs|cjs|js|json|node)$/;

export function resolve(specifier, context, next) {
  let target = specifier;

  if (target.startsWith("@/")) {
    target = pathToFileURL(path.join(FRONTEND_ROOT, target.slice(2))).href;
  } else if ((target.startsWith("./") || target.startsWith("../")) && context.parentURL) {
    target = new URL(target, context.parentURL).href;
  }

  if (target.startsWith("file:") && !HAS_EXTENSION.test(target)) {
    for (const candidate of [`${target}.ts`, `${target}/index.ts`]) {
      if (existsSync(fileURLToPath(candidate))) return next(candidate, context);
    }
  }

  return next(target === specifier ? specifier : target, context);
}
