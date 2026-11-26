"""
Icon Exporter: one image in, every app icon out (Web, PWA, Apple, Windows, Android).
Light, simple desktop app. Works on Windows, macOS and Linux.

Run from source:   pip install pillow resvg-py   then   python icon_exporter.py
Build an app:      python build.py
"""
import base64
import json
import os
import platform
import queue
import struct
import subprocess
import threading
import traceback
from io import BytesIO
from pathlib import Path

import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk

from PIL import Image, ImageDraw

# --------------------------------------------------------------------------- core settings
RASTER_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
INPUT_EXT = {".svg"} | RASTER_EXT

SMALL_SIZE_LIMIT = 32
ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]
MAC_ICON_SIZES = [16, 32, 64, 128, 256, 512, 1024]
MAC_PADDING = (1024 - 824) / 2 / 1024
MASKABLE_PADDING = 0.10
ANDROID_ADAPTIVE_PADDING = (1 - 66 / 108) / 2
IOS_SIZES = [57, 60, 72, 76, 114, 120, 144, 152, 167, 180, 1024]
ANDROID_DENSITIES = {"mdpi": 1, "hdpi": 1.5, "xhdpi": 2, "xxhdpi": 3, "xxxhdpi": 4}


class Job:
    """Everything one export run needs (kept off globals so the GUI stays simple)."""

    def __init__(self, themes: dict, out_root: Path, app_name: str, short_name: str):
        self.themes = themes          # {name: {"src": Path, "bg": "#RRGGBB"}}
        self.out_root = out_root
        self.app_name = app_name
        self.short_name = short_name


# --------------------------------------------------------------------------- rendering
def _render_svg_bytes(svg: Path, width, height) -> bytes:
    try:
        import resvg_py
        kw = {"svg_path": str(svg)}
        if width:
            kw["width"] = int(width)
        if height:
            kw["height"] = int(height)
        return bytes(resvg_py.svg_to_bytes(**kw))
    except ImportError:
        pass
    try:
        import cairosvg
        return cairosvg.svg2png(url=str(svg), output_width=width, output_height=height)
    except (ImportError, OSError):
        raise RuntimeError("No SVG renderer found. Install one with: pip install resvg-py")


def rasterize(src: Path, width=None, height=None) -> Image.Image:
    if src.suffix.lower() in RASTER_EXT:
        im = Image.open(src).convert("RGBA")
        if width:
            return im.resize((width, max(1, round(im.height * width / im.width))), Image.Resampling.LANCZOS)
        if height:
            return im.resize((max(1, round(im.width * height / im.height)), height), Image.Resampling.LANCZOS)
        return im
    return Image.open(BytesIO(_render_svg_bytes(src, width, height))).convert("RGBA")


