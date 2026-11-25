# Development workflow

## Rules

- Nothing is committed without the project owner's explicit confirmation. Nothing is pushed
  without a separate, explicit request.
- Work happens in phases. The current phase is on [Home](Home#status).
- `docs/Round1.html` (the brief) wins over every other document.
- Secrets live in `.env` only (git-ignored). `.env.example` has the same keys with placeholder
  values.

## AI agents (Claude Code and opencode)

Both harnesses have the same three agents and the same `/ready-to-commit` command. The source of
truth is `.claude/agents/` and `.claude/commands/`. The opencode versions in `.opencode/agents/`
and `.opencode/commands/` are thin wrappers that read the `.claude/` file and follow it, adding
only opencode tool names and permissions.

| Shared piece | Claude Code | opencode |
|---|---|---|
| Project rules | `CLAUDE.md` | `CLAUDE.md` (read as a fallback because the repo has no `AGENTS.md`) |
| Memory | agent-memory plugin (needs a local patch, see [Known issues](#known-issues)) | Same agent-memory MCP server and database, set up in `.opencode/opencode.jsonc` |
| Commit and push | Only after explicit permission, plus a permission prompt on `git commit` and `git push` (`.claude/settings.json`) | Same prompt (`.opencode/opencode.jsonc`) |

The agents:

| Agent | Does | Runs |
|---|---|---|
| `document-update` | Keeps `docs/design.md`, `README.md`, the `wiki/` pages and `CLAUDE.md` accurate against the code and the brief. Records decisions; never invents them. | Via `/ready-to-commit`, or on request |
| `agent-code-review` | Writes and runs pytest-django tests; reviews the code against the brief's rules (duplicate webhook, out-of-order webhook, per-field merge, sync loop, outage durability, rate cap, history, webhook validation). | Only via `/ready-to-commit` |
| `chat-export` | Runs `scripts/export-chats.py`, which exports every Claude Code and opencode session to `docs/chats/` as markdown, redacting `.env` secrets. The brief requires the full chat history. | Only via `/ready-to-commit` |

## `/ready-to-commit`

A slash command (`.claude/commands/ready-to-commit.md`) that prepares a commit and makes it only
after confirmation. It runs steps a to h in order and stops at the first failure.

| Step | What happens |
|---|---|
| a | Check for changes with `git status --porcelain`. No changes means stop. |
| b | `document-update` brings the docs, README, wiki sources and CLAUDE.md in line with the code. Then `bash scripts/build-wiki.sh "$(mktemp -d)"` must pass. |
| c | `agent-code-review` writes and runs tests with `uv run pytest -q`. A failing test stops the run. Unbuilt features are listed as gaps, not failures. |
| d | `chat-export` runs `scripts/export-chats.py`. A secret-scan hit stops the run. |
| e | `git status` again. List every changed file, grouped by area. |
| f | Draft the commit message. The subject states the decision made. |
| g | Print the message and ask: **Commit**, **Edit message**, or **Don't commit**. |
| h | On **Commit** only: stage, refuse if `.env` or an unlisted file is staged, commit, and show `git log -1 --stat`. It never pushes. |

Steps a to g change no git state. Only step h stages and commits.

## Tests

```sh
docker compose up -d --wait
uv run pytest -q
```

Tests live in `src/<app>/tests/`. The health tests use the real Postgres and Redis from Compose.
pytest-django creates a separate test database. There is no fake Salesforce yet; the brief's
sync tests will need one.

## Known issues

- The agent-memory MCP server needs `mcp<2` (the plugin uses an API that mcp 2.x removed).
  opencode pins it in `.opencode/opencode.jsonc`. In Claude Code, the plugin's `.mcp.json` must
  run the server through `uv run --with "mcp<2" ...`. That's a local patch: a plugin update
  overwrites it, and agent-memory then shows "Connection closed" until it is re-applied.

## How this wiki is published

| Wiki page | Source in the repo |
|---|---|
| Home, Architecture, Development Workflow, sidebar | `wiki/*.md` |
| Getting Started | `README.md` |
| Design | `docs/design.md` |

`.github/workflows/wiki.yml` runs `scripts/build-wiki.sh` on every push to `main` that touches
these sources, then mirrors the result into the `<repo>.wiki.git` repository. Pages that are no
longer built are deleted from the wiki.

To change a page, edit its source and push. To preview locally:

```sh
bash scripts/build-wiki.sh "$(mktemp -d)"
```

To add a hand-written page, create `wiki/Page-Name.md` and link it from `wiki/_Sidebar.md`.
Hyphens in the file name become spaces in the page title.
