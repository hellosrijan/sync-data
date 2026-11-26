# sync-data

Tequity Round 1 exercise: two-way sync of customer and contact records between Salesforce
(the client's CRM) and our operations portal (Postgres).

**`docs/Round1.html` is the source of truth.** Read it before any design or code decision.
If something here or in other docs disagrees with it, the brief wins. Where the brief is silent,
write down the assumption and the question we would have asked, as the brief requires.

Numbers every decision must hold up against:
- 5 million records (customers and contacts together)
- 30 s worst-case sync while both sides are up
- at most 100 requests/sec to the Salesforce record API, retries included
- a 6-hour Salesforce outage: pending outbound calls survive our own restarts; drain the backlog
  as fast as the cap allows

The four rules the brief checks: duplicate webhook, out-of-order webhook, sync loop (our own
update coming back), 6-hour outage.

## Rules

- **Never commit or push without the user's explicit permission.** "Ready to commit" is not
  permission to commit. Wait for an explicit "commit" / "push".
- `/ready-to-commit` (`.claude/commands/ready-to-commit.md`) is the only workflow that runs
  `document-update`, `agent-code-review` and `chat-export` together. It prints the drafted
  commit message and asks for confirmation. It commits only when the user picks **Commit** in
  that prompt, and it never pushes. Pushing always needs a separate, explicit request.
- Work in the phase the user names. Don't plan or build ahead (models, sync logic, architecture)
  until asked.
- Never print or copy values from `.env`.

## Stack

Python 3.14 with uv; Django 5.2 LTS (`>=5.2,<5.3`); DRF; Postgres 17; Redis 7; Dramatiq via
django-dramatiq; dramatiq-crontab; django-environ; psycopg 3 (Postgres driver); httpx (Salesforce HTTP client). All settings come from env vars (`.env`,
template in `.env.example`, same keys in both).

## Layout and commands

```
src/manage.py
src/config/   settings, urls (the only file that defines api/v1/)
src/sync/     the sync app: models, admin, serializers.py, urls.py, permissions.py (IsSalesforceIntegration),
              management/commands/create_integration_user.py, tests/
src/utils/    api/v1/health/ (DRF RetrieveAPIView), urls.py, serializers.py, rundramatiq override
docs/         Round1.html (brief), design.md (design doc), build-plan.md (phases + checklist), openapi.yml (API contract), chats/ (session exports), tasks/ (agent-team ledgers)
wiki/         hand-written GitHub wiki pages (Home, Architecture, Development-Workflow, _Sidebar)
scripts/build-wiki.sh       builds the wiki: wiki/*.md + README.md + docs/design.md + docs/build-plan.md
.github/workflows/wiki.yml  publishes the build to the GitHub wiki on push to main
.claude/                    Claude Code agents, commands, rules, skills, settings: the source of truth
.opencode/                  opencode (V2) config, agents, commands: wrappers around .claude/
docker-compose.yml, Dockerfile, pyproject.toml, uv.lock, .env.example
```

- The GitHub wiki is generated; the repo is the source of truth. README.md becomes the
  "Getting Started" page and docs/design.md becomes "Design". Never edit the wiki in the GitHub
  UI, and never create `wiki/Design.md` or `wiki/Getting-Started.md`. Preview with
  `bash scripts/build-wiki.sh "$(mktemp -d)"`.

```sh
docker compose up -d --build --wait     # db, redis, worker
uv run python src/manage.py check
uv run python src/manage.py migrate
uv run python src/manage.py runserver   # web runs on the host, not in compose
docker compose logs -f worker
uv run pytest -q                        # tests; needs the compose services up (real Postgres, Redis)
```

- The `worker` container runs `rundramatiq --reload --reload-use-polling --processes 1 --threads 2`
  and `manage.py crontab` together, with bash as the supervisor. Put actors in `<app>/tasks.py`.
- `utils/management/commands/rundramatiq.py` overrides django_dramatiq's command to add
  `--watch-include **/*.py`. Without it, `--reload` ignores files in app packages (watchdog 7 +
  Python 3.13+ `full_match`). `utils` must stay before `django_dramatiq` in `INSTALLED_APPS`.

## Agent team (`/monica <task>`)

Build work goes through the team. Give the task with `/monica <task>`. Monica (Sonnet) runs in
the main session and dispatches the others; subagents can't dispatch each other.

| Agent | Role |
|---|---|
| `agent-monica` | Lead: owns the task, approves the plan, dispatches, verifies every criterion |
| `agent-joey` | Router: acceptance criteria and routing plan; re-routes blocked steps |
| `agent-phoebe` | Coder: Django/DRF generic views, serializers, admin, models |
| `agent-ross` | Tester: pytest-django per criterion, Django admin checks; sends failures back to Phoebe |
| `agent-chandler` | Docs, test results, `.claude` rules, agent-memory lessons |
| `agent-rachel` | Backup: unblocks any agent |

- Read `.claude/rules/agent-team.md` (workflow, ledger, agent-memory protocol) and
  `.claude/rules/drf.md` (code conventions). The skills are in `.claude/skills/drf-endpoint/` and
  `.claude/skills/task-ledger/`.
- Hand-offs go in the task ledger, `docs/tasks/T-<NNN>-<slug>.md`, which is committed.
  Cross-task lessons go in agent-memory, stored by Chandler only, titled
  `[team] T-<NNN> …`, with `project: "sync-data"`.
- Opencode chats are not exported (user decision). `.gitignore` also keeps any
  `docs/chats/*-opencode-*.md` file out of git.

## Agents (`.claude/agents/`)

The build team's six agents are in the section above. These are the other agents here.

- `document-update`: keeps `docs/design.md`, other `docs/*.md`, `README.md`, the `wiki/` pages
  and the non-rule sections of this file accurate and clear enough for a teammate to build from. It never edits
  `## Rules`, `Round1.html`, `docs/chats/` or `docs/tasks/`.
- `agent-code-review` and `chat-export`: run **only** via `/ready-to-commit`.

## opencode

This repo is also worked on with opencode (v2.4+, V2 config schema). The `.claude/` files are
the source of truth; `.opencode/` only wraps them.

- Rules: opencode reads this `CLAUDE.md`, because the repo has no `AGENTS.md`. Don't add one: it
  would hide this file from opencode. (`AGENTS.md` is git-ignored.)
- `.opencode/agents/{document-update,agent-code-review,chat-export}.md` and
  `.opencode/commands/ready-to-commit.md` each tell opencode to read and follow the matching
  `.claude/` file. Change behavior in `.claude/`. Change `.opencode/` only for opencode-specific
  tool names or permissions.
- `.opencode/opencode.jsonc` adds the agent-memory MCP server (the same server and the same
  memory database as the Claude Code plugin), and asks before any `git commit` or `git push`.
- agent-memory needs `mcp<2`, because the plugin uses the `Server.list_tools` API that mcp 2.x
  removed. The opencode config pins it. The Claude Code plugin's `.mcp.json` was patched
  locally to run through `uv run --with "mcp<2" ...` (original saved as `.mcp.json.orig`). A
  plugin update overwrites that patch; re-apply it if agent-memory shows "Connection closed"
  again.
- `.claude/settings.json` makes Claude Code ask before `git commit` and `git push`, matching
  the opencode permissions.
- Git tracks only `.opencode/opencode.jsonc`, `.opencode/agents/` and `.opencode/commands/`. The
  rest of `.opencode/` is ignored.
- Opencode chats are never exported or committed (user decision). The chat export is a block
  inside `.claude/agents/chat-export.md`; it handles Claude Code sessions only.
- The build team (`/monica` and its six agents) has no opencode wrappers. It runs in Claude Code only.
