# Angular Skills Index (angular)

Compact map for Angular technical-debt workers: which Angular tool answers which debt problem, and where the authoritative docs live. **Load on demand; never copy tool source.**

## Debt problem → tool → authoritative docs

| Debt problem | Tool | What it fixes | Docs |
|---|---|---|---|
| Template/component lint smells | `@angular-eslint` | Angular-aware lint rules (template + TS) | https://github.com/angular-eslint/angular-eslint |
| Type errors / latent bugs | TypeScript strict mode | Static typing (`strict`, `noImplicitAny`) | https://www.typescriptlang.org/docs/ |
| Change-detection performance | `OnPush` + `trackBy` | Avoid unnecessary re-renders | https://angular.io/guide/change-detection |
| Subscription/memory leaks | `async` pipe / `takeUntil` | Unsubscribed observables | https://angular.io/guide/observables |
| Bundle/startup size | Angular CLI budgets + build | Performance budgets | https://angular.io/guide/build |
| Test coverage | TestBed (Karma/Jest) | Component/service tests | https://angular.io/guide/testing |
| Dependency injection smells | constructor injection + providers | Scope and lifetime | https://angular.io/guide/dependency-injection |

## How to use

For a debt job, pick the tool by the debt problem, read only its relevant docs, and respect the repo's own `angular.json`, `tsconfig.json`, and `eslint.config.js` (and pinned versions). Workers are **proposal-only**: this index is reference material — never install tools, change dependencies, or run the Angular CLI. Only the serial integrator runs the target's configured checks.
