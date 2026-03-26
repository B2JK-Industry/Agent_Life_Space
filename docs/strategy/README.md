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
3. `backlog_seed.yaml` is the machine-friendly seed for future backlog
   generation, ticket import, or automation.

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
