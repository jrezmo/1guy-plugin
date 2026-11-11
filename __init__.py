"""1guy: one companion surface on top of Hermes (board + talk bar + one thread per item).

The plugin's surfaces are the desktop half (desktop/plugin.js) and the dashboard backend
(dashboard/plugin_api.py -> og_core). The native entry point registers no tools, hooks or
commands; it only hands the backend the host-owned LLM door (ctx.llm), which the board uses
for plain-words headlines and the talk bar uses to route a typed line.
"""
import os
import sys


def register(ctx) -> None:  # noqa: ANN001 (the real PluginContext, duck-typed)
    root = os.path.dirname(os.path.abspath(__file__))
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from og_core import talk
        talk.set_llm(ctx.llm)
    except Exception:
        # The backend still works without headlines; never break plugin loading.
        return None
    return None
