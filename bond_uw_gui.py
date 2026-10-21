#!/usr/bin/env python3
"""채권간편검색기 - 창(GUI) 버전. bond_uw.py 와 같은 폴더에 두고 실행한다."""
import contextlib
import os
import queue
import re
import sys
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from tkinter import font as tkfont
from tkinter import messagebox, simpledialog, ttk

import bond_uw as b


# ---------------------------------------------------------------- 화면 꾸미기 (아이콘 색에 맞춘 청록 + 노랑)
BG = "#f4f8f9"
TEAL = "#1296a8"
TEAL_DARK = "#0b6e7f"
YELLOW = "#ffc43d"
FONT = "Malgun Gothic"


def apply_theme(root):
    """전체 위젯의 색/글꼴/여백을 한 번에 정한다. 실패해도 기본 모양으로 동작한다."""
    try:
        st = ttk.Style(root)
        try:
            st.theme_use("clam")
        except Exception:
            pass
        root.configure(background=BG)
        for pat, val in (("*Text.relief", "flat"), ("*Text.highlightThickness", 1), ("*Text.highlightBackground", "#c5d3d8"),
                         ("*Text.highlightColor", TEAL), ("*Text.padX", 8), ("*Text.padY", 6),
                         ("*Text.font", f"{{{FONT}}} 10"), ("*Listbox.relief", "flat"), ("*Listbox.highlightThickness", 1),
                         ("*Listbox.highlightBackground", "#c5d3d8"), ("*Listbox.font", f"{{{FONT}}} 10"),
                         ("*Listbox.selectBackground", "#bfe6ec"), ("*Listbox.selectForeground", "#0b3c46")):
            root.option_add(pat, val)
        st.configure(".", font=(FONT, 10), background=BG, foreground="#25323a")
        st.configure("TFrame", background=BG)
        st.configure("TLabel", background=BG)
        st.configure("TCheckbutton", background=BG)
        st.map("TCheckbutton", background=[("active", BG)])
        st.configure("TEntry", padding=5, fieldbackground="white", bordercolor="#c5d3d8", lightcolor="#c5d3d8")
        st.map("TEntry", bordercolor=[("focus", TEAL)], lightcolor=[("focus", TEAL)])
        st.configure("TButton", padding=(12, 6), background="white", bordercolor="#c5d3d8",
                     lightcolor="white", darkcolor="white", focuscolor=BG)
        st.map("TButton", background=[("active", "#e6f4f6"), ("disabled", "#eef1f2")],
               foreground=[("disabled", "#a5b0b5")])
        st.configure("Accent.TButton", padding=(14, 6), background=TEAL, foreground="white",
                     bordercolor=TEAL_DARK, lightcolor=TEAL, darkcolor=TEAL, focuscolor=TEAL,
                     font=(FONT, 10, "bold"))
        st.map("Accent.TButton", background=[("active", TEAL_DARK), ("disabled", "#9fc9d0")],
               foreground=[("disabled", "#eef6f8")])
        st.configure("TNotebook", background=BG, borderwidth=0, tabmargins=0, bordercolor=BG, lightcolor=BG, darkcolor=BG)
        st.layout("TNotebook.Tab", [])  # 기본 탭은 숨기고 둥근 탭 줄(PillTabs)을 대신 쓴다
        st.configure("Treeview", rowheight=26, background="white", fieldbackground="white",
                     bordercolor="#d5e0e4", font=(FONT, 10))
        st.map("Treeview", background=[("selected", "#bfe6ec")], foreground=[("selected", "#0b3c46")])
        st.configure("Treeview.Heading", background=TEAL_DARK, foreground="white", padding=(6, 7),
                     font=(FONT, 10, "bold"), relief="flat")
        st.map("Treeview.Heading", background=[("active", TEAL)])
        st.configure("Horizontal.TProgressbar", troughcolor="#dcebee", background=YELLOW,
                     bordercolor="#dcebee", lightcolor=YELLOW, darkcolor=YELLOW, thickness=8)
    except Exception:
        pass



def round_rect(cv, x1, y1, x2, y2, r, **kw):
    """모서리가 둥근 사각형 (캔버스 위에 부드러운 다각형으로 그린다)"""
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return cv.create_polygon(pts, smooth=True, **kw)


class RoundButton(tk.Canvas):
    """알약 모양 버튼. ttk.Button 처럼 command, state=, .state([...]) 를 쓸 수 있다."""

    COLORS = {  # (바탕, 테두리, 글자)
        "normal": ("white", "#c5d3d8", "#25323a"),
        "hover": ("#e6f4f6", TEAL, "#25323a"),
        "press": ("#cfe9ed", TEAL, "#25323a"),
        "disabled": ("#eef1f2", "#e1e7e9", "#a5b0b5"),
        "a_normal": (TEAL, TEAL, "white"),
        "a_hover": (TEAL_DARK, TEAL_DARK, "white"),
        "a_press": ("#095663", "#095663", "white"),
        "a_disabled": ("#a9d3d9", "#a9d3d9", "#f2f9fa"),
    }

    def __init__(self, parent, text="", command=None, state="normal", style="", height=36, padx=20, **_):
        self.accent = "Accent" in (style or "")
        self._font = tkfont.Font(family=FONT, size=10, weight="bold" if self.accent else "normal")
        self._text, self._cmd = text, command
        self._disabled = (str(state) == "disabled")
        self._hover = self._down = False
        w = self._font.measure(text) + 2 * padx
        super().__init__(parent, width=w, height=height, highlightthickness=0, bd=0, background=BG)
        self.bind("<Enter>", lambda e: self._set(hover=True))
        self.bind("<Leave>", lambda e: self._set(hover=False, down=False))
        self.bind("<ButtonPress-1>", lambda e: self._set(down=True))
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Configure>", lambda e: self._draw())
        self._draw()

    def _set(self, hover=None, down=None):
        if hover is not None:
            self._hover = hover
        if down is not None:
            self._down = down
        self._draw()

    def _release(self, e):
        was = self._down
        self._set(down=False)
        inside = 0 <= e.x <= self.winfo_width() and 0 <= e.y <= self.winfo_height()
        if was and inside and not self._disabled and self._cmd:
            self._cmd()

    def _draw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4:  # 아직 화면에 배치 전
            w, h = int(self["width"]), int(self["height"])
        key = "disabled" if self._disabled else ("press" if self._down else ("hover" if self._hover else "normal"))
        fill, line, fg = self.COLORS[("a_" if self.accent else "") + key]
        round_rect(self, 1, 1, w - 1, h - 1, (h - 2) / 2, fill=fill, outline=line, width=1)
        self.create_text(w / 2, h / 2, text=self._text, fill=fg, font=self._font)
        self.configure(cursor="arrow" if self._disabled else "hand2")

    def state(self, spec=None):
        """ttk 방식: state(["disabled"]) / state(["!disabled"])"""
        for sp in (spec or []):
            if sp == "disabled":
                self._disabled = True
            elif sp == "!disabled":
                self._disabled = False
        self._draw()


