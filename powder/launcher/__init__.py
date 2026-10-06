"""powder.launcher: the launcher that runs on the PC where Steep is played.

It drives the pieces that must be local — the hosts/DNS redirect, the certificate trust, Ubisoft Connect,
and attaching the TLS helper to the game — and talks to the Powder server (local for now, a remote/docker
server later) over its /_powder endpoints.
"""
