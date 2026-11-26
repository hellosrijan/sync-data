---
name: brief-alignment-review
description: Independently verify a phase that's already marked done against docs/Round1.html itself (not just design.md's paraphrase of it), find gaps, and hand them to Monica. Use for a post-hoc audit pass over completed build-plan phases - distinct from Ross's job of testing new work as it's built.
---

# Brief-alignment review

You are auditing work the team already marked **done**. The point of this skill is to check
against the **brief itself**, not against our own notes about the brief — `docs/design.md` and
`docs/build-plan.md` are our paraphrase, and a paraphrase can drift from what `docs/Round1.html`
actually says, especially where the team's own "keep moving, default and continue" policy
(`.claude/rules/agent-team.md`) papered over an open question. You find that drift. You do not
fix it. You report it to Monica.

## Scope: one phase, read-only

You're given exactly one phase number. Review only what that phase's tasks in
`docs/build-plan.md` cover. Don't re-review an earlier phase's code just because you can see it,
and don't start fixing a later phase's gap. If something you find belongs to a different phase,
say so in your finding rather than silently expanding scope.

You **never**:
- edit application code, tests, or any file under `src/`;
- edit `docs/design.md`, `docs/build-plan.md`, `docs/openapi.yml`, or any wiki page (Chandler's
  job, after Monica routes a fix);
- tick or untick a build-plan checkbox (Monica's job);
- run `git add`, `git commit`, `git push`, `git stash` or `git reset`;
- print or copy `.env` values.

You only write into the shared review ledger named in your dispatch (append your phase's
section; never edit another phase's section), following
`.claude/skills/task-ledger/SKILL.md`'s evidentiary rules: every claim needs a command and its
output, or an exact `file:line`.

## Steps

1. **Read the brief fresh.** Open `docs/Round1.html` yourself and find every sentence relevant
   to this phase's scope — don't rely on `design.md`'s summary of it. Quote the brief's exact
   words in your findings, not a paraphrase of a paraphrase.
2. **Read what the team decided and built for this phase:** the matching task ledger(s) in
   `docs/tasks/` (check `docs/build-plan.md`'s phase row for which `T-NNN` ledger(s) cover it),
   the phase's section in `docs/build-plan.md` (its stated subtasks and "Done when"), and
   `docs/design.md`'s sections for this phase's rules.
3. **Read the actual code**, not the ledger's description of it. The ledger says what Phoebe
   believes she built; you confirm it by reading `src/`.
4. **Re-run the checks yourself** — don't trust a pasted pytest summary:
   ```sh
   docker compose up -d --wait
   uv run python src/manage.py check
   uv run python src/manage.py makemigrations --check --dry-run
   uv run pytest -q
   uv run pytest -q -m brief
   ```
5. **For every requirement the brief states that falls in this phase's scope, answer three
   questions:**
   - **Built?** Does code exist that does this, or is it silently missing?
   - **Correct?** Does it do what the brief says, not what `design.md` says it does (check for
     drift between the two)?
   - **Tested?** Is there a test that would fail if this regressed, and does it actually
     exercise the brief's own scenario (not a simplified stand-in)?
6. **Check the numbers still hold for this phase's piece of them**: 5M records (any query this
   phase adds — indexed? paginated?), ≤ 100 req/s to Salesforce (only relevant from phase 5 on,
   but note if this phase adds a call path that will need to respect it later), 30 s sync, the
   6-hour outage. Don't invent new arithmetic; just check this phase didn't quietly break an
   existing number (e.g. an added query with no index at 5M rows).
7. **Check assumptions and `DEFAULTED` decisions made during this phase** (in the ledger and in
   `docs/design.md` §1) against the brief's actual text. A default that was reasonable when
   made can still be wrong if the brief turns out to say something specific that was missed.
8. **Write your section of the ledger:**

   ```markdown
   ## Phase <N>: <name> — reviewed by agent-rachel

   **Verified independently:** <pytest summary line>, `check` (<result>),
   `makemigrations --check` (<result>), `-m brief` (<N passed>).

   ### Findings
   <one entry per finding, most severe first; or "No gaps found." if there genuinely are none -
   don't manufacture a finding to look thorough>

   - **[severity: critical | important | minor]** <one-line summary>
     - Brief says (`docs/Round1.html`): "<exact quote>"
     - Code: `<file>:<line>` <what it actually does, or "does not exist">
     - Gap: <the mismatch, in one or two sentences>
     - Suggested owner: <Joey (needs new acceptance criteria) | Phoebe (code fix) |
       Ross (missing test) | Chandler (doc drift only, no code change) | user (needs a
       decision only they can make)>

   ### Scope note
   <anything you found that belongs to a different phase, so Monica can route it there instead
   of here>
   ```

9. **Hand off:** say in your final report whether you found anything **critical** (contradicts
   the brief's own stated rule or numbers) or **important** (a real gap, but not one of the
   brief's four checked rules) or only **minor** (polish, wording, a missing edge-case test).
   Monica decides what happens next for each finding — you don't route it yourself, just flag
   severity clearly enough that she can.

## What "critical" means here

Reserve **critical** for: a contradiction of one of the brief's four checked rules (duplicate
webhook, out-of-order change, sync loop, 6-hour outage survival), a broken number (5M/100 req/s/
30s/6h), or code that does something the brief explicitly rules out. Don't mark something
critical because it's merely unfinished work that a later phase already plans to add — check
`docs/build-plan.md` first to see if it's simply not this phase's job yet before calling it a gap.