class RoundEntry(tk.Canvas):
    """둥근 입력칸. ttk.Entry 처럼 textvariable, width(글자 수), bind, focus_set 을 쓸 수 있다."""

    def __init__(self, parent, textvariable=None, width=20, height=36, **_):
        self._font = tkfont.Font(family=FONT, size=10)
        self._focus = False
        w = self._font.measure("0") * width + 32
        super().__init__(parent, width=w, height=height, highlightthickness=0, bd=0, background=BG)
        self.entry = tk.Entry(self, textvariable=textvariable, relief="flat", bd=0, highlightthickness=0,
                              font=self._font, background="white", foreground="#25323a",
                              insertbackground=TEAL_DARK, selectbackground="#bfe6ec", selectforeground="#0b3c46")
        self._win = self.create_window(16, height // 2, window=self.entry, anchor="w", width=w - 32)
        self.entry.bind("<FocusIn>", lambda e: self._set_focus(True))
        self.entry.bind("<FocusOut>", lambda e: self._set_focus(False))
        self.bind("<Button-1>", lambda e: self.entry.focus_set())
        self.bind("<Configure>", self._on_size)
        self._draw(w, height)

    def _on_size(self, e):
        self.itemconfigure(self._win, width=max(10, e.width - 32))
        self.coords(self._win, 16, e.height // 2)
        self._draw(e.width, e.height)

    def _set_focus(self, f):
        self._focus = f
        self._draw(self.winfo_width(), self.winfo_height())

    def _draw(self, w, h):
        self.delete("bg")
        if w < 4:
            w, h = int(self["width"]), int(self["height"])
        round_rect(self, 1, 1, w - 1, h - 1, (h - 2) / 2, fill="white", width=2 if self._focus else 1,
                   outline=TEAL if self._focus else "#c5d3d8", tags="bg")
        self.tag_lower("bg")

    # --- 안쪽 입력칸으로 위임
    def bind(self, sequence=None, func=None, add=None):
        if sequence and sequence.startswith("<Return") or sequence in ("<KeyRelease>", "<Key>"):
            return self.entry.bind(sequence, func, add)
        return super().bind(sequence, func, add)

    def focus_set(self):
        self.entry.focus_set()

    def get(self):
        return self.entry.get()

    def insert(self, i, s):
        self.entry.insert(i, s)

    def delete(self, *a):
        if a and a[0] == "all" or (a and isinstance(a[0], str) and not a[0].isdigit() and a[0] != "end"):
            return super().delete(*a)  # 캔버스 항목 지우기
        return self.entry.delete(*a)


class HeaderBar(tk.Canvas):
    """맨 위 제목 띠: 아래쪽 모서리가 둥근 청록색 카드"""

    def __init__(self, parent, title, subtitle, logo=None, height=92):
        super().__init__(parent, height=height, highlightthickness=0, bd=0, background=BG)
        self.title_t, self.sub_t, self.logo, self.h = title, subtitle, logo, height
        self.bind("<Configure>", lambda e: self._draw(e.width))

    def _draw(self, w):
        self.delete("all")
        h = self.h
        round_rect(self, 12, 10, w - 12, h - 4, 30, fill=TEAL, outline=TEAL)
        # 장식: 은은한 동그라미 몇 개
        for cx, cy, r, c in ((w - 90, 40, 20, "#27a9ba"), (w - 60, 62, 9, YELLOW), (w - 130, 58, 11, "#27a9ba")):
            self.create_oval(cx - r, cy - r, cx + r, cy + r, fill=c, outline=c)
        x = 34
        if self.logo is not None:
            self.create_image(x, h / 2 + 3, image=self.logo, anchor="w")
            x += 64 + 14
        self.create_text(x, 37, text=self.title_t, anchor="w", fill="white", font=(FONT, 18, "bold"))
        self.create_text(x, 64, text=self.sub_t, anchor="w", fill="#d4f1f5", font=(FONT, 9))


class PillTabs(tk.Frame):
    """알약 모양 탭 줄. Notebook 의 탭 대신 쓴다."""

    def __init__(self, parent, nb):
        super().__init__(parent, background=BG)
        self.nb, self.btns = nb, []
        for i, tab in enumerate(nb.tabs()):
            text = nb.tab(tab, "text").strip()
            b_ = RoundButton(self, text=text, command=lambda i=i: nb.select(i), style="Accent" if i == 0 else "", height=34, padx=22)
            b_.pack(side="left", padx=(14 if i == 0 else 6, 0), pady=(10, 4))
            self.btns.append(b_)
        nb.bind("<<NotebookTabChanged>>", lambda e: self.refresh())

    def refresh(self):
        cur = self.nb.index(self.nb.select())
        for i, b_ in enumerate(self.btns):
            b_.accent = (i == cur)
            b_._font.configure(weight="bold" if i == cur else "normal")
            b_._draw()


def asset_path(name):
    """exe 로 묶였을 때와 그냥 실행할 때 모두 같은 방식으로 그림 파일 위치를 찾는다."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / name


class QueueWriter:
    def __init__(self, q):
        self.q = q

    def write(self, s):
        if s:
            self.q.put(("log", s))

    def flush(self):
        pass


HINT = "표에서 행을 선택(Shift/Ctrl+클릭, Ctrl+A)한 뒤 Ctrl+C로 복사하시면 엑셀에 바로 붙여넣을 수 있습니다. 우클릭 메뉴도 사용할 수 있습니다."


def enable_copy(tree, numeric_cols=()):
    """표(Treeview)에서 Ctrl+A / Ctrl+C / 우클릭 복사를 지원한다. 숫자 열의 쉼표는 빼고 복사한다."""
    cols = list(tree["columns"])

    def cell(i, v):
        v = str(v)
        return v.replace(",", "") if i in numeric_cols else v

    def do_copy(event=None):
        all_ids = tree.get_children()
        ids = tree.selection() or all_ids
        lines = []
        if len(ids) == len(all_ids):  # 전체를 복사할 때만 제목 줄을 포함
            lines.append("\t".join(str(tree.heading(c)["text"]) for c in cols))
        for iid in ids:
            lines.append("\t".join(cell(i, v) for i, v in enumerate(tree.item(iid, "values"))))
        tree.clipboard_clear()
        tree.clipboard_append("\n".join(lines))
        return "break"

    def select_all(event=None):
        tree.selection_set(tree.get_children())
        return "break"

    menu = tk.Menu(tree, tearoff=0)
    menu.add_command(label="복사 (Ctrl+C)", command=do_copy)
    menu.add_command(label="전체 선택 (Ctrl+A)", command=select_all)
    for seq in ("<Control-c>", "<Control-C>"):
        tree.bind(seq, do_copy)
    for seq in ("<Control-a>", "<Control-A>"):
        tree.bind(seq, select_all)
    tree.bind("<Button-3>", lambda e: menu.tk_popup(e.x_root, e.y_root))


class BusyIndicator:
    """작업 중임을 보여준다: 움직이는 진행 막대 + "검색 중... (12초)" 처럼 경과 시간이 붙는 문구."""

    def __init__(self, widget, pbar, set_text):
        self.widget, self.pbar, self.set = widget, pbar, set_text
        self.active = False
        self.text = ""
        self.t0 = 0.0

    def start(self, text):
        self.text, self.t0 = text, time.time()
        self.pbar.stop()
        self.pbar.config(mode="indeterminate")
        self.pbar["value"] = 0
        self.pbar.start(12)
        if not self.active:
            self.active = True
            self._tick()
        else:
            self._show()

    def set_text(self, text):
        self.text = text
        self._show()

    def progress(self, done, total, text):
        """전체 쪽수를 알 때는 막대를 채워 가는 방식으로 바꾼다."""
        if str(self.pbar.cget("mode")) != "determinate":
            self.pbar.stop()
            self.pbar.config(mode="determinate")
        self.pbar.config(maximum=max(total, 1))
        self.pbar["value"] = done
        self.text = text
        self._show()

    def stop(self):
        self.active = False
        self.pbar.stop()
        self.pbar.config(mode="determinate")
        self.pbar["value"] = 0

    def _show(self):
        self.set(f"{self.text} ({int(time.time() - self.t0)}초)")

    def _tick(self):
        if not self.active:
            return
        self._show()
        self.widget.after(500, self._tick)


class AliasWindow(tk.Toplevel):
    """회사명 별칭 관리. 예) 씨제이제일제당 -> CJ제일제당"""

    def __init__(self, app):
        super().__init__(app)
        self.title("회사명 별칭 관리")
        self.configure(background=BG)
        self.geometry("600x420")
        ttk.Label(self, padding=12, wraplength=480, text=(
            "종목명의 회사명과 DART 등록 회사명이 다를 때 여기에 등록해 두시면, 앞으로 자동으로 바꿔서 검색합니다."
            " 예) 입력한 이름: 씨제이제일제당 / DART 이름: CJ제일제당")).pack(anchor="w")
        form = ttk.Frame(self, padding=(12, 0))
        form.pack(fill="x")
        self.src = tk.StringVar()
        self.dst = tk.StringVar()
        ttk.Label(form, text="입력한 이름").grid(row=0, column=0, sticky="w")
        ttk.Label(form, text="DART 등록 이름").grid(row=0, column=1, sticky="w", padx=(10, 0))
        e1 = RoundEntry(form, textvariable=self.src, width=22)
        e1.grid(row=1, column=0, sticky="w")
        e2 = RoundEntry(form, textvariable=self.dst, width=22)
        e2.grid(row=1, column=1, sticky="w", padx=(10, 0))
        e2.bind("<Return>", lambda ev: self.add())
        RoundButton(form, text="추가", command=self.add).grid(row=1, column=2, padx=(10, 0))
        e1.focus_set()
        self.msg = tk.StringVar()
        ttk.Label(self, textvariable=self.msg, padding=(12, 4)).pack(anchor="w")
        self.lb = tk.Listbox(self, height=10, activestyle="none")
        self.lb.pack(fill="both", expand=True, padx=12)
        RoundButton(self, text="선택한 별칭 삭제", command=self.remove).pack(anchor="e", padx=12, pady=12)
        self.refresh()

    def refresh(self):
        self.lb.delete(0, "end")
        self.items = sorted(b.load_aliases().items())
        for s_, d_ in self.items:
            self.lb.insert("end", f"{s_}  ->  {d_}")

    def add(self):
        s_, d_ = self.src.get().strip(), self.dst.get().strip()
        if not s_ or not d_:
            self.msg.set("두 칸을 모두 입력해 주세요.")
            return
        data = b.load_aliases()
        data = {k: v for k, v in data.items() if b._akey(k) != b._akey(s_)}
        data[s_] = d_
        b.save_aliases(data)
        self.src.set("")
        self.dst.set("")
        self.msg.set(f"'{s_}' -> '{d_}' 저장했습니다.")
        self.refresh()

    def remove(self):
        sel = self.lb.curselection()
        if not sel:
            self.msg.set("삭제할 항목을 선택해 주세요.")
            return
        key = self.items[sel[0]][0]
        data = b.load_aliases()
        data.pop(key, None)
        b.save_aliases(data)
        self.msg.set(f"'{key}' 별칭을 삭제했습니다.")
        self.refresh()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("채권간편검색기")
        try:  # 창/작업표시줄 아이콘 (bond_icon.ico 가 같은 폴더에 있으면 사용)
            self.iconbitmap(str(asset_path("bond_icon.ico")))
        except Exception:
            pass
        self.geometry("1100x640")
        self.minsize(940, 440)
        apply_theme(self)
        self.key = None
        self.corps = None
        self.busy = False
        self.resolved_isin = None
        self.resolved_name = None
        self.auto_next = False
        self.dart_cache = b.DartCache()  # 읽은 공시를 재사용 (PC에도 저장됨)
        self.last_search_name = None
        self.url = None
        self.q = queue.Queue()
        self.table_text = ""

        # 하단 워터마크 (먼저 배치해야 창을 줄여도 가려지지 않는다)
        ttk.Label(self, text="문의 - 나은", foreground="#9aa3a8", font=(FONT, 8)).pack(
            side="bottom", anchor="e", padx=10, pady=(0, 3))
        self.build_header()
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True)
        page1 = ttk.Frame(self.nb)
        self.nb.add(page1, text="  인수기관 조회  ")
        self.ktb = KtbFrame(self.nb, self)
        self.nb.add(self.ktb, text="  국고채 검색  ")
        self.bond = BondFrame(self.nb, self)
        self.nb.add(self.bond, text="  일반 채권 검색  ")
        self.tabs = PillTabs(self, self.nb)
        self.tabs.pack(fill="x", side="top", before=self.nb)

        top = ttk.Frame(page1, padding=12)
        top.pack(fill="x")
        self.isin = tk.StringVar()
        self.name = tk.StringVar()
        self.series = tk.StringVar()
        self.date = tk.StringVar()
        fields = (
            ("종목코드", self.isin, 16),
            ("회사명", self.name, 16),
            ("회차", self.series, 8),
            ("발행일 (YYYYMMDD)", self.date, 12),
        )
        cols = (0, 2, 3, 4)  # 1번 열은 [코드검색] 버튼 자리
        for (label, var, w), col in zip(fields, cols):
            ttk.Label(top, text=label).grid(row=0, column=col, sticky="w", padx=(0, 10))
            e = RoundEntry(top, textvariable=var, width=w)
            e.grid(row=1, column=col, sticky="w", padx=(0, 10))
            e.bind("<Return>", lambda ev: self.go())
            if col == 0:
                e.focus_set()
        self.code_btn = RoundButton(top, text="코드검색", command=self.on_code_button)
        self.code_btn.grid(row=1, column=1, padx=(0, 14))
        self.btn = RoundButton(top, text="인수기관 조회", command=self.step2, style="Accent.TButton")
        self.btn.grid(row=1, column=5)
        RoundButton(top, text="여러 개 일괄 조회", command=lambda: BulkWindow(self)).grid(row=1, column=6, padx=(10, 0))
        RoundButton(self.tabs, text="별칭 관리", command=lambda: AliasWindow(self), height=32, padx=16).pack(
            side="right", padx=16, pady=(10, 4))

        self.status = tk.StringVar(value="종목코드를 입력하고 엔터를 누르면 회사명/회차/발행일 입력과 인수기관 조회가 한 번에 진행됩니다. ([코드검색]은 입력까지만 합니다)")
        ttk.Label(page1, textvariable=self.status, padding=(12, 0)).pack(anchor="w")
        self.pbar = ttk.Progressbar(page1, mode="determinate")
        self.pbar.pack(fill="x", padx=12, pady=(2, 0))
        self.busy_ui = BusyIndicator(self, self.pbar, self.status.set)

        cols = ("name", "qty", "amt", "eok", "pct")
        self.tree = ttk.Treeview(page1, columns=cols, show="headings", height=9)
        for c, title, w, anchor in (
            ("name", "인수기관", 200, "w"), ("qty", "인수수량", 110, "e"),
            ("amt", "인수금액(원)", 150, "e"), ("eok", "억", 70, "e"), ("pct", "비율(%)", 80, "e"),
        ):
            self.tree.heading(c, text=title)
            self.tree.column(c, width=w, anchor=anchor)
        self.tree.tag_configure("total", font=(FONT, 10, "bold"), background="#fff3cf")
        self.tree.pack(fill="both", expand=True, padx=12, pady=(8, 4))
        enable_copy(self.tree, (1, 2))
        ttk.Label(page1, text=HINT, foreground="gray", wraplength=980).pack(anchor="w", padx=12)

        bar = ttk.Frame(page1, padding=(12, 0))
        bar.pack(fill="x")
        self.link = ttk.Label(bar, text="", foreground="#0b6e7f", cursor="hand2")
        self.link.pack(side="left")
        self.link.bind("<Button-1>", lambda e: self.url and webbrowser.open(self.url))
        self.copy_btn = RoundButton(bar, text="표 복사 (엑셀 붙여넣기용)", command=self.copy_table, state="disabled")
        self.copy_btn.pack(side="right")

        self.log = tk.Text(page1, height=5, wrap="word", state="disabled", relief="flat", background="#e9f3f5", font=(FONT, 9), padx=8, pady=6)
        self.log.pack(fill="x", padx=12, pady=12)
        self.after(400, self.ensure_keys)

    # ------------------------------------------------------------ 입력 처리
    def get_key(self):
        key = os.environ.get("DART_API_KEY", "").strip()
        f = b.HOME / "key.txt"
        if not key and f.exists():
            key = f.read_text(encoding="utf-8").strip()
        if not key:
            key = (simpledialog.askstring("OpenDART API 키", "OpenDART API 키를 입력해 주세요 (한 번만 묻고 저장합니다):", parent=self) or "").strip()
            if key:
                b.HOME.mkdir(parents=True, exist_ok=True)
                f.write_text(key, encoding="utf-8")
        return key

    def go(self):
        """엔터: 종목코드가 새로 입력됐으면 회사명/회차/발행일을 채우고 이어서 인수기관까지 조회한다."""
        if self.busy:
            return
        isin = self.isin.get().strip().upper()
        if isin and isin != self.resolved_isin:
            self.step1(isin, auto=True)
        else:
            self.step2()

    def on_code_button(self):
        if self.busy:
            return
        isin = self.isin.get().strip().upper()
        if not isin:
            self.status.set("종목코드를 먼저 입력해 주세요.")
            return
        self.step1(isin)

    def build_header(self):
        """맨 위 제목 띠: 캐릭터 + 이름 + 한 줄 설명 (아래쪽이 둥근 카드)"""
        logo = None
        try:
            logo = tk.PhotoImage(file=str(asset_path("bond_icon.png"))).subsample(4, 4)  # 256px -> 64px
        except Exception:
            pass
        self._logo = logo
        HeaderBar(self, "나봉검색기", "인수기관 · 국고채 · 일반 채권을 한 번에 찾아 보세요", logo).pack(fill="x", side="top")

    def ensure_keys(self):
        """처음 실행할 때 필요한 키(OpenDART, 공공데이터포털)를 한꺼번에 입력받는다. 저장된 키는 다시 묻지 않는다."""
        if not self.key:
            self.key = self.get_key()
        if not b.stored_datago_key():
            b.load_datago_key(lambda p: simpledialog.askstring("공공데이터포털 인증키", p, parent=self))

    def reset_result(self):
        self.tree.delete(*self.tree.get_children())
        self.link.config(text="")
        self.url = None
        self.copy_btn.state(["disabled"])
        self.set_log("")

    def start_worker(self, target, args, msg):
        self.busy = True
        self.btn.state(["disabled"])
        self.code_btn.state(["disabled"])
        self.busy_ui.start(msg)
        threading.Thread(target=target, args=args, daemon=True).start()
        self.after(100, self.poll)

    # ---- 1단계: 종목코드로 회사명/회차/발행일 채우기
    def step1(self, isin, auto=False):
        if not b.ISIN_RE.match(isin):
            self.status.set("종목코드는 KR로 시작하는 12자리입니다. 예: KR6063621G20")
            return
        datago_key = b.load_datago_key(
            lambda p: simpledialog.askstring("공공데이터포털 인증키", p, parent=self))
        if not datago_key:
            self.status.set("종목코드로 찾으려면 공공데이터포털 인증키가 필요합니다.")
            return
        self.reset_result()
        self.auto_next = auto
        self.start_worker(self.work_resolve, (isin, datago_key), "종목코드로 회사명/회차/발행일 찾는 중...")

    def work_resolve(self, isin, datago_key):
        try:
            with contextlib.redirect_stdout(QueueWriter(self.q)):
                info = b.resolve_isin(datago_key, isin)
            info["isin"] = isin
            self.q.put(("fill", info))
        except Exception as e:
            self.q.put(("error", str(e)))
        self.q.put(("done", None))

    # ---- 2단계: 입력칸(회사명/회차/발행일) 기준으로 DART에서 인수기관 검색
    def step2(self):
        if self.busy:
            return
        name = self.name.get().strip()
        series = self.series.get().strip()
        if not name or not series:
            self.status.set("종목코드를 입력하고 [코드검색]을 누르시거나, 회사명과 회차를 직접 입력해 주세요.")
            return
        issue_date = None
        raw = self.date.get().strip()
        if raw:
            digits = re.sub(r"\D", "", raw)
            if len(digits) != 8:
                self.status.set("발행일은 YYYYMMDD 8자리로 입력해 주세요. 예: 20260420")
                return
            issue_date = digits
        if not self.key:
            self.key = self.get_key()
            if not self.key:
                self.status.set("API 키가 있어야 조회할 수 있습니다.")
                return
        self.last_search_name = name
        self.reset_result()
        self.start_worker(self.work, (name, series, issue_date),
                          "인수기관 조회 중... " + ("" if issue_date else "(발행일이 없으면 오래 걸립니다)"))

    def work(self, name, series, issue_date):
        try:
            with contextlib.redirect_stdout(QueueWriter(self.q)):
                # 발행일이 있으면 회사 코드 목록 없이 바로 찾는다. (없을 때만 search_bond 안에서 내려받음)
                result = self.search(name, series, issue_date)
            self.q.put(("result", result))
        except Exception as e:  # 네트워크 오류 등
            self.q.put(("error", str(e)))
        self.q.put(("done", None))

    def search(self, name, series, issue_date):
        return b.search_bond(self.key, self.corps, name, series, issue_date, self.dart_cache, log=print)

    # ------------------------------------------------------------ 결과 표시
    def poll(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "log":
                    self.append_log(val)
                elif kind == "fill":
                    self.name.set(val["issuers"][0])
                    self.series.set(val["series"])
                    self.date.set(val["date"])
                    self.resolved_isin = val["isin"]
                    self.resolved_name = val["issuers"][0]
                    if self.auto_next:
                        self.busy_ui.set_text(f"{val['name']} 확인 완료. 인수기관 조회로 넘어갑니다")
                    else:
                        self.status.set(f"{val['name']} 입력이 완료되었습니다. [인수기관 조회]를 누르거나 엔터를 한 번 더 누르면 검색합니다.")
                elif kind == "result":
                    self.busy_ui.stop()
                    self.show_result(val)
                elif kind == "error":
                    self.busy_ui.stop()
                    self.auto_next = False
                    self.status.set("오류가 발생했습니다. 아래 내용을 확인해 주세요.")
                    self.append_log(val + "\n")
                elif kind == "done":
                    self.busy_ui.stop()
                    self.busy = False
                    self.btn.state(["!disabled"])
                    self.code_btn.state(["!disabled"])
                    if self.auto_next:  # 종목코드 엔터 한 번으로 인수기관 조회까지 이어서 진행
                        self.auto_next = False
                        if self.resolved_isin and self.resolved_isin == self.isin.get().strip().upper():
                            self.step2()
                    return
        except queue.Empty:
            pass
        self.after(100, self.poll)

    def show_result(self, res):
        corp_name, series, filing, data, seen = res
        if filing is None:
            self.status.set("찾지 못했습니다. 회사명, 회차, 발행일을 확인해 주세요.")
            if seen:
                self.append_log("검색한 공시에 있던 회차: " + ", ".join(sorted(seen)) + "\n")
            return
        self.status.set(f"{corp_name} {series} 인수기관 (접수 {filing['rcept_dt']})")
        lines = ["인수기관\t인수수량\t인수금액\t비율(%)"]
        rows = list(data["rows"])
        if data["total"]:
            q, a, p = data["total"]
            rows.append(("계", q, a, p))
        for n, q, a, p in rows:
            vals = (
                n,
                f"{q:,}" if q is not None else "",
                f"{a:,}" if a is not None else "",
                b.eok(a),
                f"{p:.2f}" if p is not None else "",
            )
            self.tree.insert("", "end", values=vals, tags=("total",) if n == "계" else ())
            lines.append("\t".join([n, str(q if q is not None else ""), str(a if a is not None else ""), f"{p:.2f}" if p is not None else ""]))
        self.table_text = "\n".join(lines)
        self.copy_btn.state(["!disabled"])
        self.url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={filing['rcept_no']}"
        self.link.config(text="DART 원문 열기 (클릭)")
        self.offer_alias()

    def offer_alias(self):
        """종목코드로 채워진 회사명을 직접 고쳐서 찾았다면, 다음부터 자동 변환하도록 저장할지 묻는다."""
        src, dst = self.resolved_name, self.last_search_name
        if not src or not dst or b._akey(src) == b._akey(dst):
            return
        if self.isin.get().strip().upper() != self.resolved_isin:
            return
        if b._akey(src) in {b._akey(k) for k in b.load_aliases()}:
            return
        if messagebox.askyesno("별칭 저장", f"'{src}' 를 앞으로 '{dst}' 로 자동 변환해서 검색할까요?", parent=self):
            data = b.load_aliases()
            data[src] = dst
            b.save_aliases(data)
            self.append_log(f"별칭을 저장했습니다: {src} -> {dst}\n")

    def copy_table(self):
        self.clipboard_clear()
        self.clipboard_append(self.table_text)
        self.status.set("표를 복사했습니다. 엑셀에 붙여넣으시면 됩니다.")

    # ------------------------------------------------------------ 로그 창
    def set_log(self, text):
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.insert("end", text)
        self.log.config(state="disabled")

    def append_log(self, text):
        self.log.config(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.config(state="disabled")


class BondFrame(ttk.Frame):
    """일반 채권 검색 탭: 종목명 또는 표준코드의 일부를 넣으면 해당하는 모든 채권을 보여준다."""

    WIDTHS = {"종목명": 190, "표준코드": 110, "채권종류": 80, "채권구분": 80, "통화": 50, "발행금액": 120,
              "발행일": 80, "만기일": 80, "지급방법": 80, "지급주기": 70, "표면금리": 70, "민평금리": 70,
              "기준금리": 70, "신용등급": 70, "공사모": 60, "순위구분": 80, "보증유형": 80}

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.q = queue.Queue()
        self.busy = False
        self.items = []
        self.rows = []
        self.cols = [c for c, _, _ in b.BOND_COLS]
        self.sort_rev = {}

        top = ttk.Frame(self, padding=(12, 12, 12, 4))
        top.pack(fill="x")
        ttk.Label(top, text="종목명 / 코드").grid(row=0, column=0, sticky="w")
        self.query = tk.StringVar()
        e = RoundEntry(top, textvariable=self.query, width=34)
        e.grid(row=1, column=0, sticky="w")
        e.bind("<Return>", lambda ev: self.start())
        self.run_btn = RoundButton(top, text="검색", command=self.start, style="Accent.TButton")
        self.run_btn.grid(row=1, column=1, padx=(8, 0))
        self.refresh_btn = RoundButton(top, text="목록 새로고침", command=lambda: self.start(refresh=True))
        self.refresh_btn.grid(row=1, column=2, padx=(8, 0))
        ttk.Label(top, foreground="gray", text=(
            "예) 수산금융  /  kr1035  /  쉼표나 줄바꿈으로 여러 개를 넣으면 모두 찾습니다. "
            "12자리 표준코드만 넣으면 바로 조회합니다.")).grid(row=2, column=0, columnspan=6, sticky="w", pady=(4, 0))

        self.msg = tk.StringVar(value="종목명이나 코드의 일부를 입력하고 엔터를 눌러 주세요.")
        ttk.Label(self, textvariable=self.msg, padding=(12, 4, 12, 0)).pack(anchor="w")
        self.pbar = ttk.Progressbar(self, mode="determinate")
        self.pbar.pack(fill="x", padx=12, pady=(2, 0))
        self.busy_ui = BusyIndicator(self, self.pbar, self.msg.set)
        self.info = tk.StringVar()
        ttk.Label(self, textvariable=self.info, foreground="gray", padding=(12, 0)).pack(anchor="w")

        bar = ttk.Frame(self, padding=(12, 6))
        bar.pack(fill="x")
        self.copy_btn = RoundButton(bar, text="표 복사", command=self.copy_all, state="disabled")
        self.copy_btn.pack(side="left")
        self.code_btn = RoundButton(bar, text="종목코드만 복사", command=self.copy_codes, state="disabled")
        self.code_btn.pack(side="left", padx=6)
        self.raw_btn = RoundButton(bar, text="원본 필드 보기", command=self.show_raw, state="disabled")
        self.raw_btn.pack(side="right")

        ttk.Label(self, foreground="gray", wraplength=960, text=(
            "표에서 행을 선택(Shift/Ctrl+클릭, Ctrl+A)해 Ctrl+C로 복사하실 수 있고, 칸을 더블클릭하면 그 칸의 값만 복사됩니다. "
            "열 제목을 누르면 정렬됩니다.")).pack(side="bottom", anchor="w", padx=12, pady=(0, 8))
        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        self.tree = ttk.Treeview(frame, columns=self.cols, show="headings")
        for c in self.cols:
            self.tree.heading(c, text=c, command=lambda c=c: self.sort_by(c))
            self.tree.column(c, width=self.WIDTHS.get(c, 80), anchor="e" if c in ("발행금액", "표면금리", "민평금리", "기준금리") else "w")
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        hsb.pack(side="bottom", fill="x")
        vsb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        enable_copy(self.tree, (self.cols.index("발행금액"),))
        self.tree.bind("<Double-1>", self.copy_cell)
        self.preloading = False
        self.update_info()

    def preload(self):
        """프로그램을 열면 오늘 채권 목록을 백그라운드로 받아 둔다. (첫 검색 대기 제거)"""
        if b.bond_db_fresh():
            return
        key = b.stored_datago_key()
        if not key:
            return
        self.preloading = True
        self.info.set("채권 목록을 백그라운드에서 준비하는 중입니다... (준비되면 검색이 바로 됩니다)")

        def run():
            try:
                b.ensure_bond_db(key, log=lambda *a, **k: None)
            except Exception:
                pass
            finally:
                self.preloading = False

        threading.Thread(target=run, daemon=True).start()
        self.after(3000, self._watch_preload)

    def _watch_preload(self):
        if self.preloading:
            self.after(3000, self._watch_preload)
        else:
            self.update_info()

    def update_info(self):
        i = b.bond_db_info()
        self.info.set(f"채권 목록 기준일: {i[0]} ({i[1]:,}건)" if i else "이름으로 검색하면 목록을 내려받을 필요 없이 바로 찾습니다. (코드 일부로 검색할 때만 전체 목록을 한 번 받습니다)")

    # ---------------------------------------------------------- 검색
    def start(self, refresh=False):
        if self.busy:
            return
        text = self.query.get().strip()
        if not text and not refresh:
            self.msg.set("종목명이나 코드의 일부를 입력해 주세요.")
            return
        key = b.load_datago_key(lambda p: simpledialog.askstring("공공데이터포털 인증키", p, parent=self))
        if not key:
            self.msg.set("공공데이터포털 인증키가 필요합니다.")
            return
        self.busy = True
        for w in (self.run_btn, self.refresh_btn, self.copy_btn, self.code_btn, self.raw_btn):
            w.state(["disabled"])
        self.busy_ui.start("채권 목록 새로고침 중..." if refresh else "검색 중...")
        threading.Thread(target=self.work, args=(key, text, refresh), daemon=True).start()
        self.after(150, self.poll)

    def work(self, key, text, refresh):
        def progress(done, total):
            self.q.put(("progress", (done, total)))
        try:
            if refresh:
                b.ensure_bond_db(key, progress=progress, force=True)
                n = (b.bond_db_info() or (None, 0))[1]
                items, trunc = (b.search_bonds(key, text, progress=progress)) if text else ([], False)
                self.q.put(("ok", (items, trunc, f"목록을 새로 받았습니다 ({n:,}건). ")))
            else:
                items, trunc = b.search_bonds(key, text, progress=progress)
                self.q.put(("ok", (items, trunc, "")))
        except Exception as e:
            self.q.put(("err", str(e)))

    def poll(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "progress":
                    self.busy_ui.progress(val[0], val[1], f"채권 목록을 내려받는 중... {val[0]}/{val[1]}쪽")
                    continue
                self.busy_ui.stop()
                self.busy = False
                self.run_btn.state(["!disabled"])
                self.refresh_btn.state(["!disabled"])
                self.update_info()
                if kind == "err":
                    self.msg.set("오류: " + val.splitlines()[0])
                    messagebox.showerror("일반 채권 검색", val, parent=self)
                else:
                    self.show(*val)
                return
        except queue.Empty:
            pass
        self.after(150, self.poll)

    def show(self, items, truncated, prefix):
        self.items = items
        self.rows = [b.map_bond(it) for it in items]
        self.tree.delete(*self.tree.get_children())
        self.tree.tag_configure("odd", background="#f1f8fa")
        for n, r in enumerate(self.rows):
            self.tree.insert("", "end", values=[r[c] for c in self.cols], tags=("odd",) if n % 2 else ())
        text = prefix + f"{len(self.rows):,}건 찾았습니다."
        if truncated:
            text += " (너무 많아 앞의 일부만 표시했습니다. 검색어를 더 구체적으로 입력해 주세요.)"
        empty = [c for c in self.cols if self.rows and not any(r[c] for r in self.rows)]
        if empty:
            text += " 이 조회 결과에 값이 없는 항목: " + ", ".join(empty)
        self.msg.set(text)
        if self.rows:
            for w in (self.copy_btn, self.code_btn, self.raw_btn):
                w.state(["!disabled"])

    # ---------------------------------------------------------- 정렬 / 복사
    def sort_by(self, col):
        rev = self.sort_rev[col] = not self.sort_rev.get(col, True)
        idx = self.cols.index(col)

        def k(iid):
            v = str(self.tree.set(iid, col))
            n = b.to_num(v) if col in ("발행금액", "표면금리", "민평금리", "기준금리") else None
            return (0, n, "") if n is not None else (1, 0, v)

        for pos, iid in enumerate(sorted(self.tree.get_children(), key=k, reverse=not rev)):
            self.tree.move(iid, "", pos)

    def copy_cell(self, event):
        if self.tree.identify_region(event.x, event.y) != "cell":
            return
        iid, colid = self.tree.identify_row(event.y), self.tree.identify_column(event.x)
        if not iid or not colid:
            return
        col = self.cols[int(colid.lstrip("#")) - 1]
        val = str(self.tree.set(iid, col))
        self.clipboard_clear()
        self.clipboard_append(val)
        self.msg.set(f"[{col}] 값을 복사했습니다: {val}")

    def _clip(self, text, msg):
        self.clipboard_clear()
        self.clipboard_append(text)
        self.msg.set(msg)

    def copy_all(self):
        lines = ["\t".join(self.cols)]
        for r in self.rows:
            lines.append("\t".join(r[c].replace(",", "") if c == "발행금액" else r[c] for c in self.cols))
        self._clip("\n".join(lines), "표를 복사했습니다. 엑셀에 붙여넣으시면 됩니다.")

    def copy_codes(self):
        self._clip("\n".join(r["표준코드"] for r in self.rows),
                   f"종목코드 {len(self.rows)}개를 복사했습니다. (일괄 조회에 바로 붙여넣을 수 있습니다)")

    def show_raw(self):
        """응답 원본의 필드 이름과 값을 보여준다. 표의 빈 칸을 맞출 때 이 내용을 알려 주시면 됩니다."""
        path = b.dump_raw_sample(self.items)
        win = tk.Toplevel(self)
        win.title("원본 필드 보기")
        win.geometry("700x520")
        txt = tk.Text(win, wrap="none")
        sb = ttk.Scrollbar(win, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        txt.pack(fill="both", expand=True)
        first = self.items[0] if self.items else {}
        txt.insert("end", "첫 번째 결과의 원본 필드 (필드이름 = 값)\n" + "-" * 50 + "\n")
        for k, v in first.items():
            txt.insert("end", f"{k} = {v}\n")
        if path:
            txt.insert("end", "\n저장 위치: " + path + "\n")
        txt.bind("<Control-a>", lambda e: (txt.tag_add("sel", "1.0", "end-1c"), "break")[1])


class KtbFrame(ttk.Frame):
    """국고채 검색 탭: 지저분한 회차 목록(25-4ㄱ, (26-5) ...)을 붙여넣으면 종목코드/종목명/발행일/만기일을 보여준다."""

    TABS = (140, 390, 510)  # 종목명, 발행일, 만기일 시작 위치(픽셀)

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.q = queue.Queue()
        self.busy = False
        self.rows = []

        ttk.Label(self, padding=(12, 10, 12, 4), wraplength=900, text=(
            "국고채 회차를 붙여넣어 주세요. 한 줄에 하나씩이든 섞여 있든, 25-4ㄱ / (26-5) / ㅎ,25-10 처럼 "
            "다른 글자가 붙어 있어도 회차(숫자-숫자)만 뽑아서 찾습니다.")).pack(anchor="w")
        self.box = tk.Text(self, height=6, wrap="word")
        self.box.pack(fill="x", padx=12)

        bar = ttk.Frame(self, padding=(12, 8))
        bar.pack(fill="x")
        self.run_btn = RoundButton(bar, text="국고채 검색", command=self.start, style="Accent.TButton")
        self.run_btn.pack(side="left")
        self.infl = tk.BooleanVar(value=False)
        self.strip = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="물가채 포함", variable=self.infl).pack(side="left", padx=(14, 4))
        ttk.Checkbutton(bar, text="원금분리채 포함", variable=self.strip).pack(side="left", padx=4)
        self.copy_btn = RoundButton(bar, text="표 복사", command=self.copy_all, state="disabled")
        self.copy_btn.pack(side="right")
        self.code_btn = RoundButton(bar, text="종목코드만 복사", command=self.copy_codes, state="disabled")
        self.code_btn.pack(side="right", padx=6)
        self.msg = tk.StringVar(value="회차를 붙여넣고 [국고채 검색]을 눌러 주세요.")
        ttk.Label(self, textvariable=self.msg, padding=(12, 0)).pack(anchor="w")
        self.pbar = ttk.Progressbar(self, mode="determinate")
        self.pbar.pack(fill="x", padx=12, pady=(2, 0))
        self.busy_ui = BusyIndicator(self, self.pbar, self.msg.set)

        ttk.Label(self, foreground="gray", wraplength=900, text=(
            "결과는 마우스로 드래그해서 선택한 뒤 Ctrl+C로 복사하실 수 있습니다. "
            "종목코드를 더블클릭하면 그 코드만 선택됩니다.")).pack(side="bottom", anchor="w", padx=12, pady=(0, 8))
        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True, padx=12, pady=8)
        self.out = tk.Text(frame, wrap="none", height=10, tabs=self.TABS, cursor="xterm",
                           relief="solid", borderwidth=1, state="disabled", exportselection=False)
        self.out.tag_configure("head", font=(FONT, 10, "bold"), background="#dcebee")
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.out.yview)
        self.out.configure(yscrollcommand=vsb.set)
        self.out.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.out.bind("<Button-1>", lambda e: self.out.focus_set())  # 클릭하면 Ctrl+C 를 받을 수 있게
        self.out.bind("<Control-a>", self.select_all)
        self.out.bind("<Control-A>", self.select_all)
        menu = tk.Menu(self.out, tearoff=0)
        menu.add_command(label="복사 (Ctrl+C)", command=lambda: self.out.event_generate("<<Copy>>"))
        menu.add_command(label="전체 선택 (Ctrl+A)", command=self.select_all)
        self.out.bind("<Button-3>", lambda e: (self.out.focus_set(), menu.tk_popup(e.x_root, e.y_root)))

    def select_all(self, event=None):
        self.out.tag_add("sel", "1.0", "end-1c")
        return "break"

    def show_rows(self, rows):
        self.out.config(state="normal")
        self.out.delete("1.0", "end")
        self.out.insert("end", "채권종목코드\t채권종목명\t발행일\t만기일\n", "head")
        for r in rows:
            self.out.insert("end", f"{r['isin']}\t{r['name']}\t{r['issued']}\t{r['maturity']}\n")
        self.out.config(state="disabled")

    def start(self):
        if self.busy:
            return
        text = self.box.get("1.0", "end")
        if not b.parse_ktb_series(text):
            self.msg.set("회차(예: 25-4)를 찾지 못했습니다. 내용을 확인해 주세요.")
            return
        key = b.load_datago_key(lambda p: simpledialog.askstring("공공데이터포털 인증키", p, parent=self))
        if not key:
            self.msg.set("공공데이터포털 인증키가 필요합니다.")
            return
        self.busy = True
        self.run_btn.state(["disabled"])
        self.copy_btn.state(["disabled"])
        self.code_btn.state(["disabled"])
        self.show_rows([])
        self.busy_ui.start("국고채 검색 중... (하루 첫 검색은 조금 걸립니다)")
        threading.Thread(target=self.work, args=(key, text, self.infl.get(), self.strip.get()), daemon=True).start()
        self.after(150, self.poll)

    def work(self, key, text, infl, strip):
        notes = []
        try:
            found, missing = b.search_ktb(key, text, infl, strip, log=notes.append)
            self.q.put(("ok", (found, missing, notes)))
        except Exception as e:
            self.q.put(("err", str(e)))

    def poll(self):
        try:
            kind, val = self.q.get_nowait()
        except queue.Empty:
            self.after(150, self.poll)
            return
        self.busy_ui.stop()
        self.busy = False
        self.run_btn.state(["!disabled"])
        if kind == "err":
            self.msg.set("오류: " + val.splitlines()[0])
            messagebox.showerror("국고채 검색", val, parent=self)
            return
        found, missing, notes = val
        self.rows = found
        self.show_rows(found)
        text = f"{len(found)}건 찾았습니다."
        if missing:
            text += " 찾지 못한 회차: " + ", ".join(missing)
        self.msg.set(text)
        if found:
            self.copy_btn.state(["!disabled"])
            self.code_btn.state(["!disabled"])

    def _clip(self, text, msg):
        self.clipboard_clear()
        self.clipboard_append(text)
        self.msg.set(msg)

    def copy_all(self):
        lines = ["채권종목코드\t채권종목명\t발행일\t만기일"]
        lines += ["\t".join([r["isin"], r["name"], r["issued"], r["maturity"]]) for r in self.rows]
        self._clip("\n".join(lines), "표를 복사했습니다. 엑셀에 붙여넣으시면 됩니다.")

    def copy_codes(self):
        self._clip("\n".join(r["isin"] for r in self.rows),
                   f"종목코드 {len(self.rows)}개를 복사했습니다. (일괄 조회에 바로 붙여넣을 수 있습니다)")


class BulkWindow(tk.Toplevel):
    """종목코드 여러 개를 붙여넣어 한 번에 조회 (동시에 최대 10개)."""

    MAX_WORKERS = 10

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("일괄 조회")
        self.configure(background=BG)
        self.geometry("1020x620")
        self.minsize(820, 420)
        self.q = queue.Queue()
        self.busy = False
        self.isins = []
        self.results = {}
        self.copy_rows = []

        ttk.Label(self, text="종목코드를 붙여넣어 주세요 (한 줄에 하나씩, 공백·쉼표로 구분하셔도 됩니다)",
                  padding=(12, 10, 12, 4)).pack(anchor="w")
        self.box = tk.Text(self, height=6, wrap="word")
        self.box.pack(fill="x", padx=12)
        self.box.focus_set()

        bar = ttk.Frame(self, padding=12)
        bar.pack(fill="x")
        self.run_btn = RoundButton(bar, text="일괄 조회", command=self.start, style="Accent.TButton")
        self.run_btn.pack(side="left")
        self.copy_btn = RoundButton(bar, text="표 복사 (엑셀 붙여넣기용)", command=self.copy, state="disabled")
        self.copy_btn.pack(side="left", padx=8)
        self.progress = tk.StringVar(value=f"동시에 {self.MAX_WORKERS}개씩 조회합니다.")
        ttk.Label(bar, textvariable=self.progress).pack(side="left", padx=8)

        cols = ("isin", "corp", "series", "date", "uw", "qty", "amt", "eok", "pct")
        ttk.Label(self, text=HINT, foreground="gray", wraplength=980).pack(side="bottom", anchor="w", padx=12, pady=(0, 8))
        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        self.tree = ttk.Treeview(frame, columns=cols, show="headings")
        for c, title, w, anchor in (
            ("isin", "종목코드", 110, "w"), ("corp", "회사명", 110, "w"), ("series", "회차", 60, "w"),
            ("date", "발행일", 80, "w"), ("uw", "인수기관", 170, "w"), ("qty", "인수수량", 90, "e"),
            ("amt", "인수금액(원)", 120, "e"), ("eok", "억", 60, "e"), ("pct", "비율(%)", 60, "e"),
        ):
            self.tree.heading(c, text=title)
            self.tree.column(c, width=w, anchor=anchor)
        self.tree.tag_configure("total", font=(FONT, 10, "bold"), background="#fff3cf")
        self.tree.tag_configure("fail", foreground="#b3261e")
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        enable_copy(self.tree, (5, 6))

    # ---------------------------------------------------------- 시작
    def start(self):
        if self.busy:
            return
        toks = []
        for t in re.split(r"[\s,;]+", self.box.get("1.0", "end").strip().upper()):
            if t and t not in toks:
                toks.append(t)
        if not toks:
            self.progress.set("종목코드를 먼저 붙여넣어 주세요.")
            return
        self.datago_key = b.load_datago_key(
            lambda p: simpledialog.askstring("공공데이터포털 인증키", p, parent=self))
        if not self.datago_key:
            self.progress.set("공공데이터포털 인증키가 필요합니다.")
            return
        if not self.app.key:
            self.app.key = self.app.get_key()
        if not self.app.key:
            self.progress.set("OpenDART API 키가 필요합니다.")
            return
        self.isins = toks
        self.results = {}
        self.cache = b.DartCache()
        self.busy = True
        self.run_btn.state(["disabled"])
        self.copy_btn.state(["disabled"])
        self.progress.set(f"0/{len(toks)} 완료")
        self.render()
        threading.Thread(target=self.run, daemon=True).start()
        self.after(150, self.poll)

    # ---------------------------------------------------------- 조회 (스레드)
    def run(self):
        try:
            with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as ex:
                futs = [ex.submit(self.process, i, c) for i, c in enumerate(self.isins)]
                for fu in as_completed(futs):
                    self.q.put(("item", fu.result()))
        except Exception as e:
            self.q.put(("fatal", str(e)))
        self.q.put(("done", None))

    def process(self, idx, isin):
        notes = []
        if not b.ISIN_RE.match(isin):
            return idx, {"isin": isin, "error": "종목코드 형식이 아닙니다 (KR로 시작하는 12자리)"}
        try:
            info = b.resolve_isin(self.datago_key, isin)
            for nm in info["issuers"]:
                corp, series, filing, data, seen = b.search_bond(
                    self.app.key, self.app.corps, nm, info["series"], info["date"], self.cache, log=notes.append)
                if filing is not None:
                    return idx, {"isin": isin, "info": info, "corp": corp, "filing": filing, "data": data}
            why = notes[0] if notes else "해당 회차의 표가 공시에 없습니다"
            return idx, {"isin": isin, "info": info, "error": "인수기관을 찾지 못했습니다 - " + why}
        except Exception as e:
            return idx, {"isin": isin, "error": str(e).strip().splitlines()[0] if str(e).strip() else "알 수 없는 오류"}

    # ---------------------------------------------------------- 화면
    def poll(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "item":
                    idx, res = val
                    self.results[idx] = res
                    self.progress.set(f"{len(self.results)}/{len(self.isins)} 완료")
                    self.render()
                elif kind == "fatal":
                    self.progress.set("오류: " + val.splitlines()[0])
                elif kind == "done":
                    self.busy = False
                    self.run_btn.state(["!disabled"])
                    if self.copy_rows:
                        self.copy_btn.state(["!disabled"])
                    fails = sum(1 for r in self.results.values() if r.get("error"))
                    self.progress.set(f"완료: {len(self.results)}건 중 {fails}건을 찾지 못했습니다" if fails
                                      else f"완료: {len(self.results)}건 모두 찾았습니다")
                    return
        except queue.Empty:
            pass
        self.after(150, self.poll)

    def render(self):
        self.tree.delete(*self.tree.get_children())
        self.copy_rows = []
        for idx, isin in enumerate(self.isins):
            res = self.results.get(idx)
            if res is None:
                self.tree.insert("", "end", values=(isin, "", "", "", "조회 중...", "", "", "", ""))
                continue
            info = res.get("info") or {}
            corp = res.get("corp") or (info.get("issuers") or [""])[0]
            series, date = info.get("series", ""), info.get("date", "")
            if res.get("error"):
                self.tree.insert("", "end", tags=("fail",),
                                 values=(isin, corp, series, date, "※ " + res["error"], "", "", "", ""))
                continue
            data = res["data"]
            rows = list(data["rows"])
            if data["total"]:
                rows.append(("계",) + tuple(data["total"]))
            for n, q, a, p in rows:
                self.tree.insert("", "end", tags=("total",) if n == "계" else (), values=(
                    isin, corp, series, date, n,
                    f"{q:,}" if q is not None else "", f"{a:,}" if a is not None else "",
                    b.eok(a), f"{p:.2f}" if p is not None else ""))
                self.copy_rows.append((isin, corp, series, date, n, q, a, p))

    def copy(self):
        lines = ["종목코드\t회사명\t회차\t발행일\t인수기관\t인수수량\t인수금액\t비율(%)"]
        for r in self.copy_rows:
            lines.append("\t".join("" if v is None else str(v) for v in r))
        self.clipboard_clear()
        self.clipboard_append("\n".join(lines))
        self.progress.set("표를 복사했습니다. 엑셀에 붙여넣으시면 됩니다.")


if __name__ == "__main__":
    App().mainloop()
