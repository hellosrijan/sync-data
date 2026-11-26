# Agent team: roles, hand-offs and communication

Applies to every agent in `.claude/agents/agent-*.md` and to the `/monica` command.

## The team

| Agent | Role | Writes code? |
|---|---|---|
| **agent-monica** | Owns the task end to end. Approves the plan, dispatches every step, and gives the final verdict against the requirements. Runs in the main session through `/monica <task>`. | No |
| **agent-joey** | Turns the task into acceptance criteria and a routing plan (which agent, which step, done-when). Re-routes when a step is blocked or lands with the wrong agent. | No |
| **agent-phoebe** | Implements: models, migrations, serializers, DRF generic views, URLs, admin, tasks. | Yes |
| **agent-ross** | Writes and runs pytest-django tests and checks the Django admin. Sends failures back to Phoebe with exact repro steps. | Tests only |
| **agent-chandler** | Brings docs, test results and `.claude` rules up to date for Monica's review, and records lessons in agent-memory. | Docs only |
| **agent-rachel** | Backup. Unblocks any agent: research, a second opinion, or taking over a stuck subtask. | When asked |

**Models (user decision):** every agent runs on `haiku` (cheaper) except `agent-monica`, which
runs on `sonnet` — she's the one who approves plans, verifies every acceptance criterion
herself and writes the verdict, so she keeps the stronger model. This also applies to
`agent-code-review` (the `/ready-to-commit` reviewer, not part of the dispatch team but still
"an agent other than Monica"). `document-update` and `chat-export` were already on `haiku`.
Known risk: Haiku has occasionally been stopped mid-run by a model-provider safeguard on
ordinary doc-editing work (observed once with `document-update` in this project). If an agent
stops that way, Monica reports it to the user and waits — safeguards cover the whole
conversation, not just that one subagent, so don't retry around it.

Subagents can't start other subagents. Only Monica, running in the main session, dispatches.
An agent that needs another agent says so in its report and in the ledger, and Monica
dispatches.

## Workflow for one task

```
user ─/monica <task>─► Monica ─► Joey: criteria + routing plan ─► Monica approves
                                   │
          ┌────────────────────────┘
          ▼
       Phoebe (code) ─► Ross (tests + admin) ──fail──► Phoebe   (max 3 rounds,
          ▲                     │ pass                            then Rachel helps;
          │                     ▼                                 still failing →
          │              Chandler (docs, results, rules, memory)   Monica asks the user)
          │                     │
          └── gaps ◄── Monica (verdict against the criteria) ──done──► user
```

1. **Monica** creates the task ledger (below) and asks **Joey** for criteria and a routing plan.
2. Monica checks the plan against `docs/Round1.html`, `docs/design.md` and the wiki Architecture
   page, fixes or approves it, and dispatches the steps in order. Before each dispatch she
   checks the ledger's routing table, so the right agent gets the right step.
3. **Phoebe** implements, runs `manage.py check` and `makemigrations --check`, and hands off.
4. **Ross** tests. On a failure he writes the repro in the ledger and Monica sends it back to
   Phoebe. After **3** failed rounds Monica brings in **Rachel**. If it still fails, Monica
   stops and asks the user.
5. **Chandler** updates the docs (following `.claude/agents/document-update.md` for the rules
   on `docs/`, `README.md`, `wiki/` and `CLAUDE.md`), records Ross's results in the ledger,
   updates `.claude` rules if a convention changed, and stores lessons in agent-memory.
6. **Monica** re-checks every acceptance criterion herself, by reading the code and running
   the tests, not by trusting the reports. Then she writes the verdict.

Nobody commits. When the task is done, Monica tells the user to run `/ready-to-commit`.

## The build checklist: `docs/build-plan.md`

The build plan is also the checklist. There's no separate todo file. Every subtask is a GitHub
checkbox, with the markers explained at the top of the plan.

- **Monica**:
  - **starting a task:** appends `**In progress.**` to its subtasks;
  - **after verifying a subtask's acceptance criteria herself:** changes `- [ ]` to `- [x]` and
    appends `*Done YYYY-MM-DD.*`;
  - **cancelling one:** `- [x] ~~…~~ *Cancelled: reason.*`;
  - **when a phase starts or finishes:** updates the plan's **Status** table.