def render(job: Job, theme: str, size: int, padding: float = 0.0, bg=None) -> Image.Image:
    src = job.themes[theme]["src"]
    small = src.parent / f"{theme}-small.svg"
    if size <= SMALL_SIZE_LIMIT and small.exists():
        src = small
    inner = max(1, round(size * (1 - 2 * padding)))
    art = rasterize(src, width=inner)
    if art.height > inner:
        art = rasterize(src, height=inner)
    canvas = Image.new("RGBA", (size, size), bg or (0, 0, 0, 0))
    canvas.alpha_composite(art, ((size - art.width) // 2, (size - art.height) // 2))
    return canvas


def render_wide(job: Job, theme: str, w: int, h: int, bg: str) -> Image.Image:
    canvas = Image.new("RGBA", (w, h), bg)
    art = render(job, theme, int(h * 0.7))
    canvas.alpha_composite(art, ((w - art.width) // 2, (h - art.height) // 2))
    return canvas


def circle_mask(img: Image.Image) -> Image.Image:
    k = 4
    mask = Image.new("L", (img.width * k,) * 2, 0)
    ImageDraw.Draw(mask).ellipse((0, 0, mask.width - 1, mask.height - 1), fill=255)
    out = img.copy()
    out.putalpha(mask.resize(img.size, Image.Resampling.LANCZOS))
    return out


def save_png(img: Image.Image, path: Path, opaque: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if opaque:
        img = img.convert("RGB")
    img.save(path, format="PNG", optimize=True)


def write_ico(frames, path: Path) -> None:
    blobs = []
    for f in frames:
        buf = BytesIO()
        f.save(buf, format="PNG", optimize=True)
        blobs.append(buf.getvalue())
    header = struct.pack("<HHH", 0, 1, len(frames))
    offset = 6 + 16 * len(frames)
    entries = b""
    for f, blob in zip(frames, blobs):
        w = 0 if f.width >= 256 else f.width
        h = 0 if f.height >= 256 else f.height
        entries += struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + entries + b"".join(blobs))


# --------------------------------------------------------------------------- platform builders
def build_web(job: Job, theme: str, out: Path) -> None:
    d = out / "web"
    for s in (16, 32, 48, 96):
        save_png(render(job, theme, s), d / f"favicon-{s}x{s}.png")
    write_ico([render(job, theme, s) for s in ICO_SIZES], d / "favicon.ico")
    src = job.themes[theme]["src"]
    if src.suffix.lower() == ".svg":
        (d / "favicon.svg").write_bytes(src.read_bytes())
    save_png(render(job, theme, 192), d / "icon-192.png")
    save_png(render(job, theme, 512), d / "icon-512.png")
    bg = job.themes[theme]["bg"]
    (d / "head-snippet.html").write_text(
        f"""<link rel="icon" href="/favicon.ico" sizes="48x48">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="icon" type="image/png" sizes="32x32" href="/favicon-32x32.png">
<link rel="icon" type="image/png" sizes="16x16" href="/favicon-16x16.png">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<link rel="manifest" href="/manifest.webmanifest">
<meta name="theme-color" content="{bg}">
<meta name="msapplication-config" content="/browserconfig.xml">
""", encoding="utf-8")


def build_pwa(job: Job, theme: str, out: Path) -> None:
    d = out / "pwa"
    bg = job.themes[theme]["bg"]
    for s in (72, 96, 128, 144, 152, 192, 384, 512):
        save_png(render(job, theme, s), d / f"pwa-{s}x{s}.png")
    for s in (192, 512):
        save_png(render(job, theme, s, padding=MASKABLE_PADDING, bg=bg),
                 d / f"pwa-maskable-{s}x{s}.png", opaque=True)
    manifest = {
        "name": job.app_name, "short_name": job.short_name, "start_url": "/",
        "display": "standalone", "background_color": bg, "theme_color": bg,
        "icons": [
            {"src": "/pwa-192x192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "/pwa-512x512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "/pwa-maskable-192x192.png", "sizes": "192x192", "type": "image/png", "purpose": "maskable"},
            {"src": "/pwa-maskable-512x512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }
    (d / "manifest.webmanifest").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def build_apple(job: Job, theme: str, out: Path) -> None:
    d = out / "apple"
    bg = job.themes[theme]["bg"]
    for s in IOS_SIZES:
        save_png(render(job, theme, s, padding=0.12, bg=bg), d / "ios" / f"icon-{s}x{s}.png", opaque=True)
    save_png(render(job, theme, 180, padding=0.12, bg=bg), d / "apple-touch-icon.png", opaque=True)

    frames = {s: render(job, theme, s, padding=MAC_PADDING) for s in MAC_ICON_SIZES}
    icns = d / "macos" / "AppIcon.icns"
    icns.parent.mkdir(parents=True, exist_ok=True)
    frames[1024].save(icns, format="ICNS",
                      append_images=[f for s, f in frames.items() if s != 1024])
    iconset = d / "macos" / "AppIcon.iconset"
    for s in (16, 32, 128, 256, 512):
        save_png(render(job, theme, s, padding=MAC_PADDING), iconset / f"icon_{s}x{s}.png")
        save_png(render(job, theme, s * 2, padding=MAC_PADDING), iconset / f"icon_{s}x{s}@2x.png")


def build_windows(job: Job, theme: str, out: Path) -> None:
    d = out / "windows"
    bg = job.themes[theme]["bg"]
    write_ico([render(job, theme, s) for s in ICO_SIZES], d / "app.ico")
    for s in (70, 144, 150, 310):
        save_png(render(job, theme, s, padding=0.15, bg=bg), d / f"mstile-{s}x{s}.png", opaque=True)
    save_png(render_wide(job, theme, 310, 150, bg), d / "mstile-310x150.png", opaque=True)
    for s in (44, 50, 71, 89, 107, 142, 150, 284, 310):
        save_png(render(job, theme, s), d / "store" / f"Square{s}x{s}Logo.png")
    (d / "browserconfig.xml").write_text(
        f"""<?xml version="1.0" encoding="utf-8"?>
<browserconfig>
  <msapplication>
    <tile>
      <square70x70logo src="/mstile-70x70.png"/>
      <square150x150logo src="/mstile-150x150.png"/>
      <wide310x150logo src="/mstile-310x150.png"/>
      <square310x310logo src="/mstile-310x310.png"/>
      <TileColor>{bg}</TileColor>
    </tile>
  </msapplication>
</browserconfig>
""", encoding="utf-8")


def build_android(job: Job, theme: str, out: Path) -> None:
    d = out / "android"
    bg = job.themes[theme]["bg"]
    save_png(render(job, theme, 192), d / "android-chrome-192x192.png")
    save_png(render(job, theme, 512), d / "android-chrome-512x512.png")
    save_png(render(job, theme, 512, padding=0.08, bg=bg), d / "play-store-512.png", opaque=True)
    for name, scale in ANDROID_DENSITIES.items():
        folder = d / "res" / f"mipmap-{name}"
        legacy = render(job, theme, round(48 * scale), padding=0.08, bg=bg)
        save_png(legacy, folder / "ic_launcher.png", opaque=True)
        save_png(circle_mask(legacy), folder / "ic_launcher_round.png")
        save_png(render(job, theme, round(108 * scale), padding=ANDROID_ADAPTIVE_PADDING),
                 folder / "ic_launcher_foreground.png")
    adaptive = """<?xml version="1.0" encoding="utf-8"?>
<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">
    <background android:drawable="@color/ic_launcher_background"/>
    <foreground android:drawable="@mipmap/ic_launcher_foreground"/>
</adaptive-icon>
"""
    anydpi = d / "res" / "mipmap-anydpi-v26"
    anydpi.mkdir(parents=True, exist_ok=True)
    (anydpi / "ic_launcher.xml").write_text(adaptive, encoding="utf-8")
    (anydpi / "ic_launcher_round.xml").write_text(adaptive, encoding="utf-8")
    values = d / "res" / "values"
    values.mkdir(parents=True, exist_ok=True)
    (values / "ic_launcher_background.xml").write_text(
        f"""<?xml version="1.0" encoding="utf-8"?>
<resources>
    <color name="ic_launcher_background">{bg}</color>
</resources>
""", encoding="utf-8")


PLATFORMS = [
    ("Web", "Web favicon + icons", build_web),
    ("PWA", "PWA (manifest + maskable)", build_pwa),
    ("Apple", "Apple iOS + macOS", build_apple),
    ("Windows", "Windows (ICO + tiles)", build_windows),
    ("Android", "Android (adaptive + Play)", build_android),
]


def run_job(job: Job, chosen: list[str], log, progress) -> None:
    steps = [(label, fn) for key, label, fn in PLATFORMS if key in chosen]
    total = len(steps) * len(job.themes)
    done = 0
    for theme in job.themes:
        out = job.out_root / theme
        out.mkdir(parents=True, exist_ok=True)
        for label, fn in steps:
            log(f"[{theme}] {label}...")
            fn(job, theme, out)
            done += 1
            progress(done / total * 100)
    log("Finished.")


def open_folder(path: Path) -> None:
    try:
        system = platform.system()
        if system == "Windows":
            os.startfile(path)  # type: ignore[attr-defined]
        elif system == "Darwin":
            subprocess.run(["open", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
    except Exception:
        pass


# --------------------------------------------------------------------------- GUI (light theme)
BG = "#F4F6F9"
CARD = "#FFFFFF"
BORDER = "#DDE2EA"
TEXT = "#1F2937"
MUTED = "#6B7280"
ACCENT = "#2563EB"
ACCENT_DARK = "#1D4ED8"


def enable_dpi_awareness() -> None:
    if platform.system() == "Windows":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass


def valid_hex(c: str) -> str | None:
    c = c.strip()
    if not c.startswith("#"):
        c = "#" + c
    if len(c) in (4, 7) and all(ch in "0123456789abcdefABCDEF" for ch in c[1:]):
        if len(c) == 4:
            c = "#" + "".join(ch * 2 for ch in c[1:])
        return c.upper()
    return None


class App:
    def __init__(self) -> None:
        enable_dpi_awareness()
        self.root = tk.Tk()
        self.root.title("Icon Exporter")
        self.root.configure(bg=BG)
        self.root.geometry("780x880")
        self.root.minsize(700, 800)
        self.q: queue.Queue = queue.Queue()
        self.running = False
        self.last_out: Path | None = None
        self.preview_img = None

        self.v_image = tk.StringVar()
        self.v_use_dark = tk.BooleanVar(value=False)
        self.v_dark = tk.StringVar()
        self.v_out = tk.StringVar()
        self.v_name = tk.StringVar(value="My App")
        self.v_short = tk.StringVar(value="MyApp")
        self.v_bg = tk.StringVar(value="#FFFFFF")
        self.v_bg_dark = tk.StringVar(value="#0B0F19")
        self.v_platforms = {k: tk.BooleanVar(value=True) for k, _, _ in PLATFORMS}

        self._style()
        self._build()
        self.v_image.trace_add("write", lambda *_: self._on_image_change())
        self.v_use_dark.trace_add("write", lambda *_: self._toggle_dark())
        self.root.after(100, self._poll)

    # ---- styling
    def _style(self) -> None:
        st = ttk.Style(self.root)
        st.theme_use("clam")
        base = ("Segoe UI" if platform.system() == "Windows" else "Helvetica", 10)
        self.f_base = base
        self.f_bold = (base[0], 10, "bold")
        self.f_title = (base[0], 20, "bold")
        st.configure(".", font=base, background=CARD, foreground=TEXT)
        st.configure("TEntry", fieldbackground="#FFFFFF", bordercolor=BORDER, lightcolor=BORDER,
                     darkcolor=BORDER, padding=6, foreground=TEXT)
        st.map("TEntry", bordercolor=[("focus", ACCENT)], lightcolor=[("focus", ACCENT)],
               darkcolor=[("focus", ACCENT)])
        st.configure("TButton", background="#EEF1F6", foreground=TEXT, bordercolor=BORDER,
                     lightcolor="#EEF1F6", darkcolor="#EEF1F6", padding=(12, 6), focusthickness=0)
        st.map("TButton", background=[("active", "#E2E7EF"), ("disabled", "#F3F4F6")],
               foreground=[("disabled", "#9CA3AF")])
        st.configure("Accent.TButton", background=ACCENT, foreground="#FFFFFF", bordercolor=ACCENT,
                     lightcolor=ACCENT, darkcolor=ACCENT, padding=(18, 10), font=self.f_bold)
        st.map("Accent.TButton", background=[("active", ACCENT_DARK), ("disabled", "#93B4F5")],
               foreground=[("disabled", "#FFFFFF")], bordercolor=[("active", ACCENT_DARK)])
        st.configure("TCheckbutton", background=CARD, foreground=TEXT, focusthickness=0)
        st.map("TCheckbutton", background=[("active", CARD)], indicatorcolor=[("selected", ACCENT)])
        st.configure("Accent.Horizontal.TProgressbar", troughcolor="#E5E9F0", background=ACCENT,
                     bordercolor="#E5E9F0", lightcolor=ACCENT, darkcolor=ACCENT, thickness=10)

    def _card(self, parent, title: str) -> tk.Frame:
        wrap = tk.Frame(parent, bg=BG)
        wrap.pack(fill="x", pady=(0, 12))
        tk.Label(wrap, text=title.upper(), bg=BG, fg=MUTED, font=(self.f_base[0], 8, "bold")).pack(anchor="w")
        box = tk.Frame(wrap, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        box.pack(fill="x", pady=(4, 0))
        inner = tk.Frame(box, bg=CARD)
        inner.pack(fill="x", padx=14, pady=12)
        return inner

    def _build(self) -> None:
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=22, pady=18)

        tk.Label(body, text="Icon Exporter", bg=BG, fg=TEXT, font=self.f_title).pack(anchor="w")
        tk.Label(body, text="Pick one image and get every icon for Web, PWA, Apple, Windows and Android.",
                 bg=BG, fg=MUTED, font=self.f_base).pack(anchor="w", pady=(2, 14))

        # image card with preview
        c = self._card(body, "1. Your image")
        row = tk.Frame(c, bg=CARD)
        row.pack(fill="x")
        left = tk.Frame(row, bg=CARD)
        left.pack(side="left", fill="x", expand=True)
        tk.Label(left, text="SVG (best quality) or PNG / JPG / WEBP", bg=CARD, fg=MUTED).pack(anchor="w")
        r = tk.Frame(left, bg=CARD)
        r.pack(fill="x", pady=(6, 0))
        ttk.Entry(r, textvariable=self.v_image).pack(side="left", fill="x", expand=True)
        ttk.Button(r, text="Browse...", command=lambda: self._pick_image(self.v_image)).pack(side="left", padx=(8, 0))

        dark_row = tk.Frame(left, bg=CARD)
        dark_row.pack(fill="x", pady=(10, 0))
        ttk.Checkbutton(dark_row, text="I also have a dark version", variable=self.v_use_dark).pack(anchor="w")
        d = tk.Frame(left, bg=CARD)
        d.pack(fill="x", pady=(6, 0))
        self.e_dark = ttk.Entry(d, textvariable=self.v_dark, state="disabled")
        self.e_dark.pack(side="left", fill="x", expand=True)
        self.b_dark = ttk.Button(d, text="Browse...", state="disabled",
                                 command=lambda: self._pick_image(self.v_dark))
        self.b_dark.pack(side="left", padx=(8, 0))

        pv = tk.Frame(row, bg="#EEF1F6", width=112, height=112, highlightbackground=BORDER, highlightthickness=1)
        pv.pack(side="right", padx=(16, 0))
        pv.pack_propagate(False)
        self.lbl_preview = tk.Label(pv, text="No image", bg="#EEF1F6", fg=MUTED)
        self.lbl_preview.pack(expand=True)

        # output card
        c = self._card(body, "2. Save to")
        r = tk.Frame(c, bg=CARD)
        r.pack(fill="x")
        ttk.Entry(r, textvariable=self.v_out).pack(side="left", fill="x", expand=True)
        ttk.Button(r, text="Choose folder...", command=self._pick_out).pack(side="left", padx=(8, 0))

        # details card
        c = self._card(body, "3. Details")
        g = tk.Frame(c, bg=CARD)
        g.pack(fill="x")
        g.columnconfigure(1, weight=1)
        g.columnconfigure(3, weight=1)
        tk.Label(g, text="App name", bg=CARD, fg=MUTED).grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(g, textvariable=self.v_name).grid(row=0, column=1, sticky="ew", padx=(8, 16), pady=4)
        tk.Label(g, text="Short name", bg=CARD, fg=MUTED).grid(row=0, column=2, sticky="w", pady=4)
        ttk.Entry(g, textvariable=self.v_short).grid(row=0, column=3, sticky="ew", padx=(8, 0), pady=4)

        tk.Label(g, text="Background", bg=CARD, fg=MUTED).grid(row=1, column=0, sticky="w", pady=4)
        self._color_row(g, 1, 1, self.v_bg)
        self.lbl_bg_dark = tk.Label(g, text="Dark background", bg=CARD, fg=MUTED)
        self.lbl_bg_dark.grid(row=1, column=2, sticky="w", pady=4)
        self.color_dark_frame = self._color_row(g, 1, 3, self.v_bg_dark)
        tk.Label(c, text="Background is used where transparency isn't allowed (iOS, Android adaptive, tiles, maskable).",
                 bg=CARD, fg=MUTED, font=(self.f_base[0], 9)).pack(anchor="w", pady=(6, 0))

        # platforms
        c = self._card(body, "4. Platforms")
        pf = tk.Frame(c, bg=CARD)
        pf.pack(fill="x")
        for i, (key, label, _) in enumerate(PLATFORMS):
            ttk.Checkbutton(pf, text=label, variable=self.v_platforms[key]).grid(
                row=i // 3, column=i % 3, sticky="w", padx=(0, 22), pady=3)

        # action
        act = tk.Frame(body, bg=BG)
        act.pack(fill="x", pady=(4, 8))
        self.btn_go = ttk.Button(act, text="Generate icons", style="Accent.TButton", command=self._start)
        self.btn_go.pack(side="left")
        self.btn_open = ttk.Button(act, text="Open folder", command=self._open_out, state="disabled")
        self.btn_open.pack(side="left", padx=(10, 0))
        self.lbl_status = tk.Label(act, text="Ready", bg=BG, fg=MUTED)
        self.lbl_status.pack(side="right")

        self.bar = ttk.Progressbar(body, style="Accent.Horizontal.TProgressbar", maximum=100)
        self.bar.pack(fill="x")

        self.log = tk.Text(body, height=6, bg="#FAFBFC", fg=MUTED, relief="flat", wrap="word",
                           highlightbackground=BORDER, highlightthickness=1, font=(self.f_base[0], 9),
                           state="disabled", padx=8, pady=6)
        self.log.pack(fill="both", expand=True, pady=(10, 0))
        self._toggle_dark()

    def _color_row(self, parent, row, col, var) -> tk.Frame:
        f = tk.Frame(parent, bg=CARD)
        f.grid(row=row, column=col, sticky="ew", padx=(8, 16 if col == 1 else 0), pady=4)
        sw = tk.Label(f, width=3, bg=var.get(), relief="flat", highlightbackground=BORDER,
                      highlightthickness=1, cursor="hand2")
        sw.pack(side="left", ipady=4)
        ttk.Entry(f, textvariable=var, width=10).pack(side="left", padx=(8, 0), fill="x", expand=True)

        def refresh(*_):
            c = valid_hex(var.get())
            if c:
                sw.configure(bg=c)

        def choose(_e=None):
            res = colorchooser.askcolor(color=valid_hex(var.get()) or "#FFFFFF", parent=self.root)
            if res and res[1]:
                var.set(res[1].upper())

        var.trace_add("write", refresh)
        sw.bind("<Button-1>", choose)
        f._sw = sw  # type: ignore[attr-defined]
        return f

    # ---- events
    def _toggle_dark(self) -> None:
        on = self.v_use_dark.get()
        state = "normal" if on else "disabled"
        self.e_dark.configure(state=state)
        self.b_dark.configure(state=state)
        fg = MUTED if on else "#C3C9D3"
        self.lbl_bg_dark.configure(fg=fg)
        for w in self.color_dark_frame.winfo_children():
            if isinstance(w, ttk.Entry):
                w.configure(state=state)

    def _pick_image(self, var: tk.StringVar) -> None:
        exts = " ".join(f"*{e}" for e in sorted(INPUT_EXT))
        p = filedialog.askopenfilename(
            parent=self.root, title="Select image",
            filetypes=[("SVG or image", exts), ("SVG", "*.svg"), ("All files", "*.*")])
        if p:
            var.set(p)

    def _pick_out(self) -> None:
        p = filedialog.askdirectory(parent=self.root, title="Choose the folder to save the icons in",
                                    mustexist=False)
        if p:
            self.v_out.set(p)

    def _on_image_change(self) -> None:
        p = Path(self.v_image.get().strip().strip('"'))
        if not p.is_file() or p.suffix.lower() not in INPUT_EXT:
            self.lbl_preview.configure(image="", text="No image")
            self.preview_img = None
            return
        if not self.v_out.get().strip():
            self.v_out.set(str(p.parent / "generated"))
        try:
            art = rasterize(p, width=96)
            if art.height > 96:
                art = rasterize(p, height=96)
            canvas = Image.new("RGBA", (96, 96), "#EEF1F6")
            canvas.alpha_composite(art, ((96 - art.width) // 2, (96 - art.height) // 2))
            buf = BytesIO()
            canvas.save(buf, format="PNG")
            self.preview_img = tk.PhotoImage(data=base64.b64encode(buf.getvalue()))
            self.lbl_preview.configure(image=self.preview_img, text="")
        except Exception:
            self.lbl_preview.configure(image="", text="No preview")
            self.preview_img = None

    def _open_out(self) -> None:
        if self.last_out:
            open_folder(self.last_out)

    # ---- running
    def _write_log(self, msg: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _start(self) -> None:
        if self.running:
            return
        img = Path(self.v_image.get().strip().strip('"')).expanduser()
        if not img.is_file() or img.suffix.lower() not in INPUT_EXT:
            messagebox.showwarning("Icon Exporter", "Please choose an image (SVG, PNG, JPG or WEBP).")
            return
        out = self.v_out.get().strip().strip('"')
        if not out:
            messagebox.showwarning("Icon Exporter", "Please choose a folder to save the icons in.")
            return
        bg = valid_hex(self.v_bg.get())
        if not bg:
            messagebox.showwarning("Icon Exporter", "The background color must be a hex code like #FFFFFF.")
            return
        chosen = [k for k, v in self.v_platforms.items() if v.get()]
        if not chosen:
            messagebox.showwarning("Icon Exporter", "Select at least one platform.")
            return

        if img.suffix.lower() in RASTER_EXT:
            with Image.open(img) as im:
                if min(im.size) < 1024 and not messagebox.askyesno(
                        "Low resolution",
                        f"This image is {im.width}x{im.height}px. Large icons will be upscaled and look soft.\n\n"
                        "An SVG or a 1024x1024+ image gives the best result. Continue anyway?"):
                    return

        if self.v_use_dark.get():
            dark = Path(self.v_dark.get().strip().strip('"')).expanduser()
            dbg = valid_hex(self.v_bg_dark.get())
            if not dark.is_file() or dark.suffix.lower() not in INPUT_EXT:
                messagebox.showwarning("Icon Exporter", "Please choose the dark version image.")
                return
            if not dbg:
                messagebox.showwarning("Icon Exporter", "The dark background must be a hex code like #0B0F19.")
                return
            themes = {"light": {"src": img, "bg": bg}, "dark": {"src": dark, "bg": dbg}}
        else:
            themes = {"icons": {"src": img, "bg": bg}}

        out_root = Path(out).expanduser()
        job = Job(themes, out_root, self.v_name.get().strip() or "My App",
                  self.v_short.get().strip() or "MyApp")

        self.running = True
        self.last_out = out_root
        self.btn_go.configure(state="disabled")
        self.btn_open.configure(state="disabled")
        self.bar["value"] = 0
        self.lbl_status.configure(text="Working...")
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

        def worker():
            try:
                run_job(job, chosen, lambda m: self.q.put(("log", m)), lambda v: self.q.put(("progress", v)))
                self.q.put(("done", None))
            except Exception as e:
                self.q.put(("error", f"{e}\n\n{traceback.format_exc(limit=3)}"))

        threading.Thread(target=worker, daemon=True).start()

    def _poll(self) -> None:
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "log":
                    self._write_log(val)
                elif kind == "progress":
                    self.bar["value"] = val
                elif kind == "done":
                    self.running = False
                    self.btn_go.configure(state="normal")
                    self.btn_open.configure(state="normal")
                    self.lbl_status.configure(text="Done!", fg="#059669")
                    self._write_log(f"Saved in: {self.last_out}")
                    open_folder(self.last_out)
                elif kind == "error":
                    self.running = False
                    self.btn_go.configure(state="normal")
                    self.lbl_status.configure(text="Failed", fg="#DC2626")
                    self._write_log(val)
                    messagebox.showerror("Icon Exporter", f"Something went wrong:\n\n{val}")
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    App().run()


if __name__ == "__main__":
    main()
