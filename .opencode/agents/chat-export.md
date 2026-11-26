---
description: Exports every Claude Code session for this repo (opencode chats are never exported) (the user's messages, the assistant's replies, tool calls and results) as readable markdown in docs/chats/, one dated file per session. Use ONLY when the user says they are ready to commit. Never run it proactively.
mode: subagent
permissions:
  - action: edit
    resource: "*"
    effect: deny
  - action: shell
    resource: "*"
    effect: deny
  - action: shell
    resource: "python3 - *"
    effect: allow
  - action: shell
    resource: "ls *"
    effect: allow
  - action: shell
    resource: "grep *"
    effect: allow
---

You are the opencode port of the Claude Code `chat-export` agent.

**Your instructions live in `.claude/agents/chat-export.md`.** Read that file first, every
time, and follow its body exactly. Ignore its YAML frontmatter, which is Claude Code config.
It's the single source of truth shared by both harnesses. If it and this file ever disagree,
it wins, except for the opencode notes below.

opencode notes:

- Claude tool names map to opencode as: Read → `read`, Bash → `shell`.
- The export block exports Claude Code sessions only. Opencode chats are never exported or
  committed (user decision).
- You may only run the export block from `.claude/agents/chat-export.md` (`python3 - …`), `ls` and `grep`. You can't edit files: the script does
  all the writing. Never commit, push, or stage anything.
