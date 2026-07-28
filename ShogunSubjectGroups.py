import os
import json
import traceback
import tkinter as tk
from tkinter import messagebox

from vicon_core_api import Client
from shogun_live_api import SubjectServices

BG = "#3A3A3C"
CARD = "#2C2C2E"
LIST_BG = "#242426"
ACCENT = "#4CC2B4"
TEXT = "#D8D8DA"
MUTED = "#8A8A8E"
BUTTON = "#48484A"
SUCCESS = "#5CA05C"
ERROR = "#BF5B5B"
DARK_FG = "#1B1B1D"
LIGHT_FG = "#F2F2F2"
FONT = "Segoe UI"

HOST = "localhost"

SUBJECT_GROUPS_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "subject_groups.json"
)


def load_subject_groups():
    try:
        with open(SUBJECT_GROUPS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        raw = data.get("groups", []) if isinstance(data, dict) else data
        groups = []
        for g in raw:
            if isinstance(g, dict) and g.get("name"):
                groups.append({
                    "name": str(g["name"]),
                    "subjects": [str(s) for s in g.get("subjects", [])],
                })
        return groups
    except (OSError, ValueError):
        return []


def save_subject_groups(groups):
    try:
        with open(SUBJECT_GROUPS_FILE, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "groups": groups}, f, ensure_ascii=False, indent=2)
    except OSError as e:
        print(f"Failed to save subject groups: {str(e)}")


