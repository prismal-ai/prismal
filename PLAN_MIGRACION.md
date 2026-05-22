# Plan de trabajo — Migración `lightagent-agents` → `prismal`

> Compañero de `propuesta.md`. Este documento operacionaliza la migración del
> repositorio `gitlab.com/lightagents/lightagent-agents` al repositorio
> `github.com/prismal-ai/prismal`, **preservando el histórico completo** de
> commits y aplicando el rebranding **por fases**.
> Autor: Ernesto Crespo · Fecha: 2026-05-22 · Estado: en ejecución (Fase 0 hecha).

---

## 0. Decisiones tomadas (base del plan)

| Decisión | Elección | Implicación |
|---|---|---|
| **Preservación de histórico** | Importar el **repo completo** (286 commits, todo el árbol a la vez) como base de `prismal`. El rebranding/validación se hace **por fases** como commits nuevos encima. | Trazabilidad 100% intacta. El "orden por fases" aplica a la *adaptación y validación*, no al copiado de archivos (ya están todos). |
| **Namespace de import** | **Estrategia B**: renombrar `lightagent/` → `prismal/` ahora y reescribir todos los `from lightagent. …` → `from prismal. …`. | Alto esfuerzo: ~365 archivos Python referencian `lightagent`; ~5500 ocurrencias del nombre en código/config/docs. |
| **Orden de migración** (pedido explícito) | 1) pruebas unitarias → 2) pruebas de integración → 3) core → 4) resto → 5) ejemplos del framework. | Se respeta con un **shim de namespace transitorio** que permite validar capa por capa (ver §3). |
| **Versión del primer release** | `3.0.0` (cambio de nombre = breaking). | Igual que la propuesta. |

### Riesgos asumidos por elegir Estrategia B ahora

1. **Rompe el contrato PEP 420** con el paquete app hermano `lightagent` (regla #6
   de `CLAUDE.md`), que contribuye módulos al namespace `lightagent.*`. Hay que
   **coordinar** ese paquete hermano (renombrarlo a la vez, o aceptar que deja de
   resolver `lightagent.*` desde este paquete). → tarea de coordinación obligatoria.
2. Codemod masivo: riesgo de imports rotos, strings de log/IDs, rutas de recursos,
   nombres de tablas/colecciones y referencias en YAML/TOML que contengan
   `lightagent`. Mitigación: shim transitorio + suite en verde por capa.
3. `pyproject.toml` `[tool.hatch.build.targets.wheel] packages` debe pasar a
   `["prismal"]` (en Estrategia A se quedaba en `["lightagent"]`).

---

## 1. Estado de partida (verificado 2026-05-22)

**Origen** — `lightagent-agents` (GitLab):
- 286 commits. Ramas: `main` (default, tip 2026-04-24), `develop`,
  `fix_build_deploy_cicd` (tip más reciente 2026-05-10, 4 commits propios / 5 por
  detrás de `main`), `add_precommit_pipeline`. Tag: `lightagent-agents/v2.0.0`.
- 15 subpaquetes en `lightagent/`: agents, core, data, events, mcp, memory,
  monitoring, providers, rag, sandbox, scheduler, security, skills, utils.
- Tests: **126** unit, **17** integración, **3** security (146 ficheros `test_*`).
- `examples/` con `patterns/` y `subgraphs/`.
- Build: hatchling; `dist/` con artefactos previos; `twine` en dev-deps.

**Destino** — `prismal-ai/prismal` (GitHub):
- Repo recién creado; antes solo `main.py` + `.idea/`, sin commits.
- `origin` = `git@github.com:prismal-ai/prismal.git` (ya configurado).

> **Decisión pendiente de confirmar:** rama base para importar. Se usó el tip más
> reciente (`fix_build_deploy_cicd`). Si prefieres `main`, hay que **consolidar
> primero** las ramas en origen (mergear `fix_build_deploy_cicd` → `develop` →
> `main`) y re-basar la importación. Ver §2.

---

## 2. Fase 0 — Bootstrap del repo (HECHO, local, sin push)

Ejecutado en el repo local `prismal`:

