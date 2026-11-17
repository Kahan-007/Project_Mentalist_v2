## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

When the user types `/graphify`, use the installed graphify skill or instructions before doing anything else.

The Codex `Stop` hook runs `scripts/sync_graphify.py` after each completed turn once the hook is trusted. It refreshes the local working graph and checks the GitHub `graphify-snapshot` branch. The snapshot uses only code already on `india-screener-full-project`. It publishes a new graph when that source commit changes.

Rules:
- When `graphify-out/graph.json` exists, use Graphify before your first grep, search or source-file read for a codebase question, and include this rule in every subagent prompt that explores code. With the CLI available, use `graphify query "<question>"`, `graphify path "<A>" "<B>"` for relationships, or `graphify explain "<concept>"` for focused concepts. If the CLI is unavailable, read `graphify-out/graph.json` directly. Find relevant node labels, follow their links, and use their source locations to guide targeted source reads. Verify the relevant details in source.
- Dirty graphify-out/ files are expected after hooks or incremental updates; dirty graph files are not a reason to skip graphify. Only skip graphify if the task is about stale or incorrect graph output, or the user explicitly says not to use it.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- Before wrapping up any task that edits, adds, renames, or deletes indexed files, refresh the graph from the repository root with `graphify update .` (code-only, no API cost). If Graphify refuses the refresh because a removal makes the graph smaller, rerun `graphify update . --force` to allow it to drop obsolete nodes and links.
- On Windows, if `graphify` is unavailable on PATH, use `.\.local\graphify\bin\graphify.exe` if present, or try `py -m graphify`. If Graphify cannot run or the refresh fails, tell the user the saved graph may be stale. Verify relevant source directly when a graph result may be out of date.
- A committed graphify-out/graph.json is rewritten whole on every build, so a large diff to it in a pull request is expected. Do not flag it as churn or suggest skipping the graph rebuild or its hooks.