- **Chandler**, after each task:
  - checks the checkboxes and the Status table against the ledger and fixes any drift;
  - marks every item built on a default with `` `defaulted` ``, and adds that decision to
    "Defaulted decisions to review" if it isn't there yet.
- **Never** tick a box without evidence in the ledger. **Never** tick the user's
  "Defaulted decisions to review" boxes: only the user accepts those.
- Changing what a task *is* (wording, new or removed subtasks) is a plan change. Keep the IDs
  stable: add new subtasks with the next free number, and cancel rather than delete, so the
  ledger's references stay valid.

## Brief-alignment audits (post-hoc, separate from the build loop)

Besides building, Monica can dispatch a **review** of one or more already-done phases: one
**agent-rachel** instance per phase, each independently checking that phase's code against
`docs/Round1.html` itself (not just our own `design.md`/`build-plan.md` paraphrase of it).
Follow `.claude/skills/brief-alignment-review/SKILL.md`. Reviews of independent, already-built
phases are dispatched together in one message so they run concurrently — there's no
Phoebe→Ross-style dependency between them. Findings go in a shared review ledger
(`docs/tasks/V-<NNN>-<slug>.md`, one section per phase, append-only like a task ledger); Monica
reads every finding and decides what to do with each one (file a new build task, ask the user,
or accept it as-is) — a reviewing agent never fixes what it finds.

## Communication channel 1: the task ledger (source of truth)

One file per task: `docs/tasks/T-<NNN>-<slug>.md`, created by Monica from the `task-ledger`
skill (`.claude/skills/task-ledger/SKILL.md`). It's committed with the work, so the hand-offs
are part of the history the brief asks for.

- **Read the whole ledger before starting.** It holds the criteria, the plan and every earlier
  hand-off.
- **Append; never rewrite another agent's entry.** Each agent adds one *Log* entry per hand-off.
- **Every claim needs evidence:** the command you ran and its result, or `file:line`.
- **Status line:** only Monica changes it.

## Communication channel 2: agent-memory (lessons across tasks)

The agent-memory MCP server keeps memory across sessions and across Claude Code and opencode.
It also auto-saves every tool call, so the store is noisy. Rules:

- **Always pass `project: "sync-data"`.** The default is the working folder's name, and a
  session started inside `src/` would write to a project called `src`.
- **Start of a step:** call `memory_context` with `agent_id` set to your agent name and the
  step as `task`, or `memory_search` for specific terms. Treat results as hints and check them
  against the code.
- **Storing:** only **Chandler** stores, once per task, with
  `memory_store(project="sync-data", obs_type="decision" | "bugfix" | "discovery", title="[team] T-<NNN> <short lesson>", narrative=..., facts=[...])`.
  The `[team]` prefix is what makes curated lessons findable among the auto-saved ones.
- **Searching:** use plain words, e.g. `memory_search(project="sync-data", query="team outbox")`.
  **Don't put brackets in the query**: `"[team] …"` breaks the full-text search and returns
  nothing. Results whose title starts with `[team]` are the curated ones; entries titled
  `Ran: …` are auto-saved command logs.
- **Never** store secrets, `.env` values or personal data.

## Rules every agent follows

- `docs/Round1.html` is the source of truth. `docs/design.md` holds the decisions. The numbers
  in `CLAUDE.md` (5M records, 30 s, ≤ 100 req/s, 6-hour outage) apply to every change.
- Build only what the task asks for (CLAUDE.md: don't build ahead).
- **Never** run `git add`, `git commit`, `git push`, `git stash` or `git reset`.
- **Never** print or copy `.env` values.
- Code follows `.claude/rules/drf.md` and the `drf-endpoint` skill.
- **Keep moving; stop only when it's critical** (user instruction). For an open question, take
  the default in `docs/build-plan.md` ("Defaults for questions still open"), or else the most
  conservative option consistent with the brief. Record it as an assumption in `docs/design.md`
  §1 and as `DEFAULTED: <choice> because <reason>` in the ledger, then continue. Monica lists
  every `DEFAULTED` item for the user at the end of the task.
- **Stop (`BLOCKED: <reason>`) only if the next step would:**
  1. contradict the brief or its numbers;
  2. reverse a decision marked "Decided (user)" in `docs/design.md`;
  3. be irreversible or outward-facing (deleting data, rewriting git history, pushing, calling
     outside this machine);
  4. weaken security (secrets, authentication, permissions).
