"""Email + calendar through the user's own Hermes: the thin HTTP face of app/board/mailcal.py.

  GET  /api/board/mailcal             -> {can: {email, calendar, via}, job, as_of, stale, error, declined}
  POST /api/board/mailcal {op}        op "setup"   : the user's yes. Creates the `1guy-mail-cal` Hermes
                                                     cron job, then runs it once. A judgment in the ledger.
                                      op "not-now" : remembered; the offer stops asking.

All logic is in app/board/mailcal.py (vendored into the plugin). The one thing this file adds is the
runner: the subprocess call that executes the two disclosed commands
    hermes cron create '0 7-22 * * *' '<mailcal.PROMPT>' --name 1guy-mail-cal --deliver local
    hermes cron run <job id>
The plugin supplies its own runner the same way (mailcal.runner = ...).
"""
import os
import subprocess

import board as _board            # app/board/ (app/ is on sys.path under serve.py)
from board import mailcal as _mailcal

ALLOWED = (("hermes", "cron", "create"), ("hermes", "cron", "run"))


def run(argv, timeout=60):
    """runner(argv) -> (returncode, output). Only the two commands above ever get through."""
    if tuple(argv[:3]) not in ALLOWED:
        return 1, "not a command this route runs"
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
