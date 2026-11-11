# Releasing 1guy

The catalog entry pins one exact commit, so the order below matters: any change after step 3,
even to the README, means a new commit id and starting again from step 3.

1. **Update the canvas and version.** If the canvas changed, rebuild it with
   `python3 scripts/vendor_canvas.py <ref>` (it stops if anything private is left in the copy).
   Bump `version` in `plugin.yaml`, `desktop/plugin.js` (two places) and `catalog/1guy.yaml`
   together.
2. **Check.**
   - `.venv/bin/pytest -q`
   - `hermes plugins validate . --install-deps` (expect zero warnings)
   - `tests/stranger_install.sh`, three ways: plain, `STRANGER_HOME=fresh`, and
     `STRANGER_BUILDER=<builder for a lived-in home>`

   Then commit.
3. **Make the release commit.** `scripts/make_public.sh` builds the local branch `public` as a
   single commit with the current tree, and writes `.release/SHA`, `.release/1guy.yaml` and
   `.release/PR.md` with that commit id filled in. Re-running it on the same tree gives the same
   id.
4. **Test that exact commit.** `STRANGER_REF=public STRANGER_HOME=fresh tests/stranger_install.sh`
   installs the release commit on an empty Hermes and drives it in a browser.
5. **Push.** `git push <remote> public:main`, then confirm the commit id on GitHub matches
   `.release/SHA`.
6. **Open the catalog PR** with `.release/1guy.yaml` as `plugin-catalog/1guy.yaml` and
   `.release/PR.md` as the description. The screenshot links point at that commit, so they only
   resolve after step 5.

## Upgrading an install that used the old id (`oneguy`)

Run `scripts/migrate_from_oneguy.sh` on each machine: it is a dry run by default; add `--apply`
to make the change, or `--desktop-only` on a machine that only runs the desktop app.
`tests/migration_proof.sh` demonstrates it on a scratch home.

## Not covered by the checks above

The plugin inside the real Hermes Desktop app, a real model writing headlines and briefs, and a
real `hermes cron create` for the mail/calendar job (the tests stub that command). Open the page
once in the real app before submitting.
