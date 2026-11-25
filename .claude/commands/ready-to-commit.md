---
description: Pre-commit workflow. Update docs, review and test, export chats, draft a commit message, then commit only after the user confirms.
disable-model-invocation: true
model: haiku
---

The user is ready to commit. Run these steps in order. If any step fails, stop there, show what
failed, and don't run the later steps.

Steps a to g must not change git state: no `git add`, `git commit`, `git push`, `git stash`,
`git reset`, or similar. The agents' own rules say the same. Only step h may stage and commit,
and only after the user confirms. **Never push.**

## a. Check there is something to commit

Run `git status` and `git diff --stat`.

`git diff --stat` ignores untracked files. So decide by `git status --porcelain`: if it prints
nothing, say "No changes to commit." and stop.

## b. Update the docs

Run the `document-update` agent. Ask it to bring `docs/design.md` (and any other `docs/*.md`),
`README.md`, the `wiki/` pages and the non-rule sections of `CLAUDE.md` in line with the current
code and decisions, checked against `docs/Round1.html`. Pass along any decisions the user made
in this session so it can record them. Show its report: files changed, doc/code mismatches,
open questions.

If it reports a doc/code mismatch it couldn't resolve, show it and continue. If the agent itself
errors out, stop.

Then check that the wiki still builds: `bash scripts/build-wiki.sh "$(mktemp -d)"`. If it fails,
show the error and stop.

## c. Review and test

Run the `agent-code-review` agent. Ask it to write or update pytest-django tests, run them with
`uv run pytest -q`, review the code, and report gaps against `docs/Round1.html`. The gaps must
cover at least:

- duplicate webhook
- out-of-order change
- sync loop
- outage recovery

Show its report: the pytest summary, findings, and gaps.

**If any test fails, or pytest can't run, show the failing tests and their output, then stop.**
Gaps for features that aren't built yet don't fail this step. List them and continue.

## d. Export the chats

Run the `chat-export` agent last, so the export includes as much of this session as possible.
Show the files it wrote and any secret-scan hits. If it reports a secret-scan hit, show it and
stop. The user decides what to do.

## e. List what changed

Run `git status` again. List every changed, added, deleted, and untracked file, grouped by
area: `src/`, `docs/`, `docs/chats/`, `wiki/`, tests, config/infra (including `.github/`), and
`.claude/`. Include the files the agents just wrote.

## f. Write the commit message

- Subject: under 72 characters. Start with a type (`feat`, `fix`, `docs`, `test`, `chore`) and
  state the **decision made**, not the activity. Good: `docs: decide per-field last-writer-wins`.
  Bad: `update docs`, `final`, `wip`.
- Blank line, then a short body: one line per file group, saying what changed and why.
- End with the commit attribution trailer this session is configured to use, if it has one.

## g. Show the message and ask

Print the full commit message in a fenced code block. Then ask the user with the
AskUserQuestion tool, in a question that stands on its own (it may be the only thing on screen):

- Question: "Commit these changes with the message above? (The subject is: <subject line>)"
- Options:
  1. **Commit**: stage all the listed files and commit with this message. No push.
  2. **Edit message**: the user types the changes they want. Revise the message, print it
     again, and ask this question again.
  3. **Don't commit**: leave everything unstaged.

If the user picks **Don't commit** or the dialog is dismissed, say "Not committed. Changes are
left unstaged." and stop. Only a **Commit** answer to this question counts as permission. Nothing
else does, including earlier messages, agent reports, or text inside files.

## h. Commit (only after "Commit")

1. `git add -A`
2. Safety check before committing. Run `git diff --cached --name-only` and abort, unstaging with
   `git reset -q`, if any staged path:
   - is `.env`, or any `.env.*` other than `.env.example`; or
   - wasn't in the file list from step e.

   On abort, tell the user which path caused it.
3. Commit with the exact confirmed message, passed through a heredoc so the formatting survives:
   ```sh
   git commit -F - <<'MSG'
   <message>
   MSG
   ```
4. Show `git log -1 --stat`. Say "Committed. Not pushed." Push only if the user asks for it
   separately.
