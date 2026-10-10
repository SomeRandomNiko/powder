"""Plain Tkinter window: one row per step with its state, text and buttons; Play (one button per installed
Steep version) / Restore system / Logs / Quit.
Deliberately simple (functionality first)."""
from __future__ import annotations

import ctypes
import queue
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from ..version import __version__
from . import account, network
from .session import ACTION, FAILED, HOST, JOIN, LOCAL, OK, PENDING, RUNNING, Session, play_buttons

ICONS = {PENDING: "·", RUNNING: "…", OK: "✔", ACTION: "?", FAILED: "✖"}
COLORS = {PENDING: "gray", RUNNING: "blue", OK: "green", ACTION: "darkorange", FAILED: "red"}


MUTEX_NAME = "PowderLauncher"          # the installer checks it (AppMutex) to ask for Powder to be closed


def _hold_mutex():
    """A named mutex for as long as the window is open; None outside Windows."""
    try:
        return ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
    except AttributeError:
        return None


class AccountDialog(tk.Toplevel):
    """A small modal form for the account: register, sign in, change email / password, rename."""
    FORMS = {
        "register": ("Register", [("email", "Email", False), ("password", "Password (8+ characters)", True),
                                  ("password2", "Password again", True), ("name", "Player name", False)]),
        "sign_in": ("Sign in", [("email", "Email", False), ("password", "Password", True)]),
        "change_email": ("Change email", [("new_email", "New email", False), ("password", "Password", True)]),
        "change_password": ("Change password", [("password", "Current password", True),
                                                ("new_password", "New password (8+ characters)", True),
                                                ("password2", "New password again", True)]),
        "rename": ("Rename", [("name", "New player name", False), ("password", "Password", True)]),
    }

    def __init__(self, parent, kind: str, info: dict):
        super().__init__(parent)
        title, fields = self.FORMS[kind]
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.result: dict | None = None
        self.kind = kind
        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both")
        note = ""
        if kind == "register":
            note = ("The email and password are also what you sign in to Ubisoft Connect with.")
            if info.get("takes_over"):
                note += (f"\nThis is the first account on the server: it takes over the player "
                         f"{info['takes_over']} and its progress (leave the name empty to keep it).")
        if note:
            tk.Label(frame, text=note, justify="left", wraplength=360).grid(row=0, column=0, columnspan=2,
                                                                          sticky="w", pady=(0, 8))
        self.vars = {}
        for row, (key, label, secret) in enumerate(fields, 1):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=2)
            var = tk.StringVar(value=info.get(key, "") if key in ("email",) else "")
            ttk.Entry(frame, textvariable=var, show="•" if secret else "", width=32).grid(row=row, column=1, pady=2)
            self.vars[key] = var
        bar = ttk.Frame(frame)
        bar.grid(row=len(fields) + 1, column=0, columnspan=2, sticky="e", pady=(8, 0))
        ttk.Button(bar, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(bar, text=title, command=self.ok).pack(side="right", padx=6)
        self.bind("<Return>", lambda e: self.ok())
        self.bind("<Escape>", lambda e: self.destroy())
        self.grab_set()

    def ok(self) -> None:
        values = {k: v.get() for k, v in self.vars.items()}
        if "password2" in values:
            first = values.get("new_password", values.get("password"))
            if values.pop("password2") != first:
                messagebox.showerror("Account", "The two passwords differ.", parent=self)
                return
        if any(not v.strip() for k, v in values.items() if k != "name") or (
                self.kind == "rename" and not values["name"].strip()):
            messagebox.showerror("Account", "Please fill in every field.", parent=self)
            return
        self.result = values
        self.destroy()

    @classmethod
    def ask(cls, parent, kind: str, info: dict) -> dict | None:
        d = cls(parent, kind, info)
        parent.wait_window(d)
        return d.result


class LauncherWindow:
    def __init__(self, session: Session, restore_only: bool = False):
        """restore_only: just Restore system (the uninstaller runs this); no Play buttons."""
        self.session = session
        self.restore_only = restore_only
        self.mutex = _hold_mutex()
        self.root = tk.Tk()
        self.root.title(f"Powder {__version__}" + (" - Restore system" if restore_only else ""))
        self.root.geometry("860x660")
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        session.on_update = lambda: self.root.after(0, self.render)

        top = ttk.Frame(self.root, padding=8)
        top.pack(fill="x")
        self.play_bar = ttk.Frame(top)                  # one Play button per installed version
        self.play_bar.pack(side="left")
        self.shown_buttons: list[str] | None = None
        if not restore_only:
            ttk.Button(top, text="Restore system…", command=session.restore).pack(side="left", padx=6)
        ttk.Button(top, text="Open logs", command=session.open_logs).pack(side="left")
        ttk.Button(top, text="Close" if restore_only else "Quit", command=self.quit).pack(side="right")

        self.server_bar = ttk.Frame(self.root, padding=(8, 0, 8, 4))  # this PC only / host / join
        self.account_bar = ttk.Frame(self.root, padding=(8, 0, 8, 4))
        self.pack_bar = ttk.Frame(self.root, padding=(8, 0, 8, 4))   # the game-data pack, set up apart from Play
        if not restore_only:
            self.server_bar.pack(fill="x")
            self.account_bar.pack(fill="x")
            self.pack_bar.pack(fill="x")
            self.use_pack = tk.BooleanVar(value=True)
            self.mode = tk.StringVar(value=session.mode())
            self.join_address = tk.StringVar(value=session.join_address())
            self.host_address = tk.StringVar(value=session.host_address())
            self.shown_account = None
            self.render_server()
            self.render_account()
            self.render_pack()
            session.dialog = self.dialog_from_worker

        self.body = ttk.Frame(self.root, padding=8)
        self.body.pack(fill="both", expand=True)
        self.render()
        if restore_only:
            self.root.after(100, session.restore)

    # ---- where the server is ----------------------------------------------------------------------
    def render_server(self) -> None:
        """Built once; mode_changed only updates it (rebuilding would swallow clicks on it)."""
        bar = self.server_bar
        tk.Label(bar, text="Server", font=("Segoe UI", 10, "bold")).pack(side="left", padx=(0, 8))
        ttk.Radiobutton(bar, text="Just this PC", value=LOCAL, variable=self.mode,
                        command=self.mode_changed).pack(side="left", padx=(0, 6))
        ttk.Radiobutton(bar, text="Host for others", value=HOST, variable=self.mode,
                        command=self.mode_changed).pack(side="left")
        self.host_box = ttk.Combobox(bar, textvariable=self.host_address, width=15, state="readonly")
        self.host_box.pack(side="left", padx=(2, 8))
        self.host_box.bind("<<ComboboxSelected>>", lambda e: self.mode_changed())
        ttk.Radiobutton(bar, text="Join:", value=JOIN, variable=self.mode,
                        command=self.mode_changed).pack(side="left")
        entry = ttk.Entry(bar, textvariable=self.join_address, width=18)
        entry.pack(side="left", padx=(2, 0))
        entry.bind("<FocusIn>", lambda e: self.mode.set(JOIN))
        entry.bind("<FocusOut>", lambda e: self.mode_changed())
        entry.bind("<Return>", lambda e: self.mode_changed())
        self.server_note = tk.Label(bar, fg="gray")
        self.server_note.pack(side="left", padx=8)
        self.update_server()

    def update_server(self) -> None:
        mode = self.mode.get()
        addresses = network.lan_addresses()
        self.host_box.configure(values=addresses, state="readonly" if mode == HOST and len(addresses) > 1
                                else "disabled")
        if mode == HOST:
            host = self.host_address.get()
            note = f"friends choose Join and enter {host}" if host else "this PC is in no local network"
        elif mode == JOIN:
            note = "the address shown on the host's Powder"
        else:
            note = "nobody else can join"
        self.server_note.configure(text=note)

    def mode_changed(self) -> None:
        self.session.set_mode(self.mode.get(), join_address=self.join_address.get(),
                              host_address=self.host_address.get() or None)
        self.update_server()
        self.render_account()
        self.render_pack()

    # ---- the account on the server --------------------------------------------------------------------
    def render_account(self) -> None:
        for w in self.account_bar.winfo_children():
            w.destroy()
        tk.Label(self.account_bar, text="Account", font=("Segoe UI", 10, "bold")).pack(side="left", padx=(0, 8))
        acc = self.shown_account = self.session.account()
        if acc:
            tk.Label(self.account_bar, text=f"{acc.name} ({acc.email})").pack(side="left")
            for label, what in (("Change email…", "change_email"), ("Change password…", "change_password"),
                                ("Rename…", "rename")):
                ttk.Button(self.account_bar, text=label, command=lambda w=what: self.account_change(w)).pack(
                    side="left", padx=(6, 0))
            ttk.Button(self.account_bar, text="Sign out", command=self.sign_out).pack(side="left", padx=(6, 0))
        else:
            ttk.Button(self.account_bar, text="Register…", command=lambda: self.account_change("register")).pack(
                side="left")
            ttk.Button(self.account_bar, text="Sign in…", command=lambda: self.account_change("sign_in")).pack(
                side="left", padx=(6, 0))
            tk.Label(self.account_bar, text="none on this server yet (Play asks for it)", fg="gray").pack(
                side="left", padx=8)

    def account_change(self, what: str) -> None:
        acc = self.session.account()
        info = {"email": acc.email if acc else "", "name": acc.name if acc else ""}
        if what == "register":
            try:
                info["takes_over"] = account.takes_over(self.session.cfg, self.session.ca_path)
            except account.AccountError:
                pass
        answers = AccountDialog.ask(self.root, what, info)
        if not answers:
            return
        if acc and what not in ("register", "sign_in"):
            answers["email"] = acc.email
        try:
            self.session.account_action(what, **answers)
        except account.AccountError as e:
            messagebox.showerror("Account", str(e))
        else:
            if what == "change_password":
                messagebox.showinfo("Account", "Password changed. Ubisoft Connect asks for the new one the next "
                                               "time it signs in.")
        self.render_account()

    def sign_out(self) -> None:
        if messagebox.askyesno("Account", "Forget the account on this PC? (It stays on the server; sign in again "
                                          "any time. Ubisoft Connect stays signed in until you sign out there.)"):
            self.session.sign_out()
            self.render_account()

    def dialog_from_worker(self, kind: str, info: dict) -> dict | None:
        """Session.request from the worker thread: show the dialog in the UI thread and wait for it."""
        answer: queue.Queue = queue.Queue()

        def show():
            answer.put(AccountDialog.ask(self.root, kind, info))
            self.render_account()
        self.root.after(0, show)
        while True:
            try:
                return answer.get(timeout=0.3)
            except queue.Empty:
                if self.session.cancel.is_set():
                    return None

    def render_pack(self) -> None:
        """No pack: "Choose pack…"; a pack: a checkbox to use it or not (remembered; applies at the next Play)."""
        for w in self.pack_bar.winfo_children():
            w.destroy()
        info = self.session.pack_info()
        if info is None:                                 # remote server: its own pack, if any
            return
        tk.Label(self.pack_bar, text="Game data", font=("Segoe UI", 10, "bold")).pack(side="left", padx=(0, 8))
        if info.installed:
            self.use_pack.set(info.enabled)
            ttk.Checkbutton(self.pack_bar, text=f"Use the game-data pack (built {info.built})",
                            variable=self.use_pack, command=self.toggle_pack).pack(side="left")
            ttk.Button(self.pack_bar, text="Replace…", command=self.choose_pack).pack(side="left", padx=(8, 0))
            ttk.Button(self.pack_bar, text="Remove", command=self.remove_pack).pack(side="left", padx=(4, 0))
            note = "store and challenge rewards on" if info.enabled else "off: empty store, no challenge rewards"
        else:
            ttk.Button(self.pack_bar, text="Choose pack…", command=self.choose_pack).pack(side="left")
            note = "none (optional): the store is empty and challenges give no rewards"
            if info.problem:
                note += f" - the installed one can't be used: {info.problem}"
        tk.Label(self.pack_bar, text=note, fg="gray").pack(side="left", padx=8)

    def choose_pack(self) -> None:
        name = filedialog.askopenfilename(parent=self.root, title="Choose the game-data pack (.zip)",
                                          filetypes=[("Game-data pack", "*.zip"), ("All files", "*.*")])
        if name:
            error = self.session.install_pack(Path(name))
            if error:
                messagebox.showerror("Game-data pack", f"{Path(name).name} can't be used: {error}")
        self.render_pack()

    def remove_pack(self) -> None:
        if not messagebox.askyesno("Game-data pack", "Delete the installed game-data pack from this PC?\n\n"
                                   "Your progress is not affected; you can choose a pack again later."):
            return
        error = self.session.remove_pack()
        if error:
            messagebox.showerror("Game-data pack", f"The pack could not be removed: {error}")
        self.render_pack()

    def toggle_pack(self) -> None:
        self.session.set_pack_enabled(self.use_pack.get())
        self.render_pack()

    def render_play_buttons(self) -> None:
        if self.restore_only:
            return
        buttons = play_buttons(self.session.installs)
        if [label for label, _ in buttons] == self.shown_buttons:
            return
        for w in self.play_bar.winfo_children():
            w.destroy()
        for label, install in buttons:
            ttk.Button(self.play_bar, text=label, command=lambda i=install: self.session.start(i)).pack(
                side="left", padx=(0, 4))
        self.shown_buttons = [label for label, _ in buttons]

    def render(self) -> None:
        self.render_play_buttons()
        if not self.restore_only and self.session.account() != self.shown_account:
            self.render_account()                       # e.g. registered in the Account step
        for w in self.body.winfo_children():
            w.destroy()
        for row, step in enumerate(self.session.steps):
            tk.Label(self.body, text=ICONS[step.state], fg=COLORS[step.state], font=("Segoe UI", 12, "bold"),
                     width=2).grid(row=row * 3, column=0, sticky="nw")
            tk.Label(self.body, text=step.title, font=("Segoe UI", 10, "bold")).grid(row=row * 3, column=1, sticky="nw")
            tk.Label(self.body, text=step.text, wraplength=560, justify="left").grid(row=row * 3, column=2, sticky="nw")
            if step.state == ACTION:
                if step.details:
                    box = tk.Text(self.body, height=min(9, step.details.count("\n") + 1), width=60)
                    box.insert("1.0", step.details)
                    box.grid(row=row * 3 + 1, column=2, sticky="w", pady=2)
                bar = ttk.Frame(self.body)
                bar.grid(row=row * 3 + 2, column=2, sticky="w", pady=(0, 6))
                for label in step.actions:
                    ttk.Button(bar, text=label, command=lambda l=label: self.session.choose(l)).pack(side="left")
                if step.details:
                    ttk.Button(bar, text="Copy lines", command=lambda d=step.details: self.copy(d)).pack(side="left", padx=6)
        self.body.columnconfigure(2, weight=1)

    def copy(self, text: str) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(text)

    def quit(self) -> None:
        remove = True
        if self.session.hosts_removal_on_exit:          # announce the UAC prompt before it appears
            remove = messagebox.askyesnocancel(
                "Powder", "Remove the hosts-file entries again before closing (remove_hosts_on_exit)?\n\n"
                "The hosts file is a protected Windows system file, so Windows asks for admin rights (UAC) "
                "after you click Yes.\n\nNo: close and keep the entries. Cancel: don't close.")
            if remove is None:
                return
        self.session.stop(remove_hosts=remove)
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()
