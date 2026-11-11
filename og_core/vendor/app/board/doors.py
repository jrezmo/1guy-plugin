"""doors: the two-door first run (docs/product.md, "The first 60 seconds"). Which door a home opens
on is picked from what the home holds, never asked.

    pick(board)   -> "a" | "b" | None
    apply(board)  -> sets board["first_run"] and board["door"] (his words for that door); returns it

    "a"   a lived-in Hermes: a handful of chats in the last two weeks, or any scheduled job. He went
          through it: the counted opener, the three that need you, the piles (board/untangle.py).
    "b"   a sparse one: nothing to go through, so he asks. `what are you in the middle of?`
          Each line the user writes is a to-do (POST /api/board/task op=add, the same path the talk
          bar's "add to my list" uses); untangle runs over them as over anything else.
    None  the first run is over: the user has made a judgment (any line in the ledger).

Reads the board it is handed (blocks + _history, both already read by the sources) and the judgment
ledger under ONEGUY_HOME. No model, no network, writes nothing. Stdlib only.
"""
from . import ledger, untangle

HANDFUL = 5        # this many chats in two weeks is a lived-in Hermes; so is any scheduled job
PILE_AFTER = 3     # door B: untangle has something to say once this many lines are down

WORDS = {
    "a": {"close": "pick one and i'll take it from here."},
    "b": {"ask": "what are you in the middle of?", "hint": "one thing per line. i'll sort it.",
          "close": "want me to start on one?", "sample": "look around a sample desk",
          "pile_after": PILE_AFTER},
}


def lived_in(board):
    f = untangle.facts(board)
    return f["jobs"] >= 1 or f["chats"] >= HANDFUL


def judged():
    """The user has made a first judgment: the first run is over."""
    return bool(ledger.tail(1))


def pick(board):
    if judged():
        return None
    return "a" if lived_in(board) else "b"


def apply(board, which="unset"):
    """Put the door on the payload. Door B has nothing to count, so his opener is the question."""
    which = pick(board) if which == "unset" else which
    board["first_run"] = which
    board.pop("door", None)
    if which:
        board["door"] = dict(WORDS[which], which=which)
    if which == "b":
        br = board.setdefault("briefing", {"for_you": [], "broke": []})
        br.update(line=WORDS["b"]["ask"], by="door")
    return which
