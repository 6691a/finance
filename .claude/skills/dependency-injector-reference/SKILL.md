---
name: dependency-injector-reference
description: Use when the user asks about Python Dependency Injector, dependency-injector, DeclarativeContainer, providers, wiring, Provide, inject, overrides, Resources, async injection, or FastAPI and SQLAlchemy dependency injection.
---

# Python Dependency Injector Reference

Verify Dependency Injector behavior against current official sources before answering. Do not rely on memory for provider semantics, wiring behavior, async mode, or lifecycle details.

## Source order

1. Inspect the project's `pyproject.toml`, lockfile, and installed package to identify the version in use.
2. Search the official documentation at <https://python-dependency-injector.ets-labs.org/>.
3. Verify implementation-sensitive claims against the official <https://github.com/ets-labs/python-dependency-injector> repository. Prefer its default branch, examples, tests, and `src/dependency_injector/` source.
4. Use third-party sources only when the user explicitly requests comparisons or the official sources do not cover the issue. Label any inference.

## Route the question

| Topic | Official documentation |
|---|---|
| Factory, Singleton, Callable, Coroutine, Resource | `/providers/` |
| Awaitable dependencies and async-mode cascade | `/providers/async.html` |
| `@inject`, `Provide`, `Provider`, module wiring | `/wiring.html` |
| Test doubles and provider replacement | `/providers/overriding.html` |
| Startup and shutdown lifecycle | `/providers/resource.html` |
| FastAPI with SQLAlchemy | `/examples/fastapi-sqlalchemy.html` |

Open the exact page and relevant GitHub file before making API-specific recommendations.

## Answer contract

- State the installed or targeted Dependency Injector version when behavior may vary.
- Link each important claim to the exact official documentation or GitHub file that supports it.
- Distinguish dependency injection from a Service Locator. Application code should receive dependencies through constructor or function parameters; restrict direct container lookups to the composition root.
- Explain whether a provider returns an object or an awaitable. Account for async-mode cascade when an injected dependency is awaitable.
- Show the required `container.wire()` or `WiringConfiguration` step when recommending `Provide` markers.
- When injecting a factory itself, use the documented provider-delegate form rather than injecting a produced instance.
- Preserve provider scope deliberately: use `Factory` for per-call objects, `Singleton` only for shared lifecycle state, and `Resource` for explicit initialization or shutdown.
- For tests, prefer provider overrides over replacing project globals.
- Fit examples to the repository's existing container, settings, repository, session, and entrypoint conventions.
- If the user only asks for an explanation or review, do not edit files. Implement only when explicitly requested.

## Common checks

- A `Provide` object reaching runtime usually means the target module or package was not wired, the marker points to the wrong container, or decorator order is wrong.
- A provider with an awaitable dependency switches to async mode and must be awaited or asynchronously injected.
- Creating dependencies with `container.service()` inside business logic is lookup, not parameter injection.
- Injecting a runtime token into a client can be modeled as an async provider dependency so the fully constructed client is injected.
- Do not copy patterns that call `Base.metadata.create_all()` into projects that manage schemas with Alembic.
