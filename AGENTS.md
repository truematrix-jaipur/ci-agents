# AGENTS.md instructions for /home/agents

<INSTRUCTIONS>
# Codex Working Rules for `/home/agents`

These instructions apply whenever Codex works in this directory tree.

## Git Best Practices (Required)

1. Start by checking repository state:
   - `git status --short`
   - `git branch --show-current`

2. Never commit directly to `main`/`master`.
   - Create or use a task branch for every change.

3. Keep commits focused and small.
   - One logical change per commit.
   - Avoid bundling unrelated edits.

4. Do not rewrite published history.
   - No `git push --force` on shared branches.
   - No rebasing shared remote branches unless explicitly requested.

5. Do not discard user changes.
   - Never run destructive commands (for example `git reset --hard`, `git checkout -- <file>`) unless explicitly requested.

6. Before committing, run relevant validation.
   - At minimum run targeted tests/lint for touched code.
   - If validation cannot run, clearly document that in the final report.

7. Commit message quality:
   - Use clear, imperative messages.
   - Explain what changed and why.

8. Keep working tree clean after task completion where possible.
   - Confirm with `git status`.

9. For reviews/PR-ready changes, include:
   - Summary of modified files.
   - Risks/assumptions.
   - Commands used for validation.

## Safety

- Prefer reversible edits.
- Ask before any risky or environment-wide operation.

</INSTRUCTIONS>
