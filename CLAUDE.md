# WEALTHWATCH — project guide for Claude Code

Part of the PlainSpoken Foundry Nine (PF9) app fleet. The fleet-wide protocol also lives
in the parent `PlainSpokenFoundryNine/CLAUDE.md`; this repo-local copy makes it travel
with the repo.

## Track all work in MAILR (system of record)

MAILR is the system of record for every PF9 app. The `mcp__mailr__mailr_*` tools are
available to you — use them; don't track work only in your head or in local files.

- **Work board:** tag every unit of work `app: "WEALTHWATCH"`. Add with `mailr_add_work`,
  move with `mailr_update_work`, review with `mailr_list_work app="WEALTHWATCH"`.
- **Status flow:** Backlog -> In Progress -> Blocked (note who/what you're waiting on) ->
  Shipped. Mark Shipped only after **prod-verified by identity/PID change** (a 200 can't
  tell old code from new), not just green tests. Log **`HOURS: n.n`** + the commit hash in
  the Shipped note; never delete Shipped history.
- **Deploy:** most PF9 apps ship with `pf9-deploy wealthwatch` (ships the WORKING TREE —
  commit first); verify by identity/PID change. Check this app's memory file for its exact
  deploy/restart/verify steps (a few apps use their own script or rsync).
- **Outgoing email:** call `mailr_pending_outgoing` first; send from
  support@plainspokenfoundrynine.com; log with `mailr_remember` after. Never send without
  Mark's explicit go-ahead; verify recipients first.
- **Audit before push:** review the real diff (bugs, edge cases, escaping, over-inclusion)
  and fix forward — green tests are necessary, not sufficient.
