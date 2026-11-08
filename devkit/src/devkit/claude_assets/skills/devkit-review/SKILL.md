---
name: devkit-review
description: Review your uncommitted changes with independent reviewer subagents, check every finding, fix only the obvious ones, and list the rest for discussion. Use when devkit's stop hook asks for a review round, or when the user asks for a devkit review (/devkit-review).
---

Run one review round of devkit's review loop. devkit's stop hook decides when a round is needed and
how many run; you do the round. Independent reviewers look at the change, a checker confirms each
finding, you fix only what is obviously right, and everything else goes to the user.

## 0. The session

- **Sent by the stop hook:** its message names the round and a `--session <id>`. Pass that same
  `--session <id>` to every `devkit review` command below.
- **On demand** (the user asked, or ran `/devkit-review`): start with
  `devkit review plan --start --session ${CLAUDE_SESSION_ID}` and use the `session` it prints from
  then on. If that `--session` value is still a placeholder in braces rather than an id, leave
  `--session` out. If it says there is nothing to review, tell the user so and stop.

If the user asks you to skip the review, run `devkit review skip --session <id> --reason "<their words>"` and
stop.
Only skip when the user asks, never on your own judgment.

## 1. Plan and scope

Run `devkit review plan --session <id>`. It prints JSON: `round`, `max`, `session`, `reviewers` (focus
to model), `checker` (a model) and `scope` (the command that prints this round's diff). Round 1
covers everything changed since the last review; later rounds cover only the previous round's fixes.

Run the `scope` command yourself and read the diff, then gather what the reviewers need:
- **Intent:** one to three sentences on *what* the change does. Never *why* it is correct, what you
  tested, or what you are unsure about.
- **Rule files:** the paths that exist of `AGENTS.md` and `CLAUDE.md` at the repo root and in the
  directories of the changed files, and `.devkit.yaml`. If the repo has an `openspec/` directory, run
  `openspec list --json` and add the directory of each change in progress
  (`openspec/changes/<name>/`). Paths only: never paste or summarise the files.

## 2. Reviewers, in parallel

Start one subagent per focus in `reviewers`, all in the same message so they run in parallel:
- `subagent_type`: `devkit-review-<focus>`, for example `devkit-review-correctness`;
- `model`: that focus's model from `plan`. For `inherit`, leave `model` out;
- in the foreground: never set `run_in_background`, and wait in this turn for every result;
- never a fork of this conversation: each reviewer starts fresh.

If those agent types don't exist, tell the user to run `devkit install --agent claude` and restart,
and stop without recording the round.

Each reviewer's prompt is exactly this, filled in, and nothing else. No part of this conversation,
no code you pasted, no opinion on the change:

```
Intent: <the intent>
Scope: run `<the scope command from plan>` to see the change under review.
Rule files to read yourself: <the rule file paths, comma-separated>
These don't count as findings; leave them out:
- issues that existed before this change
- issues on lines the change didn't touch
- anything a linter, type checker or the tests would catch
- nitpicks: style, naming, wording, formatting
- changes that are clearly intentional
- rules the code silences on purpose (a noqa, an exemption, a documented exception)
Reply with the JSON array your instructions describe.
```

## 3. Collect and drop findings without evidence

Each reviewer replies with a JSON array of findings: `focus`, `file`, `line`, `quote`, `claim` and
`failure_scenario`. Add `model`, the focus's model from `plan`, to each.

- A finding missing a file, line, quote or failure scenario is **dropped** with the reason
  `no evidence`. It is not checked or fixed. If it has no claim either, give it a short claim saying
  what it was about.
- Merge duplicates: two findings about the same problem at the same place. Keep the one with the
  better evidence; the other is **dropped** with the reason `duplicate`.
- A reviewer that failed or replied with something other than findings: say so in your final
  message.

## 4. Check every remaining finding

For each remaining finding, start a `devkit-review-checker` subagent with `model` set to `checker`
from `plan`, in the foreground as in step 2. Start at most 10 at a time, in one message, and the next
batch once those finish. Its prompt is the finding's JSON and the scope command, nothing else: not
the reviewer's reasoning, not your view of it.

The checker replies with `{"score": ..., "reason": ...}`. Keep the score on the finding:
- **75 or more:** confirmed. Go on to step 5.
- **50 to 74:** a **question**. It is shown to the user as a question, never as a fact, and never
  fixed.
- **Below 50:** **dropped**, with the reason `checker: <score>`.

## 5. Fix only the obvious findings

A confirmed finding is **obvious** only when all of these hold:
- the fix is local: a few lines in one place;
- it has one evident correct form, which anyone reading the finding would write;
- it changes no behaviour beyond the defect;
- an existing test covers it, or you add one that fails without the fix.

Fix each obvious finding, outcome **fixed**. Every other confirmed finding is **discuss**: one that
needs an API or interface change, has more than one reasonable fix, changes other behaviour, can't be
tested, or that you disagree with. If you disagree with a finding the checker confirmed, it stays
**discuss** with your objection; never drop it.

After fixing, re-run the tests that cover the changed code. If a fix breaks them and the cause isn't
obvious, undo that fix and make the finding **discuss**.

Never commit, push, stage (`git add`), stash, or reset anything. The fixes stay in the working tree
for the user and for the next round.

## 6. Record the round

Write every finding, dropped ones included, as one JSON array to a file in your scratchpad or temp
directory, **outside the repo**: a file in the repo would itself become part of the next round.

```json
[{"focus": "correctness", "model": "opus", "file": "src/app.py", "line": 12,
  "quote": "...", "claim": "...", "failure_scenario": "...", "score": 75, "outcome": "fixed"},
 {"focus": "tests", "model": "sonnet", "claim": "...", "outcome": "dropped", "reason": "no evidence"}]
```

`outcome` is `fixed`, `discuss`, `question` or `dropped`, and a dropped finding needs a `reason`. A
finding that wasn't checked has no `score`. A round with no findings is an empty array `[]`.

Then run `devkit review record --session <id> < <that file>`. Always redirect the file: never put the
JSON in the command line, a heredoc or `echo`, since quoted code breaks the shell quoting. If
`record` rejects the file, fix what it names and run it again. It prints the counts and the ids of
the findings left open (`discuss` and `question`), in the order they appear in the file.

## 7. Report

End your reply with the round's result, in this form:

**Review round N of M**

- **Fixed:** each finding as `file:line`, what was wrong, and what you changed. Then the tests you
  ran and whether they passed.
- **To discuss:** each open finding with its id from `record` (`f-...`), `file:line`, the quoted
  code, the claim and the failure scenario. Mark questions as *Question (unconfirmed)*. Add your
  objection where you have one.
- **Dropped: N**, with the reasons grouped, for example `no evidence: 2, checker score below 50: 3, duplicate: 1`.

Then stop. Don't start the next round yourself: the stop hook asks for it when your fixes need a
review, and ends the loop otherwise.

## 8. Verdicts

When the user later accepts or rejects a finding from a To discuss list (agreeing to a fix counts as
accepting), run `devkit review verdict <finding id> accepted` or
`devkit review verdict <finding id> rejected`, adding `--note "<their reason>"` when they gave one.
This shows over time which reviewers and models make good findings.
