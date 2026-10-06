"""Plain Tkinter window: one row per step with its state, text and buttons; Play (one button per installed
Steep version) / Restore system / Logs / Quit.
Deliberately simple (functionality first)."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from .session import ACTION, FAILED, OK, PENDING, RUNNING, Session, play_buttons

ICONS = {PENDING: "·", RUNNING: "…", OK: "✔", ACTION: "?", FAILED: "✖"}
COLORS = {PENDING: "gray", RUNNING: "blue", OK: "green", ACTION: "darkorange", FAILED: "red"}


class LauncherWindow:
    def __init__(self, session: Session):
        self.session = session
        self.root = tk.Tk()
        self.root.title("Powder")
        self.root.geometry("760x520")
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        session.on_update = lambda: self.root.after(0, self.render)

        top = ttk.Frame(self.root, padding=8)
        top.pack(fill="x")
        self.play_bar = ttk.Frame(top)                  # one Play button per installed version
        self.play_bar.pack(side="left")
        self.shown_buttons: list[str] | None = None
        ttk.Button(top, text="Restore system…", command=session.restore).pack(side="left", padx=6)
        ttk.Button(top, text="Open logs", command=session.open_logs).pack(side="left")
        ttk.Button(top, text="Quit", command=self.quit).pack(side="right")

        self.body = ttk.Frame(self.root, padding=8)
        self.body.pack(fill="both", expand=True)
        self.render()

    def render_play_buttons(self) -> None:
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
