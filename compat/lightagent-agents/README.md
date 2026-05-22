# lightagent-agents (deprecated → `prismal`)

> **This package has been renamed to [`prismal`](https://github.com/prismal-ai/prismal).**

`lightagent-agents` `2.9.0` is a **compatibility bridge**. It contains no
framework code of its own: it depends on `prismal` and ships a small import
shim so that legacy code keeps working during migration:

- `pip install lightagent-agents` installs `prismal` as a dependency.
- `from lightagent. … import …` is transparently redirected to
  `from prismal. … import …` (a `meta_path` finder), emitting a
  `DeprecationWarning`.

## Migrate

```bash
pip uninstall lightagent-agents
pip install prismal
```

```python
# before
from lightagent.agents.graph import get_async_compiled_graph
# after
from prismal.agents.graph import get_async_compiled_graph
```

Environment variables also moved from the `LIGHTAGENT_` prefix to `PRISMAL_`
(the legacy prefix still works via a deprecated fallback in `prismal`).

This bridge will stop being maintained in a future release. Please migrate to
`prismal`.
