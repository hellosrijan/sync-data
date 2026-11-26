---
name: task-ledger
description: Template and rules for the task ledger in docs/tasks/, the hand-off file the agent team (Monica, Joey, Phoebe, Ross, Chandler, Rachel) uses to pass work and evidence to each other. Use when creating a task or adding a hand-off entry.
---

# Task ledger

One file per task: `docs/tasks/T-<NNN>-<slug>.md`. `NNN` is the next free number in
`docs/tasks/`. Monica creates it. Everyone else appends to it.

## Rules

- Read the whole ledger before you start.
- Append one **Log** entry per hand-off. Never edit another agent's entry.
- Every claim has evidence: a command and its result, or `file:line`.
- Only Monica changes **Status**. Values: `routing` → `coding` → `testing` → `docs` →
  `review` → `done`. `blocked` can happen at any point.
- `BLOCKED: <reason>` at the top of your entry means stop and wait for Monica.

## Template

```markdown
# T-<NNN>: <title>

**Status:** routing
**Owner:** agent-monica
**Requested:** <YYYY-MM-DD> by the user: "<the task, quoted>"

## Acceptance criteria
<!-- Joey drafts; Monica approves. Each one testable. Cite the brief or design section. -->
- [ ] AC1: … (docs/design.md §…)
- [ ] AC2: …

## Routing plan
<!-- Joey. Monica checks this table before every dispatch. -->
| Step | Agent | Input | Done when |
|---|---|---|---|
| 1 | agent-phoebe | AC1–AC2 | check + makemigrations --check clean |
| 2 | agent-ross | Phoebe's files | tests for AC1–AC2 pass, admin pages 200 |
| 3 | agent-chandler | Ross's results | docs + ledger + memory updated |
| 4 | agent-monica | all of the above | every AC verified |

## Log
<!-- Append-only. One entry per hand-off. -->

### <YYYY-MM-DD HH:MM> agent-<name> → agent-<next>
- **Did:** …
- **Files:** `path:line`, …
- **Ran:** `command` → result
- **Open:** … (or "none")

## Test results
<!-- Ross: the pytest summary line, plus each AC → test name → pass/fail. -->

## Docs updated
<!-- Chandler: each file changed and why; the agent-memory title stored. -->

## Verdict
<!-- Monica: each AC → verified how → ✅/❌; then done / sent back to <agent>. -->
```
