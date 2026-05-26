# CLAUDE.md

Project rules for Claude / coding agents working in this repo.

## Rules

1. **Use the latest package versions and pin them exactly.** When adding a
   dependency to `pyproject.toml` (or any other manifest), look up the latest
   stable release and pin it with `==` (Python) or the equivalent exact-version
   operator for the ecosystem. No `>=`, no `~=`, no caret ranges. Lockfiles
   (`uv.lock`, etc.) are committed.
