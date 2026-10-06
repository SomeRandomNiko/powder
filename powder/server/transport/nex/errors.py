"""Exceptions a NEX handler may raise (kept separate so handlers and the dispatcher can both import them)."""


class Fallthrough(Exception):
    """A handler declines; the recorded reply (if any) is used instead."""


class RmcError(Exception):
    """Answer the call with an RMC error (name, code) instead of outputs."""

    def __init__(self, name: str = "Core", code: int = 2):
        super().__init__(f"{name}/{code}")
        self.name, self.code = name, code