```bash
cd prismal
git remote add legacy <ruta-local>/lightagent-agents/.git
git fetch legacy --tags          # importa las 4 ramas + tag v2.0.0
git checkout -b migration legacy/fix_build_deploy_cicd
# -> rama 'migration' con los 286 commits; main.py/.idea preservados
```

Resultado: rama `migration` con histórico íntegro (286 commits). **No se ha hecho
push.** Reversible al 100% (el repo destino estaba vacío).

### Pendientes de Fase 0 (heredados de la propuesta, requieren tu acción)

- [ ] Reservar `prismal` en **PyPI** con placeholder `0.0.0` (evita que lo tomen).
- [ ] Registrar dominio `prismal.dev` (alternativas: `.ai`, `.io`).
- [ ] Confirmar **rama base** y si se consolidan ramas en origen antes del push.
- [ ] Decidir el destino de la rama `main` de prismal: que `main` *sea* el
      histórico importado (cutover) vs. mantener trabajo en `migration` y mergear
      al final.
- [ ] Coordinar el paquete app hermano `lightagent` (por Estrategia B).

---

## 3. Mecanismo que permite el orden pedido (shim transitorio)

El orden que pediste (unit → integración → core → resto → ejemplos) choca con el
hecho de que, al renombrar el directorio del paquete `lightagent/` → `prismal/`,
*todo* lo que importa `lightagent` se rompe a la vez. Para validar **capa por
capa** introducimos un **shim de namespace temporal**:

1. `git mv lightagent prismal` y codemod de los imports **internos** del paquete
   (`from lightagent.` → `from prismal.` dentro de `prismal/`). El core ya queda
   funcional bajo el nuevo nombre.
2. Se añade un paquete puente `lightagent/` (un `__init__.py` mínimo que
   reexporta desde `prismal` y emite `DeprecationWarning`). Así, durante la
   transición, **ambos** `from prismal. …` y `from lightagent. …` resuelven.
3. Con el shim activo, cada capa de tests/ejemplos se migra a `from prismal. …`
   y se valida **de forma independiente**, en el orden pedido, sin tener que
   tocar todo el árbol de golpe.
4. El shim se **retira en la última fase**, cuando ya no queda ningún
   `from lightagent. …` en el repo.

> Nota: el shim aquí es de *namespace de import* (interno al repo durante la
> migración), distinto del shim de *distribución* `lightagent-agents` de la
> propuesta (paquete puente publicado en PyPI que depende de `prismal`). Ambos
> pueden coexistir.

---

## 4. Fases de adaptación (commits encima del histórico importado)

Cada fase termina con la suite correspondiente **en verde** y un commit/MR propio,
para mantener trazabilidad y poder revertir por fase.

### Fase 1 — Identidad de distribución (sin tocar imports)
`pyproject.toml`: `name = "prismal"`, `version = "3.0.0"`, `description` larga
(§2 propuesta), `[project.urls]` a `prismal-ai/prismal` + `prismal.dev`,
`keywords` (+`prismal`, `supervisor-pattern`). Aún **no** se cambia
`packages = ["lightagent"]`. Commit aislado, seguro.

### Fase 2 — Codemod de namespace + **pruebas unitarias** (orden #1)
- `git mv lightagent prismal`; codemod imports internos del paquete.
- `packages = ["prismal"]` en hatch.
- Crear shim `lightagent/` (§3).
- Migrar `tests/unit/**` (`from lightagent.` → `from prismal.`).
- **Verde:** `uv run pytest tests/unit -m "not live_api"`.

### Fase 3 — **Pruebas de integración** (orden #2)
- Migrar `tests/integration/**` y `tests/security/**`.
- **Verde:** `uv run pytest -m integration` y `-m security`
  (requieren servicios: sandbox backends, DBs).

### Fase 4 — **Core** (orden #3)
- Barrido de referencias residuales a `lightagent` dentro del código del paquete
  que no sean imports: strings de logging, nombres de recursos/datasets, IDs,
  rutas, nombres de tablas/colecciones, canary tokens, `SecurePromptBuilder`.
- Revisar específicamente: `core/`, `providers/`, `security/`, `agents/graph.py`,
  `agents/tool_registry.py`, `agents/intent_router.py`.
