import os
import re
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.patches import Patch
from aw_client import ActivityWatchClient

# ── colour palette ───────────────────────────────────────────────────────────
BG        = "#1e1e2e"
SURFACE   = "#2a2a3d"
SURFACE2  = "#32324a"
ACCENT    = "#7f77dd"
TEXT      = "#e0e0f0"
MUTED     = "#9090a8"
GREEN     = "#1d9e75"
BORDER    = "#3e3e58"

CAT_COLORS = {
    "Browser":      "#378add",
    "Productivity": "#1d9e75",
    "Media":        "#ba7517",
    "Games":        "#d85a30",
    "System":       "#888780",
    "Other":        "#7f77dd",
}

DATE_RE  = re.compile(r"(\d{2}-\d{2}-\d{4})")
DATE_FMT = "%m-%d-%Y"


# ─────────────────────────────────────────────────────────────────────────────
# Utility helpers
# ─────────────────────────────────────────────────────────────────────────────
def categorize(name):
    n = name.lower()
    if any(k in n for k in ["chrome","edge","firefox","msedge","opera","brave","iexplore","safari"]):
        return "Browser"
    if any(k in n for k in ["code","word","excel","slack","notepad","python","powershell",
                              "cmd","winword","outlook","teams","notion","onenote","libreoffice",
                              "pycharm","vscode","cursor","sublime"]):
        return "Productivity"
    if any(k in n for k in ["spotify","vlc","obs","mpv","wmplayer","itunes","youtube",
                              "netflix","twitch","discord"]):
        return "Media"
    if any(k in n for k in ["steam","game","epic","battle","valorant","csgo","minecraft",
                              "league","legends","rockstar","blizzard"]):
        return "Games"
    if any(k in n for k in ["svchost","csrss","winlogon","taskmgr","runtime","system",
                              "explorer","dwm","lsass","wininit","services","registry"]):
        return "System"
    return "Other"


def fmt_hms(secs):
    h, rem = divmod(int(secs), 3600)
    m, s   = divmod(rem, 60)
    if h:  return f"{h}h {m:02d}m"
    if m:  return f"{m}m {s:02d}s"
    return f"{s}s"


def parse_date_from_filename(filename):
    m = DATE_RE.search(filename)
    if m:
        try:
            return datetime.strptime(m.group(1), DATE_FMT).date()
        except ValueError:
            pass
    return None


def load_logs_by_date(folder, date_from=None, date_to=None):
    """Load log files and return data organized by date"""
    logs_by_date = defaultdict(lambda: defaultdict(int))
    logs_descs = defaultdict(dict)
    filter_active = (date_from is not None) or (date_to is not None)
    matched_dates = []

    for filename in sorted(os.listdir(folder)):
        if not filename.endswith(".log"):
            continue
        file_date = parse_date_from_filename(filename)

        if filter_active:
            if file_date is None:
                continue
            if date_from and file_date < date_from:
                continue
            if date_to and file_date > date_to:
                continue

        if file_date:
            matched_dates.append(file_date)

        filepath = os.path.join(folder, filename)
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                parts = line.strip().split("\t")
                if len(parts) < 2:
                    continue
                name = parts[0].strip()
                try:
                    secs = int(parts[1].strip())
                except ValueError:
                    continue
                desc = parts[2].strip() if len(parts) >= 3 else ""
                logs_by_date[file_date][name] += secs
                if desc and name not in logs_descs[file_date]:
                    logs_descs[file_date][name] = desc

    return logs_by_date, logs_descs, matched_dates


