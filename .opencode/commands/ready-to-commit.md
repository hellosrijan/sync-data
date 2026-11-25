---
description: Pre-commit workflow. Update docs, review and test, export chats, draft a commit message, then commit only after the user confirms.
subagent: false
---

The user is ready to commit. This is the opencode port of the Claude Code `/ready-to-commit`
command.

**The workflow lives in `.claude/commands/ready-to-commit.md`.** Read that file now and run its
steps a to h exactly, in order, stopping at the first failure. Ignore its YAML frontmatter.
It's the single source of truth shared by both harnesses.

Translate Claude Code names to opencode:

| Claude Code | opencode |
|---|---|
| "Run the `X` agent" (Agent tool) | Run the `X` subagent with the `subagent` tool. The subagents `document-update`, `agent-code-review` and `chat-export` exist in `.opencode/agents/`. |
| AskUserQuestion tool | The `question` tool, with the same three options: Commit, Edit message, Don't commit |
| Bash | `shell` |

Commit message trailer (step f): use the trailer this session is configured to use, if any.
If none is configured, don't add one.

The `git commit` in step h will also hit this project's permission prompt. That's expected:
the user's **Commit** answer and that prompt both have to agree. Never push.
