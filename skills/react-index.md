# React Skills Index (react)

Compact map for React technical-debt workers: which React tool answers which debt problem, and where the authoritative docs live. **Load on demand; never copy tool source.**

## Debt problem → tool → authoritative docs

| Debt problem | Tool | What it fixes | Docs |
|---|---|---|---|
| Hook bugs (deps, order) | `eslint-plugin-react-hooks` | Rules of hooks, exhaustive-deps | https://www.npmjs.com/package/eslint-plugin-react-hooks |
| JSX/lifecycle smells | `eslint-plugin-react` | JSX, prop-types, lifecycle rules | https://github.com/jsx-eslint/eslint-plugin-react |
| Type errors | `@typescript-eslint` + TS strict | Static typing | https://typescript-eslint.io/ |
| Re-render performance | `React.memo`, `useMemo`, `useCallback` | Unnecessary renders | https://react.dev/reference/react |
| State complexity | `useReducer` / context | Prop drilling, tangled state | https://react.dev/learn |
| Test coverage | React Testing Library + Jest | Component/behavior tests | https://testing-library.com/docs/react-testing-library/intro |
| Accessibility | `eslint-plugin-jsx-a11y` | a11y rules | https://github.com/jsx-eslint/eslint-plugin-jsx-a11y |

## How to use

For a debt job, pick the tool by the debt problem, read only its relevant docs, and respect the repo's own `package.json`, `eslint.config.js`, and `tsconfig.json` (and pinned versions). Workers are **proposal-only**: this index is reference material — never install tools, change dependencies, or run the build. Only the serial integrator runs the target's configured checks.
