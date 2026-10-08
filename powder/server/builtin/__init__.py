"""Replies the server builds itself, so Powder runs without a game-data pack.

A pack, when installed, still answers first; these fill the gaps:
  products.py   Steep's owned-games entries for Ubisoft Connect (Connect only starts owned games)
  sdk.py        the Ubisoft services SDK configuration of the game and of Ubisoft Connect
Only interface facts (product/app/space ids, hostnames) and values written for Powder; no copies of
Ubisoft's texts, images or configuration.
"""
