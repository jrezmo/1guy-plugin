"""Email + calendar through the user's own Hermes: the thin HTTP face of app/board/mailcal.py.

  GET  /api/board/mailcal             -> {can: {email, calendar, via}, job, as_of, stale, error, declined}
  POST /api/board/mailcal {op}        op "setup"   : the user's yes. Creates the `1guy-mail-cal` Hermes
                                                     cron job, then runs it once. A judgment in the ledger.
                                      op "not-now" : remembered; the offer stops asking.

All logic is in app/board/mailcal.py (vendored into the plugin). The one thing this file adds is the
runner: the subprocess call that executes the three disclosed commands, only ever on the one
`1guy-mail-cal` job this route created:
    hermes cron create '0 7,12,17 * * *' '<mailcal.PROMPT>' --name 1guy-mail-cal --deliver local
    hermes cron edit <job id> [--prompt '<mailcal.PROMPT>'] [--schedule '0 7,12,17 * * *']
    hermes cron run <job id>
`edit` brings a job made by an older version up to date; `run` is the user's yes, or a look at a
board whose digest is over an hour old (at most once an hour, mailcal.freshen).
The plugin supplies its own runner the same way (mailcal.runner = ...).
"""
import os
import subprocess

import board as _board            # app/board/ (app/ is on sys.path under serve.py)
from board import mailcal as _mailcal

ALLOWED = (("hermes", "cron", "create"), ("hermes", "cron", "edit"), ("hermes", "cron", "run"))


def run(argv, timeout=None):
    """runner(argv) -> (returncode, output). Only the three commands above ever get through.
    `cron run` is synchronous (a whole agent turn), so it gets minutes, not a minute."""
    if tuple(argv[:3]) not in ALLOWED:
        return 1, "not a command this route runs"
    if argv[2] in ("edit", "run") and (len(argv) < 4 or argv[3] != (_mailcal.find_job() or {}).get("id")):
        return 1, "only the 1guy-mail-cal job"
    timeout = timeout or (_mailcal.RUN_TIMEOUT_S if tuple(argv[:3]) == ("hermes", "cron", "run") else 60)
    env = dict(os.environ, HERMES_HOME=os.environ.get("HERMES_HOME") or _mailcal.hermes_home())
    try:
        p = subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout, env=env,
                           stdin=subprocess.DEVNULL, cwd=os.path.expanduser("~"))
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    except OSError as e:
        return 127, str(e)


if _mailcal.runner is None:
    _mailcal.runner = run


def get_mailcal(query, body):
    return _mailcal.status()


def post_mailcal(query, body):
    res, status = _mailcal.action(body)
    if res.get("ok"):
        _board.invalidate("jobs")
        _board.invalidate("widgets")
    return res, status


ROUTES = {
    ("GET", "/api/board/mailcal"): get_mailcal,
    ("POST", "/api/board/mailcal"): post_mailcal,
}
