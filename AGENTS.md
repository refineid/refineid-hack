# Repository instructions

## What this repository is

A scratchpad of hacks, not a product. Shortcuts the RefineID product
repositories forbid are allowed here: do not file them as findings (a leak of a
PIN or PUK is the exception, below), and do not rewrite code to product standard
unless asked. One rule about secrets holds: nothing here may actively leak a PIN
or PUK, by any route. That includes printing or logging one, sending it to
another machine or service, writing it to a file or shell history, and
committing it. A PIN or PUK may be handed only to the local program that talks
to the card. How commits are made is not relaxed: the hooks in `githooks/`
apply. See "Rules of this repository" in the README.

## Worktree policy

- Reading does not require a worktree; writing does. The main checkout is
  never edited directly. Before writing code or modifying files for any task,
  create a dedicated git worktree strictly under `../wt/`
  (e.g., `../wt/refineid-hack-<topic>`) on a `<topic>` branch. Never
  work directly on `main` or modify files in the main checkout.
- All worktrees MUST live strictly under `../wt/`, never loose beside
  repositories or in parent directories.
- Never put a git worktree under `/tmp`. It is cleared on reboot and takes the
  branch's only checkout with it.
- One task, one worktree, one branch (`<topic>`), one pull request per branch.
- Merge the pull request once local verification passes, and the review
  too when one ran; then remove the worktree and branch and fast-forward
  `main`.

## Source comments

- Comments explain what the code does now and the constraints it honors.
  Past bugs, previous implementations, and explanations of what a fix changed
  belong in commit messages, not source comments.

## Commits and integration

- Commits are cheap backups. Make small, focused commits often, without
  asking for permission, once the required commit checks pass.
- Complete the integration without waiting for another instruction: push
  the task branch, open a pull request, and merge it into `main` once the
  required checks pass. Sync local `main` with the merged remote.
  Use squash merges to keep the `main` history linear; do not use merge commits.

## File bugs when problems are seen

- Standing instruction: as soon as you observe a confirmed bug, broken
  tool, documentation defect, or secret-handling flaw (outside the
  product-standard shortcuts and throwaway experiments allowed by "What
  this repository is" above), file a GitHub issue (`gh issue create`,
  with `-R <owner>/<repo>` when filing from another checkout) immediately
  in the owning repository with sanitized evidence, without waiting for
  user permission or the end of the task, whether the fix will happen next
  or in a later branch (following the secrecy, personal-data, and
  security-reporting rules below). Do not file issues for defects in the current branch's own
  unmerged changes; fix those in the branch.
- Search existing open issues first. Reuse the matching issue and add only
  new, useful evidence instead of creating a duplicate. Group findings only
  when they share a cause and can be resolved by one focused change.
- State the observed behavior, expected behavior, affected repository-relative
  paths, reproduction or inspection evidence, impact, and acceptance checks.
  Distinguish observations from hypotheses and specification requirements.
  Never claim an unexecuted test or hardware operation was verified.
- Keep speculative improvements in working notes until they have a concrete
  problem and useful acceptance criteria. Avoid issue spam and severity claims
  unsupported by evidence.
- Never put credentials, PIN data or candidate lengths, card secrets,
  personal data, private workspace paths, or persistent device identifiers
  in issue text, logs, screenshots, attachments, or reproduction fixtures.
  Report security-sensitive findings through the repository's private
  reporting process; if no safe channel is available, notify the user
  without publishing sensitive details.
- An issue does not excuse a broken gate or incomplete work needed to make
  the current task correct. Fix findings required for the task before handing
  it over; file independently deferred work with a clear scope.
- Link newly filed or reused issues in the task handoff. If issue creation is
  unavailable, preserve a sanitized finding locally and report that it was
  not filed; never silently discard it.

## No backwards compatibility

- When code, a script, a command, an API, a file format or a setting is
  replaced, remove the old one in the same change. Do not leave
  compatibility wrappers, deprecated aliases, forwarding shims, fallback
  readers or migration paths behind.
- Update every caller, hook, CI job and document to the replacement in that
  same change instead of keeping the old entry point alive for them.

## Independent review

- Review is optional here: the agent decides whether a change needs
  one, for example code that parses what a card, a network, or a radio
  sends. Once a review is required, use a premier frontier model from
  a family distinct from every family that authored any part of the
  diff; deep reviews -- protocol, cryptography, concurrency,
  state-machine, security-critical, signing, or shared cross-platform
  contract changes (and any change whose tier is unclear) -- require
  at least two reviewers from distinct non-author families, running
  all available eligible premier families in parallel.
- Premier reviewers (as of 2026-11; pick families distinct from every
  authoring family at the strongest available reasoning tier):
  - **Anthropic**: `agy` with `claude-opus-5-5-high`
    (Claude Opus 5.5 / High).
  - **Google**: `agy` with `gemini-4-carbon-high` (or
    `gemini-4-neon-xhigh` / `gemini-4-neon-high` if ranked higher).
  - **OpenAI**: `codex exec` with `--model gpt-6.2-astra
    -c model_reasoning_effort=xhigh` (GPT-6.2-Astra / xhigh;
    maintainer-authorized for this project when installed and within
    quota).
- Frontier rankings and CLI catalogs evolve over time; agents may
  check <https://artificialanalysis.ai> alongside each CLI's model
  list (for example `agy models` or Codex's available models) to pick
  the strongest currently available frontier model per family instead
  of using weaker reviewers while premier models are available.
- Filter candidates first by family independence, authorization, and
  availability, and fill required slots from eligible premier families
  first. Use fallback harnesses -- `claude`, `opencode`, or `muse` --
  only per unfilled required slot when no eligible premier family
  remains available due to missing CLI, authorization, quota, outage,
  or same-family exclusion, selecting the strongest listed model whose
  training organization differs from every authoring family and from
  every reviewer family already counted for the diff. If a change
  needs review and the required independent reviewers cannot be
  obtained, stop and report to the owner instead of merging unreviewed.
- A model family is the organization that trained the model (human-only
  authorship excludes no family). When a reviewer's family is
  uncertain, treat it as same-family and skip it; if an AI author's
  family is uncertain, ask the owner.
- Run every reviewer headless from an empty scratch directory,
  supplying the diff in the prompt (reading any prompt file into a
  variable or stream and checking that the read succeeded before
  launching) and explicitly instructing it not to use any tools. Use
  each CLI's strictest read-only sandbox and plan/no-edit mode -- for
  example, on POSIX shells `command agy --sandbox --mode plan
  --disable-slash-commands --model <model> -p "$prompt"` and
  `command codex exec --ignore-user-config --model gpt-6.2-astra
  -c model_reasoning_effort=xhigh --ephemeral --skip-git-repo-check
  --sandbox read-only --json -o OUT_FILE - < PROMPT_FILE`; on Windows
  PowerShell resolve executables directly via `& (Get-Command agy
  -CommandType Application)` with `-p (Get-Content -Raw -ErrorAction
  Stop PROMPT_FILE)` or pipe `Get-Content -Raw -ErrorAction Stop
  PROMPT_FILE | & (Get-Command codex -CommandType Application) exec
  ... -`. Always bypass shell aliases or functions that add
  `--dangerously-skip-permissions`,
  `--dangerously-bypass-approvals-and-sandbox`, `--full-auto`, or
  `--yolo`, never grant write or auto-approval permissions, and frame
  the request as a code review from the project's author.
- Check every finding against the code. Fix the real ones and list the
  rejected ones, with the reason, in the pull request description.