def load_activitywatch_by_date(date_from=None, date_to=None):
    """Load ActivityWatch data organized by date (applications only, not window titles)"""
    try:
        aw_by_date = defaultdict(lambda: defaultdict(int))
        aw_descs = defaultdict(dict)
        
        # Connect to ActivityWatch
        client = ActivityWatchClient("screen-time-dashboard", testing=False)
        
        # Get all buckets
        buckets = client.get_buckets()
        
        # Find the window bucket (this contains app data)
        window_bucket_id = None
        for bucket_id in buckets:
            if 'aw-watcher-window' in bucket_id:
                window_bucket_id = bucket_id
                break
        
        if not window_bucket_id:
            return None, None, None
        
        # Set time range
        if date_from:
            start = datetime.combine(date_from, datetime.min.time())
        else:
            start = datetime.now() - timedelta(days=30)
        
        if date_to:
            end = datetime.combine(date_to, datetime.max.time())
        else:
            end = datetime.now()
        
        # Convert to UTC for ActivityWatch
        start_utc = start.replace(tzinfo=timezone.utc)
        end_utc = end.replace(tzinfo=timezone.utc)
        
        # Get window events
        events = client.get_events(window_bucket_id, start=start_utc, end=end_utc)
        
        # Process each event
        for event in events:
            # Get the app name from the event data (NOT window title)
            app_name = None
            if hasattr(event, 'data') and isinstance(event.data, dict):
                # Priority: app name (the executable), not window title
                app_name = event.data.get('app')
                if not app_name:
                    app_name = event.data.get('exe')
            
            if not app_name:
                continue
            
            # Clean up app name
            app_name = app_name.replace('.exe', '').replace('.EXE', '')
            
            # Get local date from event timestamp
            event_local = event.timestamp.replace(tzinfo=timezone.utc).astimezone()
            event_date = event_local.date()
            
            # Add duration
            duration = event.duration.total_seconds()
            aw_by_date[event_date][app_name] += duration
            
            # Store a sample description
            if app_name not in aw_descs[event_date]:
                aw_descs[event_date][app_name] = app_name
        
        return aw_by_date, aw_descs, list(aw_by_date.keys())
        
    except Exception as e:
        print(f"ActivityWatch error: {e}")
        return None, None, None


def combine_data(aw_data, aw_descs, logs_data, logs_descs):
    """Combine ActivityWatch and log data, prioritizing ActivityWatch for dates that exist in both"""
    combined_totals = defaultdict(int)
    combined_descs = {}
    all_dates = set()
    
    # Add ActivityWatch data first (priority)
    if aw_data:
        for date_key, apps in aw_data.items():
            all_dates.add(date_key)
            for app_name, secs in apps.items():
                combined_totals[app_name] += secs
                if app_name in aw_descs.get(date_key, {}):
                    combined_descs[app_name] = aw_descs[date_key][app_name]
    
    # Add log data ONLY for dates NOT in ActivityWatch
    if logs_data:
        for date_key, apps in logs_data.items():
            # Only add log data if this date is NOT already covered by ActivityWatch
            if aw_data and date_key in aw_data:
                continue  # Skip this date - ActivityWatch already has it
            
            all_dates.add(date_key)
            for app_name, secs in apps.items():
                combined_totals[app_name] += secs
                if app_name in logs_descs.get(date_key, {}):
                    # Only set description if not already set by ActivityWatch
                    if app_name not in combined_descs:
                        combined_descs[app_name] = logs_descs[date_key][app_name]
    
    # Build rows
    rows = [
        {
            "name": n, 
            "desc": combined_descs.get(n, n),
            "secs": int(s), 
            "cat": categorize(n)
        }
        for n, s in sorted(combined_totals.items(), key=lambda x: x[1], reverse=True)
        if s > 60
    ]
    
    return rows, sorted(list(all_dates))


# ─────────────────────────────────────────────────────────────────────────────
# Rounded entry widget
# ─────────────────────────────────────────────────────────────────────────────
def _round_rect(canvas, x1, y1, x2, y2, r, **kw):
    pts = [
        x1+r, y1,   x2-r, y1,
        x2,   y1,   x2,   y1+r,
        x2,   y2-r, x2,   y2,
        x2-r, y2,   x1+r, y2,
        x1,   y2,   x1,   y2-r,
        x1,   y1+r, x1,   y1,
        x1+r, y1,
    ]
    return canvas.create_polygon(pts, smooth=True, **kw)


