---
name: verified-step-commit
description: Create a git commit (never push) after each verified roadmap step in the GUAI project. Use after the user confirms a milestone step works, or when finishing a roadmap step they have reviewed.
---

# Commit after each verified step

In this repository, work is organized as small roadmap steps in `spec/roadmap.md`. After a step
is implemented **and the user has verified it works** (emulator run, device check, or explicit
confirmation), create a git commit. This protects work-in-progress — source files here have
been lost before because steps stayed uncommitted for too long.

## Rules

1. **Only commit after verification.** Ask or wait for the user's confirmation that the step
   works before committing. Do not commit unverified or in-progress changes.
2. **Never push.** `git commit` only — pushing happens deliberately by the user (releases are
   tag-driven).
3. **Commit the whole step.** Include every file the step touched (server, watchface, app,
   frontend, scripts, resources). Generated artifacts under `watchface/resources/images/` are
   committed too — they are deterministic outputs of `scripts/gen_watchface_icons.py` and the
   build depends on them.
4. **Message style:** one plain sentence, matching the existing history, e.g.
   `Added icon pack, weather panel slot, configurable panel and light/dark theme to the
   watchface (steps 10.1-10.7)`. Mention the roadmap step numbers in parentheses.
5. **One commit per step.** Do not batch multiple verified steps into one commit; do not split
   a single step into several commits.
6. If `spec/roadmap.md` is tracked at the time of the commit, include its status updates for
   that step. (Note: `spec/` and `docs/` are currently git-ignored by the user's choice — do
   not force-add them.)

## Reference

- Roadmap with step numbering and exit gates: `spec/roadmap.md`
- Emulator smoke-test workflow: `just watch-emulate` / `just app-emulate` / `just watch-config`