class SubjectGroupApp:
    TYPE_LABELS = {
        "ERigidObject": "Prop",
        "ELabelingCluster": "Cluster",
        "EGeneral": "Subject",
    }

    def __init__(self, root):
        self.root = root
        self.root.title("Shogun Subject Group Manager")
        self.root.geometry("960x680")
        self.root.minsize(780, 540)
        self.root.configure(bg=BG)

        self.client = None
        self.sub = None
        self.groups = load_subject_groups()
        self.subjects = []

        self.create_widgets()
        self.root.after(100, self.connect)

    @staticmethod
    def _shade(hex_color, factor):
        try:
            r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
            r, g, b = (min(255, max(0, int(c * factor))) for c in (r, g, b))
            return f"#{r:02x}{g:02x}{b:02x}"
        except Exception:
            return hex_color

    def _btn(self, parent, text, cmd, bg=None, fg=None, small=False):
        bg = bg if bg else BUTTON
        fg = fg if fg else TEXT
        return tk.Button(parent, text=text, command=cmd,
                         font=(FONT, 9 if small else 10),
                         bg=bg, fg=fg, relief="flat", bd=0,
                         padx=10, pady=6 if small else 9, cursor="hand2",
                         activebackground=self._shade(bg, 1.22), activeforeground=fg)

    def _make_listbox(self, parent, selectmode, height=8):
        wrap = tk.Frame(parent, bg=CARD)
        wrap.pack(fill=tk.BOTH, expand=True)
        scroll = tk.Scrollbar(wrap, orient=tk.VERTICAL)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        lb = tk.Listbox(wrap, selectmode=selectmode, height=height,
                        activestyle="none", exportselection=False,
                        bg=LIST_BG, fg=TEXT, selectbackground=ACCENT, selectforeground=DARK_FG,
                        highlightthickness=1, highlightbackground="#1E1E1E",
                        relief="flat", bd=0, font=(FONT, 10),
                        yscrollcommand=scroll.set)
        lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.config(command=lb.yview)
        return lb

    def create_widgets(self):
        main = tk.Frame(self.root, bg=BG, padx=16, pady=14)
        main.pack(fill=tk.BOTH, expand=True)

        header = tk.Frame(main, bg=BG)
        header.pack(fill=tk.X)
        tk.Label(header, text="Subject Group Manager", font=(FONT, 15, "bold"),
                 bg=BG, fg=TEXT).pack(side=tk.LEFT)
        self.conn_btn = self._btn(header, "⟳ Reconnect", self.connect,
                                  bg=BUTTON, fg=TEXT, small=True)
        self.conn_btn.pack(side=tk.RIGHT)
        self.conn_var = tk.StringVar(value="● Not connected")
        self.conn_label = tk.Label(header, textvariable=self.conn_var, font=(FONT, 9, "bold"),
                                   bg=BG, fg=MUTED)
        self.conn_label.pack(side=tk.RIGHT, padx=(0, 12))

        tk.Label(main,
                 text=f"Host: {HOST}  ·  Group subjects/props and toggle them on/off together.",
                 font=(FONT, 9), bg=BG, fg=MUTED).pack(anchor=tk.W, pady=(3, 12))

        self.status_var = tk.StringVar(value="")
        tk.Label(main, textvariable=self.status_var, font=(FONT, 9),
                 bg=BG, fg=ACCENT, anchor=tk.W).pack(side=tk.BOTTOM, fill=tk.X, pady=(12, 0))

        body = tk.Frame(main, bg=BG)
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(0, weight=1, uniform="col")
        body.columnconfigure(1, weight=1, uniform="col")
        body.rowconfigure(0, weight=1)

        self._build_subject_column(body)
        self._build_group_column(body)

    def _build_subject_column(self, body):
        col = tk.Frame(body, bg=CARD, padx=14, pady=12)
        col.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        head = tk.Frame(col, bg=CARD)
        head.pack(fill=tk.X, pady=(0, 8))
        tk.Label(head, text="Subjects in Shogun", font=(FONT, 11, "bold"),
                 bg=CARD, fg=TEXT).pack(side=tk.LEFT)
        self._btn(head, "⟳ Refresh", self.refresh_subjects,
                  bg=BUTTON, fg=TEXT, small=True).pack(side=tk.RIGHT)

        self.subject_lb = self._make_listbox(col, tk.EXTENDED, height=12)

        tk.Label(col, text="● Enabled / ○ Disabled  ·  Multi-select with Ctrl / Shift",
                 font=(FONT, 8), bg=CARD, fg=MUTED).pack(anchor=tk.W, pady=(6, 8))

        quick = tk.Frame(col, bg=CARD)
        quick.pack(fill=tk.X)
        self._btn(quick, "Enable Selected", lambda: self._enable_selected_subjects(True),
                  bg=SUCCESS, fg=LIGHT_FG, small=True).pack(
                      side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        self._btn(quick, "Disable Selected", lambda: self._enable_selected_subjects(False),
                  bg=ERROR, fg=LIGHT_FG, small=True).pack(
                      side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))

    def _build_group_column(self, body):
        col = tk.Frame(body, bg=CARD, padx=14, pady=12)
        col.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        tk.Label(col, text="Groups", font=(FONT, 11, "bold"),
                 bg=CARD, fg=TEXT).pack(anchor=tk.W, pady=(0, 8))

        self.group_lb = self._make_listbox(col, tk.BROWSE, height=6)
        self.group_lb.bind("<<ListboxSelect>>", lambda e: self._on_group_select())

        create = tk.Frame(col, bg=CARD)
        create.pack(fill=tk.X, pady=(8, 4))
        self.new_group_var = tk.StringVar()
        tk.Entry(create, textvariable=self.new_group_var, font=(FONT, 10),
                 bg=LIST_BG, fg=TEXT, insertbackground=TEXT, relief="flat").pack(
                     side=tk.LEFT, fill=tk.X, expand=True, ipady=4, padx=(0, 6))
        self._btn(create, "＋ New", self._create_group_from_selection,
                  bg=ACCENT, fg=DARK_FG, small=True).pack(side=tk.LEFT)

        ops = tk.Frame(col, bg=CARD)
        ops.pack(fill=tk.X, pady=(0, 8))
        self._btn(ops, "Add Selected", self._add_selection_to_group, small=True).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 3))
        self._btn(ops, "Replace", self._replace_group_with_selection, small=True).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=3)
        self._btn(ops, "Delete", self._delete_group,
                  bg=ERROR, fg=LIGHT_FG, small=True).pack(
                      side=tk.LEFT, fill=tk.X, expand=True, padx=(3, 0))

        tk.Label(col, text="Group Contents", font=(FONT, 10, "bold"),
                 bg=CARD, fg=TEXT).pack(anchor=tk.W, pady=(4, 4))
        self.member_lb = self._make_listbox(col, tk.EXTENDED, height=6)
        tk.Label(col, text="✗ = not loaded  ·  select members, then Remove Members",
                 font=(FONT, 8), bg=CARD, fg=MUTED).pack(anchor=tk.W, pady=(4, 6))

        member_ops = tk.Frame(col, bg=CARD)
        member_ops.pack(fill=tk.X, pady=(0, 8))
        self._btn(member_ops, "Remove Members", self._remove_selected_members, small=True).pack(
            side=tk.RIGHT)

        toggle = tk.Frame(col, bg=CARD)
        toggle.pack(fill=tk.X)
        self._btn(toggle, "✔ Enable Group", lambda: self._set_group_enabled(True),
                  bg=SUCCESS, fg=LIGHT_FG).pack(
                      side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        self._btn(toggle, "✖ Disable Group", lambda: self._set_group_enabled(False),
                  bg=ERROR, fg=LIGHT_FG).pack(
                      side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))

        self.refresh_group_list()

    def connect(self):
        try:
            self._status(f"Connecting to {HOST}...", "info")
            self.client = Client(HOST)
            self.sub = SubjectServices(self.client)
            self.conn_var.set("● Connected")
            self.conn_label.configure(fg=SUCCESS)
            self.refresh_subjects()
        except Exception as e:
            traceback.print_exc()
            self.sub = None
            self.conn_var.set("● Not connected")
            self.conn_label.configure(fg=ERROR)
            self._status(f"Connection failed: {str(e)}", "error")
            messagebox.showerror(
                "Connection Error",
                f"Could not connect to Shogun Live at {HOST}.\n"
                f"Make sure Shogun Live is running.\n\n{str(e)}")

    def _status(self, msg, status_type="info"):
        self.status_var.set(msg)
        print(f"[{status_type}] {msg}")

    def _require_conn(self):
        if not self.sub:
            self._status("Not connected to Shogun (please reconnect)", "error")
            return False
        return True

    def refresh_subjects(self):
        if not self._require_conn():
            return
        try:
            result, names = self.sub.subjects()
            if not result:
                self._status("Failed to get subjects", "error")
                names = []
            enabled = []
            try:
                er, enabled = self.sub.enabled_subjects()
                if not er:
                    enabled = []
            except Exception:
                enabled = []
            enabled_set = set(enabled)

            self.subjects = []
            for name in names:
                type_label = "Subject"
                try:
                    tr, stype = self.sub.subject_type(name)
                    if tr:
                        type_label = self.TYPE_LABELS.get(
                            getattr(stype, "name", str(stype)), str(stype))
                except Exception:
                    pass
                self.subjects.append((name, type_label, name in enabled_set))

            self.subject_lb.delete(0, tk.END)
            for name, type_label, is_en in self.subjects:
                marker = "●" if is_en else "○"
                self.subject_lb.insert(tk.END, f"{marker}  {name}   [{type_label}]")
                self.subject_lb.itemconfig(
                    tk.END, foreground=TEXT if is_en else MUTED)

            self._on_group_select()
            self._status(f"Loaded {len(self.subjects)} subjects ({len(enabled_set)} enabled)",
                         "success")
        except Exception as e:
            traceback.print_exc()
            self._status(f"Refresh failed: {str(e)}", "error")

    def _selected_subject_names(self):
        return [self.subjects[i][0] for i in self.subject_lb.curselection()
                if 0 <= i < len(self.subjects)]

    def _apply_enabled(self, names, enable):
        ok, missing = 0, []
        for name in names:
            try:
                r = self.sub.set_subject_enabled(name, enable)
                if r:
                    ok += 1
                else:
                    missing.append(name)
            except Exception:
                missing.append(name)
        return ok, missing

    def _enable_selected_subjects(self, enable):
        if not self._require_conn():
            return
        names = self._selected_subject_names()
        if not names:
            self._status("Select one or more subjects", "info")
            return
        ok, missing = self._apply_enabled(names, enable)
        word = "enabled" if enable else "disabled"
        self.refresh_subjects()
        if missing:
            self._status(f"{ok} subject(s) {word} ({len(missing)} failed)", "error")
        else:
            self._status(f"{ok} subject(s) {word}", "success")

    def _set_group_enabled(self, enable):
        if not self._require_conn():
            return
        g = self._selected_group()
        if not g:
            self._status("Select a group", "info")
            return
        names = g["subjects"]
        if not names:
            self._status("This group has no subjects", "info")
            return
        ok, missing = self._apply_enabled(names, enable)
        word = "enabled" if enable else "disabled"
        self.refresh_subjects()
        if missing:
            messagebox.showwarning(
                "Some subjects not found",
                f'Group "{g["name"]}" {word}.\n\n'
                f"Not loaded / not found:\n  " + "\n  ".join(missing))
            self._status(f'"{g["name"]}" {word} ({ok} ok / {len(missing)} failed)', "error")
        else:
            self._status(f'"{g["name"]}" {word} ({ok} subject(s))', "success")

    def refresh_group_list(self):
        self.group_lb.delete(0, tk.END)
        for g in self.groups:
            self.group_lb.insert(tk.END, f"{g['name']}   ({len(g['subjects'])})")

    def _selected_group_index(self):
        sel = self.group_lb.curselection()
        return sel[0] if sel else None

    def _selected_group(self):
        idx = self._selected_group_index()
        return self.groups[idx] if idx is not None and idx < len(self.groups) else None

    def _on_group_select(self):
        g = self._selected_group()
        self.member_lb.delete(0, tk.END)
        if not g:
            return
        loaded = {s[0]: s[2] for s in self.subjects}
        for name in g["subjects"]:
            if name in loaded:
                mark = "●" if loaded[name] else "○"
                color = TEXT if loaded[name] else MUTED
            else:
                mark = "✗"
                color = ERROR
            self.member_lb.insert(tk.END, f"{mark}  {name}")
            self.member_lb.itemconfig(tk.END, foreground=color)

    def _persist(self):
        save_subject_groups(self.groups)

    def _create_group_from_selection(self):
        name = self.new_group_var.get().strip()
        if not name:
            self._status("Enter a group name", "info")
            return
        names = self._selected_subject_names()
        if not names:
            self._status("Select subjects to put in the group", "info")
            return
        for g in self.groups:
            if g["name"] == name:
                if not messagebox.askyesno(
                        "Confirm", f'Group "{name}" already exists. Overwrite it?'):
                    return
                g["subjects"] = list(names)
                self._persist()
                self.refresh_group_list()
                self._status(f'Group "{name}" overwritten', "success")
                return
        self.groups.append({"name": name, "subjects": list(names)})
        self._persist()
        self.refresh_group_list()
        self.group_lb.selection_clear(0, tk.END)
        self.group_lb.selection_set(len(self.groups) - 1)
        self._on_group_select()
        self.new_group_var.set("")
        self._status(f'Group "{name}" created ({len(names)} subject(s))', "success")

    def _add_selection_to_group(self):
        g = self._selected_group()
        if not g:
            self._status("Select a target group", "info")
            return
        names = self._selected_subject_names()
        if not names:
            self._status("Select subjects to add", "info")
            return
        added = 0
        for n in names:
            if n not in g["subjects"]:
                g["subjects"].append(n)
                added += 1
        self._persist()
        self.refresh_group_list()
        self._reselect_group(g["name"])
        self._on_group_select()
        self._status(f'Added {added} subject(s) to "{g["name"]}"', "success")

    def _replace_group_with_selection(self):
        g = self._selected_group()
        if not g:
            self._status("Select a group to replace", "info")
            return
        names = self._selected_subject_names()
        if not names:
            self._status("Select subjects to use", "info")
            return
        g["subjects"] = list(names)
        self._persist()
        self.refresh_group_list()
        self._reselect_group(g["name"])
        self._on_group_select()
        self._status(f'"{g["name"]}" replaced with {len(names)} subject(s)', "success")

    def _remove_selected_members(self):
        g = self._selected_group()
        if not g:
            self._status("Select a group", "info")
            return
        sel = self.member_lb.curselection()
        if not sel:
            self._status("Select members to remove", "info")
            return
        to_remove = {g["subjects"][i] for i in sel if 0 <= i < len(g["subjects"])}
        g["subjects"] = [s for s in g["subjects"] if s not in to_remove]
        self._persist()
        self.refresh_group_list()
        self._reselect_group(g["name"])
        self._on_group_select()
        self._status(f'Removed {len(to_remove)} member(s) from "{g["name"]}"', "success")

    def _delete_group(self):
        g = self._selected_group()
        if not g:
            self._status("Select a group to delete", "info")
            return
        if not messagebox.askyesno("Confirm", f'Delete group "{g["name"]}"?'):
            return
        self.groups = [x for x in self.groups if x is not g]
        self._persist()
        self.refresh_group_list()
        self.member_lb.delete(0, tk.END)
        self._status(f'Group "{g["name"]}" deleted', "success")

    def _reselect_group(self, name):
        for i, g in enumerate(self.groups):
            if g["name"] == name:
                self.group_lb.selection_clear(0, tk.END)
                self.group_lb.selection_set(i)
                return


def main():
    root = tk.Tk()
    SubjectGroupApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
