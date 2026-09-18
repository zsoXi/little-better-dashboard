# Fixtures (synthetic only)

This directory holds no real user data.

All test fixtures are synthetic and generated at test time inside
`tempfile.TemporaryDirectory` dirs with an isolated `HOME` /
`USERPROFILE` / `LOCALAPPDATA`. Checked-in files here (if any) must be
small hand-written synthetic samples. Never copy a real
`opencode.db`, real `~/.codex/sessions` rollouts, real router ledgers,
or any message content from the live machine.
