---
description: Review a pull request (or the current branch) against Spendly's code quality and security conventions
argument-hint: "[pr-number] [--comment] e.g. /review-pr 12 or /review-pr --comment (omit pr-number to review the current branch's diff against main)"
allowed-tools: Read, Grep, Glob, Bash(git:*), Bash(gh:*)
---

You are running the team's PR review pipeline for
Spendly. It reuses the same **spendly-quality-reviewer**
and **spendly-security-reviewer** subagents that back
`/code-review-feature`, but points them at a real PR (or
the current branch) instead of a curriculum step.

User input: $ARGUMENTS

## Step 1 — Parse arguments

From $ARGUMENTS extract:
- `pr_number` — optional, the leading integer if present
- `--comment` — optional flag. If present, the combined
  report will be posted as a comment on the PR at the end
  (only valid together with `pr_number` — see Step 5)

## Step 2 — Resolve the diff to review

**If `pr_number` was given:**
1. Run `gh pr view <pr_number> --json title,baseRefName,headRefName,url`
   to confirm the PR exists and get its branches. If `gh`
   is not installed or not authenticated, stop and say:
   "gh CLI is not available. Run `/review-pr` with no PR
   number to review the current branch's diff locally
   instead."
2. Run `gh pr diff <pr_number>` to fetch the full diff text.

**If no `pr_number` was given:**
1. Run `git status` and `git branch --show-current`. If
   the current branch is `main`, stop and say: "You're on
   `main` — check out a feature branch first, or pass a PR
   number: `/review-pr <pr-number>`."
2. Run `git diff main...HEAD` to get the diff text. If it's
   empty, stop and say: "No changes found between this
   branch and `main`."

Keep the raw diff text in context — do not summarize or
truncate it. Also run `git diff main...HEAD --stat` (or
the PR equivalent) to get the list of changed files.

## Step 3 — Run both reviewers in parallel

Invoke **spendly-quality-reviewer** and
**spendly-security-reviewer** in a single message with two
Agent tool calls so they run concurrently. Give each of
them the same context:

- The full diff text from Step 2 (paste it into the prompt
  — do not ask the subagent to re-run `git diff` itself,
  since a PR diff may not exist as a local working-tree
  diff)
- The list of changed files
- The PR title/URL if reviewing a PR, or the branch name
  if reviewing locally
- Instruction: review only what's in the supplied diff,
  following your existing checklist and output format
  exactly as defined in your system prompt

Wait for both to complete before continuing.

## Step 4 — Combine into one report

Produce a single combined report:

```
PR Review — <PR title/URL, or branch name>

## Quality Review
<spendly-quality-reviewer's full output>

## Security Review
<spendly-security-reviewer's full output>

## Verdict
One of:
- ✅ Looks good to merge
- 💬 A few things worth addressing first — list them
```

## Step 5 — Post as a PR comment (only if `--comment` and `pr_number` were both given)

If `--comment` was passed without a `pr_number`, tell the
user: "`--comment` needs a PR number to post to. Try:
`/review-pr <pr-number> --comment`." and stop here.

Otherwise, write the combined report from Step 4 to a
temp file and run:
`gh pr comment <pr_number> --body-file <tmp file>`

Before running it, tell the user you are about to post
this comment publicly on the PR and show them the report
first — this is a visible, hard-to-reverse action.

## Handoff Rules

- Do NOT attempt to fix any code found in review — this
  command only reports
- Do NOT run the reviewers sequentially — they must run in
  parallel in one message
- Do NOT post a PR comment without the explicit `--comment`
  flag

---

## Usage examples

- `/review-pr 12` — review PR #12 via `gh`, print the
  combined report, do not comment
- `/review-pr 12 --comment` — same, then posts the report
  as a comment on PR #12
- `/review-pr` — review the current branch's diff against
  `main` locally (no `gh` required, nothing is posted)