class RoundedEntry(tk.Frame):
    RADIUS  = 8
    H       = 30
    PAD_X   = 10

    def __init__(self, master, textvariable, width_chars=12,
                 bg=SURFACE2, fg=TEXT, **kw):
        super().__init__(master, bg=master["bg"] if "bg" not in kw else kw.pop("bg"),
                         highlightthickness=0, bd=0)
        self._var  = textvariable
        self._bg   = bg

        char_px   = 8
        total_w   = width_chars * char_px + self.PAD_X * 2 + 2

        self._canvas = tk.Canvas(self, width=total_w, height=self.H,
                                 bg=self["bg"], highlightthickness=0, bd=0)
        self._canvas.pack(side="left")

        self._shape = _round_rect(self._canvas, 1, 1, total_w-1, self.H-1,
                                  self.RADIUS, fill=bg, outline=BORDER, width=1)

        self._entry = tk.Entry(self._canvas, textvariable=textvariable,
                               font=("Segoe UI", 10), width=width_chars,
                               bg=bg, fg=fg, insertbackground=fg,
                               relief="flat", bd=0, highlightthickness=0)
        self._canvas.create_window(total_w // 2, self.H // 2,
                                   window=self._entry, anchor="center")

        self._entry.bind("<FocusIn>",  lambda _: self._canvas.itemconfig(
            self._shape, outline=ACCENT, width=2))
        self._entry.bind("<FocusOut>", lambda _: self._canvas.itemconfig(
            self._shape, outline=BORDER, width=1))

    def get(self):
        return self._var.get()


class RoundedDateEntry(tk.Frame):
    RADIUS = 8
    H      = 30

    def __init__(self, master, textvariable, **kw):
        super().__init__(master, bg=master["bg"] if "bg" not in kw else kw.pop("bg"),
                         highlightthickness=0, bd=0)
        self._var = textvariable

        total_w = 148

        self._canvas = tk.Canvas(self, width=total_w, height=self.H,
                                 bg=self["bg"], highlightthickness=0, bd=0)
        self._canvas.pack(side="left")

        self._shape = _round_rect(self._canvas, 1, 1, total_w-1, self.H-1,
                                  self.RADIUS, fill=SURFACE2, outline=BORDER, width=1)

        self._entry = tk.Entry(self._canvas, textvariable=textvariable,
                               font=("Segoe UI", 10), width=11,
                               bg=SURFACE2, fg=TEXT, insertbackground=TEXT,
                               relief="flat", bd=0, highlightthickness=0)
        self._canvas.create_window(total_w // 2 - 10, self.H // 2,
                                   window=self._entry, anchor="center")

        icon_btn = tk.Label(self._canvas, text="📅", font=("Segoe UI", 9),
                            bg=SURFACE2, fg=TEXT, cursor="hand2")
        self._canvas.create_window(total_w - 16, self.H // 2,
                                   window=icon_btn, anchor="center")
        icon_btn.bind("<Button-1>", lambda _: self._pick())

        self._entry.bind("<FocusIn>",  lambda _: self._canvas.itemconfig(
            self._shape, outline=ACCENT, width=2))
        self._entry.bind("<FocusOut>", lambda _: self._canvas.itemconfig(
            self._shape, outline=BORDER, width=1))

    def _pick(self):
        top = tk.Toplevel(self)
        top.title("Pick date")
        top.configure(bg=BG)
        top.resizable(False, False)
        top.grab_set()

        try:
            cur = datetime.strptime(self._var.get(), DATE_FMT).date()
        except ValueError:
            cur = date.today()

        state = {"year": cur.year, "month": cur.month}

        header    = tk.Frame(top, bg=BG)
        header.pack(fill="x", padx=10, pady=(8, 0))
        month_lbl = tk.Label(header, text="", font=("Segoe UI", 11, "bold"),
                              fg=TEXT, bg=BG, width=14)
        month_lbl.pack(side="left", expand=True)
        grid_frm  = tk.Frame(top, bg=BG)
        grid_frm.pack(padx=10, pady=6)

        def render():
            month_lbl.config(
                text=date(state["year"], state["month"], 1).strftime("%B %Y"))
            for w in grid_frm.winfo_children():
                w.destroy()
            for ci, dn in enumerate(["Mo","Tu","We","Th","Fr","Sa","Su"]):
                tk.Label(grid_frm, text=dn, font=("Segoe UI", 8, "bold"),
                         fg=MUTED, bg=BG, width=3).grid(row=0, column=ci, pady=(0,4))
            first     = date(state["year"], state["month"], 1)
            start_col = first.weekday()
            last_day  = (date(state["year"], state["month"] % 12 + 1, 1)
                         - timedelta(days=1)).day if state["month"] != 12 else 31

            row, col = 1, start_col
            for day_num in range(1, last_day + 1):
                is_today = date(state["year"], state["month"], day_num) == date.today()
                btn = tk.Button(
                    grid_frm, text=str(day_num), width=3,
                    font=("Segoe UI", 9),
                    bg=ACCENT if is_today else SURFACE,
                    fg="white" if is_today else TEXT,
                    relief="flat", cursor="hand2",
                    command=lambda d=day_num: _select(d))
                btn.grid(row=row, column=col, padx=1, pady=1)
                col += 1
                if col > 6:
                    col = 0; row += 1

        def _select(day):
            self._var.set(date(state["year"], state["month"], day).strftime(DATE_FMT))
            top.destroy()

        def prev_m():
            if state["month"] == 1: state["month"] = 12; state["year"] -= 1
            else: state["month"] -= 1
            render()

        def next_m():
            if state["month"] == 12: state["month"] = 1; state["year"] += 1
            else: state["month"] += 1
            render()

        tk.Button(header, text="◀", bg=BG, fg=TEXT, relief="flat",
                  cursor="hand2", command=prev_m).pack(side="left")
        tk.Button(header, text="▶", bg=BG, fg=TEXT, relief="flat",
                  cursor="hand2", command=next_m).pack(side="right")
        render()


