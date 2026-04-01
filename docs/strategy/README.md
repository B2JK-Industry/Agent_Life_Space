# Strategy Docs

This folder is the durable source of truth for long-range product and
architecture direction.

Why this location:
- It lives in the repo, so it is versioned and reviewable.
- Claude Code can read it directly from the workspace.
- It is less fragile than chat history or ad-hoc notes.
- It is better than GitHub Wiki for canonical planning because changes travel
  with the codebase and commit history.

Recommended usage:
1. `MASTER_SOURCE_OF_TRUTH.md` is the canonical strategy and architecture
   document.
2. `THEMES_EPICS_STORIES.md` is the human backlog decomposition.
3. `BACKLOG_PROGRESS.md` is the current execution snapshot against the strategy.
4. `BACKLOG_REVIEW_AGAINST_MASTERPLAN.md` is the gap analysis between backlog
   and masterplan.
5. `NEXT_BACKLOG.md` is the near-term prioritized execution backlog derived
   from the current state of `main`.
6. `AS_IS_TO_BE_2026_04_01.md` is the archival snapshot of the 2026-04-01
   merge and post-merge closure event that brought `main` to the current
   `v1.28.1` baseline.
7. `backlog_seed.yaml` is the machine-friendly seed for future backlog
   generation, ticket import, or automation.
8. `prompts/` contains durable Claude Code task prompts derived from current
   architectural findings.

Recommended governance:
- Keep these files on `main`.
- Protect them with normal code review.
- Treat edits as architectural decisions, not casual notes.
- When a major strategy change happens, update these docs first and only then
  generate backlog changes.

Recommended next storage step:
- Commit and push these files to GitHub on `main`.
- Optionally tag the commit with a planning marker such as
  `strategy-baseline-v1`.
