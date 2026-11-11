"""og_core: the plugin's backend. Four deep modules behind dashboard/plugin_api.py:

  store     where everything lives (Hermes home, the plugin data dir, optional-source settings)
  tasklist  the shared markdown tasklist
  board     sources -> blocks: the vendored 1guy board, pointed at this Hermes install
  talk      the talk bar, item threads, and agent turns relayed through the desktop app
"""