# ─────────────────────────────────────────────────────────────────────────────
# Main dashboard
# ─────────────────────────────────────────────────────────────────────────────
class ScreenTimeDashboard(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Screen Time Dashboard (ActivityWatch + Logs Combined)")
        self.geometry("1280x910")
        self.configure(bg=BG)
        self.resizable(True, True)

        self.data       = []
        self.log_folder = None
        self.top_n_var  = tk.IntVar(value=15)
        self.cat_var    = tk.StringVar(value="All")
        self.sort_var   = tk.StringVar(value="Time (desc)")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self.refresh())

        self.date_mode   = tk.StringVar(value="all")
        today            = date.today().strftime(DATE_FMT)
        self.single_date = tk.StringVar(value=today)
        self.range_from  = tk.StringVar(value=today)
        self.range_to    = tk.StringVar(value=today)
        self.upto_date   = tk.StringVar(value=today)

        self._style_ttk()
        self._build_ui()

        default = os.path.expandvars(r"%LOCALAPPDATA%\digital-wellbeing\dailylogs")
        if os.path.isdir(default):
            self.log_folder = default
            self._load()

    def _style_ttk(self):
        s = ttk.Style()
        s.theme_use("clam")
        s.configure("Custom.Treeview",
                    background=SURFACE, fieldbackground=SURFACE,
                    foreground=TEXT, rowheight=30, font=("Segoe UI", 9))
        s.configure("Custom.Treeview.Heading",
                    background=BG, foreground=MUTED,
                    font=("Segoe UI", 9, "bold"), relief="flat")
        s.map("Custom.Treeview", background=[("selected", ACCENT)])
        s.configure("TRadiobutton", background=SURFACE, foreground=TEXT,
                    font=("Segoe UI", 9))
        s.map("TRadiobutton", background=[("active", SURFACE)])

    def _vsep(self, parent):
        sep = tk.Frame(parent, bg=BORDER, width=1)
        sep.pack(side="left", fill="y", padx=12, pady=6)
        return sep

    def _build_ui(self):
        # Top bar
        topbar = tk.Frame(self, bg=SURFACE, pady=12)
        topbar.pack(fill="x")
        tk.Label(topbar, text="⏱  Screen Time Dashboard (ActivityWatch + Logs Combined)",
                 font=("Segoe UI", 15, "bold"), fg=TEXT, bg=SURFACE
                 ).pack(side="left", padx=20)
        
        # Info label
        info_label = tk.Label(topbar, text="⚠️ ActivityWatch prioritized | Logs fill missing dates",
                              font=("Segoe UI", 9), fg=ACCENT, bg=SURFACE)
        info_label.pack(side="left", padx=20)
        
        tk.Button(topbar, text="📂  Open Logs Folder",
                  command=self._browse,
                  bg=ACCENT, fg="white", font=("Segoe UI", 10),
                  relief="flat", padx=14, pady=6, cursor="hand2"
                  ).pack(side="right", padx=18)

        # Metric cards
        cards_frame = tk.Frame(self, bg=BG)
        cards_frame.pack(fill="x", padx=20, pady=(12, 0))
        self.card_labels = {}
        for key, title in [("total","Total Time"),("top","Top Process"),
                            ("count","Processes"),("avg","Avg / Process")]:
            f = tk.Frame(cards_frame, bg=SURFACE, padx=18, pady=12)
            f.pack(side="left", expand=True, fill="both", padx=(0, 10))
            tk.Label(f, text=title, font=("Segoe UI", 9), fg=MUTED, bg=SURFACE).pack(anchor="w")
            lbl = tk.Label(f, text="—", font=("Segoe UI", 20, "bold"), fg=TEXT, bg=SURFACE)
            lbl.pack(anchor="w")
            self.card_labels[key] = lbl

        # Date filter bar
        date_outer = tk.Frame(self, bg=SURFACE)
        date_outer.pack(fill="x", padx=20, pady=(10, 0))
        tk.Frame(date_outer, bg=BORDER, height=1).pack(side="bottom", fill="x")

        date_frame = tk.Frame(date_outer, bg=SURFACE, pady=10)
        date_frame.pack(fill="x", padx=12)

        tk.Label(date_frame, text="DATE FILTER", font=("Segoe UI", 8, "bold"),
                 fg=MUTED, bg=SURFACE).pack(side="left", padx=(0, 12))

        self._vsep(date_frame)

        ttk.Radiobutton(date_frame, text="All time",
                        variable=self.date_mode, value="all"
                        ).pack(side="left", padx=(0, 2))

        self._vsep(date_frame)

        tk.Label(date_frame, text="SINGLE DAY", font=("Segoe UI", 8, "bold"),
                 fg=MUTED, bg=SURFACE).pack(side="left", padx=(0, 6))
        ttk.Radiobutton(date_frame, text="",
                        variable=self.date_mode, value="single"
                        ).pack(side="left", padx=(0, 4))
        RoundedDateEntry(date_frame, self.single_date).pack(side="left", padx=(0, 0))

        self._vsep(date_frame)

        tk.Label(date_frame, text="RANGE", font=("Segoe UI", 8, "bold"),
                 fg=MUTED, bg=SURFACE).pack(side="left", padx=(0, 6))
        ttk.Radiobutton(date_frame, text="",
                        variable=self.date_mode, value="range"
                        ).pack(side="left", padx=(0, 4))
        RoundedDateEntry(date_frame, self.range_from).pack(side="left", padx=(0, 4))
        tk.Label(date_frame, text="→", font=("Segoe UI", 10),
                 fg=MUTED, bg=SURFACE).pack(side="left", padx=(0, 4))
        RoundedDateEntry(date_frame, self.range_to).pack(side="left", padx=(0, 0))

        self._vsep(date_frame)

        tk.Label(date_frame, text="UP TO", font=("Segoe UI", 8, "bold"),
                 fg=MUTED, bg=SURFACE).pack(side="left", padx=(0, 6))
        ttk.Radiobutton(date_frame, text="",
                        variable=self.date_mode, value="upto"
                        ).pack(side="left", padx=(0, 4))
        RoundedDateEntry(date_frame, self.upto_date).pack(side="left", padx=(0, 0))

        self._vsep(date_frame)

        tk.Button(date_frame, text="▶  Apply",
                  command=self._load,
                  bg=GREEN, fg="white", font=("Segoe UI", 9, "bold"),
                  relief="flat", padx=14, pady=5, cursor="hand2"
                  ).pack(side="left", padx=(0, 10))

        self.date_info_var = tk.StringVar(value="")
        tk.Label(date_frame, textvariable=self.date_info_var,
                 font=("Segoe UI", 9, "italic"), fg=ACCENT, bg=SURFACE
                 ).pack(side="left")

        # Process filter controls
        ctrl_outer = tk.Frame(self, bg=SURFACE)
        ctrl_outer.pack(fill="x", padx=20, pady=(0, 0))
        tk.Frame(ctrl_outer, bg=BORDER, height=1).pack(side="bottom", fill="x")

        ctrl = tk.Frame(ctrl_outer, bg=SURFACE, pady=9)
        ctrl.pack(fill="x", padx=12)

        def clbl(text):
            return tk.Label(ctrl, text=text, font=("Segoe UI", 8, "bold"),
                            fg=MUTED, bg=SURFACE)

        clbl("TOP N").pack(side="left", padx=(0, 6))
        tk.Scale(ctrl, from_=3, to=30, orient="horizontal",
                 variable=self.top_n_var, command=lambda _: self.refresh(),
                 bg=SURFACE, fg=TEXT, troughcolor=SURFACE2, highlightthickness=0,
                 activebackground=ACCENT, length=100, font=("Segoe UI", 9),
                 bd=0, sliderlength=14
                 ).pack(side="left", padx=(0, 2))

        self._vsep(ctrl)

        clbl("CATEGORY").pack(side="left", padx=(0, 6))
        cb_cat = ttk.Combobox(ctrl, textvariable=self.cat_var,
                              values=["All"] + list(CAT_COLORS.keys()),
                              state="readonly", width=13, font=("Segoe UI", 10))
        cb_cat.pack(side="left", padx=(0, 2))
        cb_cat.bind("<<ComboboxSelected>>", lambda _: self.refresh())

        self._vsep(ctrl)

        clbl("SORT").pack(side="left", padx=(0, 6))
        cb_sort = ttk.Combobox(ctrl, textvariable=self.sort_var,
                               values=["Time (desc)", "Time (asc)", "Name (A-Z)"],
                               state="readonly", width=13, font=("Segoe UI", 10))
        cb_sort.pack(side="left", padx=(0, 2))
        cb_sort.bind("<<ComboboxSelected>>", lambda _: self.refresh())

        self._vsep(ctrl)

        clbl("SEARCH").pack(side="left", padx=(0, 6))
        RoundedEntry(ctrl, self.search_var, width_chars=18,
                     bg=SURFACE2
                     ).pack(side="left", padx=(0, 2))

        # Main content area
        content = tk.Frame(self, bg=BG)
        content.pack(fill="both", expand=True, padx=20, pady=10)

        chart_panel = tk.Frame(content, bg=SURFACE)
        chart_panel.pack(side="left", fill="both", expand=True)
        self.fig, self.ax = plt.subplots(figsize=(7, 5))
        self.fig.patch.set_facecolor(SURFACE)
        self.ax.set_facecolor(SURFACE)
        self.canvas = FigureCanvasTkAgg(self.fig, master=chart_panel)
        self.canvas.get_tk_widget().pack(fill="both", expand=True, padx=6, pady=6)

        table_panel = tk.Frame(content, bg=SURFACE, width=390)
        table_panel.pack(side="right", fill="both", padx=(10, 0))
        table_panel.pack_propagate(False)

        self.tree = ttk.Treeview(table_panel,
                                  columns=("rank","name","cat","time","pct"),
                                  show="headings", style="Custom.Treeview")
        for col, txt, w, anc in [
            ("rank", "#",        38,  "center"),
            ("name", "Process", 170,  "w"),
            ("cat",  "Category", 92,  "center"),
            ("time", "Time",     72,  "center"),
            ("pct",  "Share",    50,  "center"),
        ]:
            self.tree.heading(col, text=txt)
            self.tree.column(col, width=w, anchor=anc)

        sb = ttk.Scrollbar(table_panel, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)

        self.status_var = tk.StringVar(value="Ready - Loading data...")
        tk.Label(self, textvariable=self.status_var,
                 font=("Segoe UI", 9), fg=MUTED, bg=BG, anchor="w"
                 ).pack(fill="x", padx=22, pady=(0, 6))

    def _parse_date_var(self, var):
        try:
            return datetime.strptime(var.get().strip(), DATE_FMT).date()
        except ValueError:
            return None

    def _browse(self):
        folder = filedialog.askdirectory(title="Select daily logs folder")
        if folder:
            self.log_folder = folder
            self._load()

    def _load(self):
        """Load and combine data from ActivityWatch (priority) and logs (fallback)"""
        mode = self.date_mode.get()
        date_from = date_to = None

        if mode == "single":
            d = self._parse_date_var(self.single_date)
            if d is None:
                messagebox.showwarning("Invalid date",
                    "Please enter a valid date in MM-DD-YYYY format.")
                return
            date_from = date_to = d
        elif mode == "range":
            date_from = self._parse_date_var(self.range_from)
            date_to   = self._parse_date_var(self.range_to)
            if not date_from or not date_to:
                messagebox.showwarning("Invalid date",
                    "Please enter valid dates in MM-DD-YYYY format.")
                return
            if date_from > date_to:
                date_from, date_to = date_to, date_from
        elif mode == "upto":
            date_to = self._parse_date_var(self.upto_date)
            if date_to is None:
                messagebox.showwarning("Invalid date",
                    "Please enter a valid date in MM-DD-YYYY format.")
                return

        self.status_var.set("Loading data from ActivityWatch (priority)...")
        self.update_idletasks()

        # Load ActivityWatch data
        aw_data, aw_descs, aw_dates = load_activitywatch_by_date(date_from, date_to)
        
        # Load log data
        logs_data = None
        logs_descs = None
        logs_dates = []
        if self.log_folder:
            self.status_var.set("Loading fallback data from logs...")
            self.update_idletasks()
            logs_data, logs_descs, logs_dates = load_logs_by_date(self.log_folder, date_from, date_to)
        
        # Combine data (ActivityWatch prioritized)
        rows, all_dates = combine_data(aw_data, aw_descs, logs_data, logs_descs)
        
        if not rows:
            messagebox.showinfo("No data", 
                "No data found from any source.\n\n"
                "Make sure ActivityWatch is running OR you have selected a logs folder.")
            return
        
        self.data = rows
        
        # Show which dates came from where
        aw_count = len(aw_dates) if aw_dates else 0
        logs_count = len(logs_dates) if logs_dates else 0
        total_days = len(all_dates)
        
        if aw_count > 0 and logs_count > 0:
            self.date_info_var.set(f"✓ {aw_count} days from ActivityWatch + {logs_count} days from logs")
            source_msg = f"(ActivityWatch: {aw_count} days, Logs: {logs_count} days)"
        elif aw_count > 0:
            self.date_info_var.set(f"✓ {aw_count} days from ActivityWatch")
            source_msg = "(ActivityWatch only)"
        else:
            self.date_info_var.set(f"✓ {logs_count} days from Log Files")
            source_msg = "(Log files only - ActivityWatch not available)"
        
        self._update_cards()
        total_secs = sum(d["secs"] for d in self.data)
        self.status_var.set(
            f"Loaded {len(self.data)} processes, total {fmt_hms(total_secs)} "
            f"from {total_days} days {source_msg}")
        self.refresh()

    def _update_cards(self):
        if not self.data:
            return
        total = sum(d["secs"] for d in self.data)
        top   = self.data[0]
        self.card_labels["total"].config(text=fmt_hms(total))
        self.card_labels["top"].config(
            text=(top["desc"] or top["name"])[:22], font=("Segoe UI", 13, "bold"))
        self.card_labels["count"].config(text=str(len(self.data)))
        self.card_labels["avg"].config(text=fmt_hms(total // max(len(self.data), 1)))

    def _filtered(self):
        cat  = self.cat_var.get()
        srt  = self.sort_var.get()
        q    = self.search_var.get().lower()
        n    = self.top_n_var.get()
        rows = [r for r in self.data
                if (cat == "All" or r["cat"] == cat)
                and (not q or q in r["name"].lower() or q in r["desc"].lower())]
        if srt == "Time (asc)":    rows.sort(key=lambda x: x["secs"])
        elif srt == "Name (A-Z)":  rows.sort(key=lambda x: x["name"].lower())
        else:                       rows.sort(key=lambda x: x["secs"], reverse=True)
        return rows[:n]

    def refresh(self):
        rows  = self._filtered()
        total = sum(d["secs"] for d in self.data) or 1

        # Chart
        self.ax.clear()
        self.ax.set_facecolor(SURFACE)

        if rows:
            labels = [(r["desc"] or r["name"].replace(".exe",""))[:24] for r in rows]
            values = [r["secs"] / 3600 for r in rows]
            colors = [CAT_COLORS.get(r["cat"], ACCENT) for r in rows]

            bars = self.ax.barh(range(len(rows)), values, color=colors,
                                height=0.60, zorder=3)
            self.ax.set_yticks(range(len(rows)))
            self.ax.set_yticklabels(labels, fontsize=9, color=TEXT)
            self.ax.invert_yaxis()

            max_v = max(values) if values else 1
            for bar, val in zip(bars, values):
                self.ax.text(
                    bar.get_width() + max_v * 0.015,
                    bar.get_y() + bar.get_height() / 2,
                    f"{val:.2f}h", va="center", ha="left",
                    fontsize=8, color=MUTED)

            self.ax.xaxis.set_major_formatter(
                mticker.FuncFormatter(lambda x, _: f"{x:.1f}h"))
            self.ax.tick_params(axis="x", colors=MUTED, labelsize=8)
            self.ax.tick_params(axis="y", colors=TEXT)
            for sp in self.ax.spines.values():
                sp.set_visible(False)
            self.ax.xaxis.grid(True, color="#3a3a55", linewidth=0.5, zorder=0)
            self.ax.set_xlabel("Hours", color=MUTED, fontsize=9)

            seen    = list(dict.fromkeys(r["cat"] for r in rows))
            handles = [Patch(color=CAT_COLORS.get(c, ACCENT), label=c) for c in seen]
            self.ax.legend(handles=handles, loc="lower right",
                           framealpha=0, fontsize=8, labelcolor=TEXT)
        else:
            self.ax.text(0.5, 0.5, "No data to display",
                         transform=self.ax.transAxes,
                         ha="center", va="center", color=MUTED, fontsize=12)

        self.fig.tight_layout(pad=1.2)
        self.canvas.draw()

        # Table
        self.tree.delete(*self.tree.get_children())
        for i, r in enumerate(rows, 1):
            pct  = f"{r['secs'] / total * 100:.1f}%"
            name = (r["desc"] or r["name"].replace(".exe",""))[:32]
            self.tree.insert("", "end",
                             values=(i, name, r["cat"], fmt_hms(r["secs"]), pct))


if __name__ == "__main__":
    app = ScreenTimeDashboard()
    app.mainloop()