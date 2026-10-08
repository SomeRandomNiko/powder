"""Replies the server builds itself, so Powder runs without a game-data pack.

A pack, when installed, still answers first; these fill the gaps:
  products.py, product_table.py   Steep's owned-games entries for Ubisoft Connect (Connect only starts owned
                                  games): product/add-on/branch ids, and a product configuration written here
  sdk.py                          the Ubisoft services SDK configuration of the game and of Connect: which
                                  SDK features are on, and the service URL templates on Ubisoft's hostnames
  http.py                         the game's other start replies (item list version, DLC item mappings,
                                  event types, challenges), and Connect's cached UI settings left alone
NEX replies the pack used to hold are in transport/nex/handlers/gamestorage.py.
Only interface facts (ids, hostnames, URL templates, feature names, the item list's fingerprint) and values
written for Powder; no copies of Ubisoft's texts, images or catalogues.
"""
