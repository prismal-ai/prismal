# prismal-x-example — plugin template

A minimal, installable template for a prismal extension plugin. Copy this
directory, rename `prismal_x_example` → `prismal_x_<your_domain>`, and edit the
nodes / subgraph to taste.

## Layout

```
plugin_template/
├── pyproject.toml                      # declares entry points (the discovery contract)
├── src/prismal_x_example/
│   ├── __init__.py
│   ├── nodes.py                        # @prismal_node nodes (prismal.nodes group)
│   └── plugin.py                       # register_<name>(registry) (prismal.subgraphs group)
└── tests/test_plugin.py
```

## Use it

```bash
# 1. Rename the package
mv src/prismal_x_example src/prismal_x_<domain>
#    …and update the names in pyproject.toml + imports.

# 2. Install (editable) into an environment that has prismal
pip install -e .

# 3. Verify discovery
python -m prismal.plugins list
python -m prismal.plugins doctor
```

After install, `prismal.agents.extension.discover_plugins()` auto-registers the
declared entry points. Gate which plugins load with
`PRISMAL_PLUGINS_ALLOWLIST` / `PRISMAL_PLUGINS_DENYLIST`.

## Entry-point groups

| Group | Export | Contract |
|-------|--------|----------|
| `prismal.subgraphs` | `register_<name>(registry)` | self-register or return a `SubgraphDefinition` |
| `prismal.nodes` | a `@prismal_node` callable | imported = registered |
| `prismal.tools` | a `BaseTool` or zero-arg factory | added to the plugin tool pool (cap 120) |
| `prismal.rag_engines` | a RAG engine class | registered in `RAGEngineRegistry` |

Recommended naming convention: `prismal-x-<domain>`.
