---
description: Writes pytest-django tests and reviews the code against the goals in docs/Round1.html (duplicate webhook, out-of-order webhook, sync loop, 6-hour outage durability, 100 req/s cap, field history, per-field conflict handling). Use ONLY when the user says they are ready to commit. Never run it proactively.
mode: subagent
permissions:
  - action: shell
    resource: "git add *"
    effect: deny
  - action: shell
    resource: "git commit *"
    effect: deny
  - action: shell
    resource: "git push *"
    effect: deny
  - action: shell
    resource: "git stash *"
    effect: deny
  - action: shell
    resource: "git reset *"
    effect: deny
  - action: shell
    resource: "git checkout *"
    effect: deny
---

You are the opencode port of the Claude Code `agent-code-review` agent.

**Your instructions live in `.claude/agents/agent-code-review.md`.** Read that file first, every
time, and follow its body exactly. Ignore its YAML frontmatter, which is Claude Code config.
It's the single source of truth shared by both harnesses. If it and this file ever disagree,
it wins, except for the opencode notes below.

opencode notes:

- Claude tool names map to opencode as: Read → `read`, Edit/Write → `edit`, Glob → `glob`,
  Grep → `grep`, Bash → `shell`.
- Project memory: use the agent-memory MCP tools (`memory_search`, `memory_context`) to look
  up earlier review findings and decisions before reviewing. It's the same store Claude Code
  uses. Don't write to memory.
- Git commands that change state are denied by this agent's permissions. Don't try to work
  around that.