- **Verde:** `uv run mypy prismal` + `uv run bandit -r prismal -c pyproject.toml`.

### Fase 5 — **Resto** (orden #4)
- CI/CD: `.gitlab-ci.yml` (variables de nombre, jobs build/publish, artefactos).
  Decidir si se migra a **GitHub Actions** (repo ahora en GitHub) o se mantiene
  GitLab CI espejado.
- `packaging/deb/` (`lightagent-agents_2.0.0` → `prismal_3.0.0`, `DEBIAN/control`
  campo `Package:`, `changelog`, `copyright`; ruta interna pasa a
  `dist-packages/prismal/`), `packaging/build_rpm.py`.
- `setup.cfg`, `ty.toml`, `.pre-commit-config.yaml`, `.gitleaks.toml`, `.trivyignore`.
- Docs: `README.md` (header/tagline, badges `py/prismal`, `pip install prismal`,
  ejemplos a `from prismal. …`), `CLAUDE.md` (Package context + regla #6 reescrita
  para el nuevo namespace), `CHANGELOG.md` (`## [3.0.0]`), `CONTRIBUTING.md`.
- Recursos de marca: renombrar/retirar PDFs/PPTX/HTML con nombre `LightAgent`.

### Fase 6 — **Ejemplos del framework** (orden #5) + cierre
- Migrar `examples/patterns/**` y `examples/subgraphs/**` y `examples/README.md`.
- **Retirar el shim** `lightagent/`; verificar que no queda ningún
  `from lightagent` (`grep -rn 'lightagent' --include='*.py'` → 0 en imports).
- Verificación final:
  ```bash
  rm -rf dist/ && python -m build
  twine check dist/*
  twine upload --repository testpypi dist/*   # validar en TestPyPI
  uv run pytest && uv run ruff check . && uv run mypy prismal && \
    uv run bandit -r prismal -c pyproject.toml
  ```
- Cutover de `main` + `git push origin` + tag `prismal/v3.0.0`.
- (Diferido) Publicar el shim de distribución `lightagent-agents 2.9.0`
  (`dependencies=["prismal>=3.0.0"]`, `DeprecationWarning`) en PyPI.

### Fase 7 — Post-migración
GitHub: descripción/topics/website/social preview. Read the Docs / MkDocs en
`prismal.dev`. Anuncio de deprecación. Coordinar el paquete app hermano.

---

## 5. Verificación y trazabilidad

- **Histórico:** los 286 commits originales quedan intactos como base; cada fase
  añade commits nuevos y atómicos encima → `git log` muestra desarrollo previo +
  migración. El remote `legacy` y el tag `lightagent-agents/v2.0.0` permiten
  cotejar contra el origen en cualquier momento.
- **Por fase:** suite de tests de la capa en verde antes de cerrar el commit/MR.
- **Final:** `filterwarnings=error` y `fail_under=80` deben seguir cumpliéndose;
  `ruff`/`mypy`/`bandit` limpios; `twine check` OK; validado en TestPyPI.
- **Gate de "0 residuos":** `grep -rn 'lightagent' .` solo debería aparecer en
  el `CHANGELOG`/notas históricas y en el shim de distribución, no en imports.

---

## 6. Checklist operativo

- [x] Fase 0 — histórico importado a `prismal` (rama `migration`, 286 commits, sin push)
- [ ] Fase 0 — reservar `prismal` en PyPI + dominio + confirmar rama base + coordinar paquete hermano
- [ ] Fase 1 — `pyproject.toml` (name/version/description/urls/keywords)
- [ ] Fase 2 — codemod namespace + shim + `tests/unit` en verde
- [ ] Fase 3 — `tests/integration` + `tests/security` en verde
- [ ] Fase 4 — core sin referencias residuales; mypy/bandit limpios
- [ ] Fase 5 — CI/CD + packaging + docs
- [ ] Fase 6 — ejemplos migrados, shim retirado, build/TestPyPI/suite OK, tag `prismal/v3.0.0`
- [ ] Fase 7 — post-migración (GitHub, docs, anuncio, paquete hermano)
