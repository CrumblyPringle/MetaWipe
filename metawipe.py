import os
import sys
import io
import struct
import shutil
import tempfile
import threading
import traceback
from pathlib import Path
from datetime import datetime

# ── GUI ─────────────────────────────────────────────────────────────────────
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# ── Image / document libs ────────────────────────────────────────────────────
from PIL import Image
import piexif
import pikepdf
import mutagen


class CleanResult:
    def __init__(self, path: Path, success: bool, removed: list[str], note: str = ""):
        self.path = path
        self.success = success
        self.removed = removed
        self.note = note


def clean_jpeg(src: Path, dst: Path) -> CleanResult:
    removed = []
    img = Image.open(src)

    # Strip all EXIF
    data = img.info
    if "exif" in data:
        removed.append("EXIF data")
    if "icc_profile" in data:
        removed.append("ICC profile")
    if "xmp" in data:
        removed.append("XMP metadata")
    if "comment" in data:
        removed.append("Comment")
    if "photoshop" in data:
        removed.append("Photoshop IRB data")

    # Re-save without any metadata
    clean = Image.new(img.mode, img.size)
    clean.paste(img)
    clean.save(dst, format="JPEG", quality=95, optimize=True,
                exif=b"", icc_profile=None)

    if not removed:
        removed.append("(no metadata found)")

    return CleanResult(src, True, removed)


def clean_png(src: Path, dst: Path) -> CleanResult:
    removed = []
    img = Image.open(src)

    meta_keys = list(img.info.keys())
    if meta_keys:
        removed.extend(meta_keys)
    else:
        removed.append("(no metadata found)")

    # Save with absolutely no ancillary chunks
    clean = Image.new(img.mode, img.size)
    clean.paste(img)
    clean.save(dst, format="PNG", optimize=True)

    return CleanResult(src, True, removed)


def clean_pdf(src: Path, dst: Path) -> CleanResult:
    removed = []
    with pikepdf.open(src) as pdf:
        # Document info dict
        if pdf.docinfo:
            keys = list(pdf.docinfo.keys())
            removed.append("Document info (" + ", ".join(str(k) for k in keys) + ")")
            for k in keys:
                del pdf.docinfo[k]

        # XMP metadata stream
        with pdf.open_metadata() as meta:
            if len(meta) > 0:
                removed.append("XMP metadata stream")
                meta.clear()

        # Remove embedded thumbnails from pages
        for i, page in enumerate(pdf.pages):
            if "/Thumb" in page:
                del page["/Thumb"]
                removed.append(f"Page {i+1} thumbnail")

        pdf.save(dst, compress_streams=True, object_stream_mode=pikepdf.ObjectStreamMode.generate)

    if not removed:
        removed.append("(no metadata found)")

    return CleanResult(src, True, removed)


def clean_docx(src: Path, dst: Path) -> CleanResult:
    """Strip core properties and custom XML parts from a DOCX."""
    import zipfile, re
    removed = []

    REMOVE_PARTS = {
        "docProps/core.xml",      # author, title, dates …
        "docProps/app.xml",       # application name, version …
        "docProps/custom.xml",    # custom properties
    }

    with zipfile.ZipFile(src, "r") as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        rels_data = {}
        names = zin.namelist()

        # First pass: collect [Content_Types].xml and .rels files
        for name in names:
            if name == "_rels/.rels":
                rels_data[name] = zin.read(name)

        # Second pass: copy everything except removed parts
        for name in names:
            data = zin.read(name)
            if name in REMOVE_PARTS:
                removed.append(name.split("/")[-1])
                continue
            # Scrub creator info from _rels/.rels
            if name == "_rels/.rels":
                # Remove relationships pointing to removed parts
                import xml.etree.ElementTree as ET
                root = ET.fromstring(data)
                ns = "http://schemas.openxmlformats.org/package/2006/relationships"
                to_remove = []
                for child in root:
                    target = child.get("Target", "")
                    if any(target.endswith(p.split("/")[-1]) for p in REMOVE_PARTS):
                        to_remove.append(child)
                for child in to_remove:
                    root.remove(child)
                data = ET.tostring(root, xml_declaration=True, encoding="UTF-8")
            zout.writestr(name, data)

    if not removed:
        removed.append("(no metadata found)")

    return CleanResult(src, True, removed)


def clean_audio(src: Path, dst: Path) -> CleanResult:

    removed = []
    shutil.copy2(src, dst)
    audio = mutagen.File(dst, easy=False)
    if audio is None:
        return CleanResult(src, False, [], "mutagen could not parse file")
    tags = audio.tags
    if tags:
        removed.append(f"Tags: {type(tags).__name__} ({len(tags)} fields)")
        audio.delete()
        audio.save()
    else:
        removed.append("(no tags found)")
    return CleanResult(src, True, removed)


def clean_gif(src: Path, dst: Path) -> CleanResult:
    removed = []
    img = Image.open(src)
    if img.info:
        removed.extend(img.info.keys())
    frames = []
    try:
        while True:
            frames.append(img.copy().convert("RGBA"))
            img.seek(img.tell() + 1)
    except EOFError:
        pass
    if frames:
        frames[0].save(dst, format="GIF", save_all=True,
                       append_images=frames[1:], loop=0)
    else:
        img.save(dst, format="GIF")
    if not removed:
        removed.append("(no metadata found)")
    return CleanResult(src, True, removed)


def clean_tiff(src: Path, dst: Path) -> CleanResult:
    removed = []
    img = Image.open(src)
    if "exif" in img.info:
        removed.append("EXIF data")
    if img.info:
        removed.extend(k for k in img.info if k != "exif")
    clean = Image.new(img.mode, img.size)
    clean.paste(img)
    clean.save(dst, format="TIFF")
    if not removed:
        removed.append("(no metadata found)")
    return CleanResult(src, True, removed)


def clean_generic_image(src: Path, dst: Path, fmt: str) -> CleanResult:

    removed = []
    img = Image.open(src)
    if img.info:
        removed.extend(img.info.keys())
    clean = Image.new(img.mode, img.size)
    clean.paste(img)
    clean.save(dst, format=fmt)
    if not removed:
        removed.append("(no metadata found)")
    return CleanResult(src, True, removed)


def clean_csv(src: Path, dst: Path) -> CleanResult:

    import csv

    removed = []
    raw = src.read_bytes()

    # Detect and strip BOM
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
        removed.append("UTF-8 BOM")
    elif raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        removed.append("UTF-16 BOM")
        raw = raw.replace(b"\x00", b"")
        raw = raw[2:]

    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()

    # Strip comment lines
    comment_lines = [l for l in lines if l.strip().startswith(("#", "//"))]
    if comment_lines:
        removed.append(f"Comment lines ({len(comment_lines)})")
    lines = [l for l in lines if not l.strip().startswith(("#", "//"))]

    # Re-parse, stripping cell whitespace
    cleaned_rows = []
    cells_stripped = 0
    reader = csv.reader(lines)
    for row in reader:
        new_row = []
        for cell in row:
            stripped = cell.strip()
            if stripped != cell:
                cells_stripped += 1
            new_row.append(stripped)
        cleaned_rows.append(new_row)

    if cells_stripped:
        removed.append(f"Whitespace in {cells_stripped} cell(s)")

    # Drop leading/trailing empty rows
    while cleaned_rows and all(c == "" for c in cleaned_rows[0]):
        cleaned_rows.pop(0)
        removed.append("Leading empty row")
    while cleaned_rows and all(c == "" for c in cleaned_rows[-1]):
        cleaned_rows.pop()
        removed.append("Trailing empty row")

    if not removed:
        removed.append("(no traceable elements found)")

    with dst.open("w", newline="\n", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerows(cleaned_rows)

    return CleanResult(src, True, removed)


DISPATCH = {
    ".jpg":  lambda s, d: clean_jpeg(s, d),
    ".jpeg": lambda s, d: clean_jpeg(s, d),
    ".png":  lambda s, d: clean_png(s, d),
    ".pdf":  lambda s, d: clean_pdf(s, d),
    ".docx": lambda s, d: clean_docx(s, d),
    ".mp3":  lambda s, d: clean_audio(s, d),
    ".ogg":  lambda s, d: clean_audio(s, d),
    ".flac": lambda s, d: clean_audio(s, d),
    ".m4a":  lambda s, d: clean_audio(s, d),
    ".gif":  lambda s, d: clean_gif(s, d),
    ".tif":  lambda s, d: clean_tiff(s, d),
    ".tiff": lambda s, d: clean_tiff(s, d),
    ".bmp":  lambda s, d: clean_generic_image(s, d, "BMP"),
    ".webp": lambda s, d: clean_generic_image(s, d, "WEBP"),
    ".csv":  lambda s, d: clean_csv(s, d),
}

SUPPORTED_EXTENSIONS = sorted(DISPATCH.keys())


def clean_file(src: Path, output_dir: Path | None = None, overwrite: bool = False) -> CleanResult:
    ext = src.suffix.lower()
    if ext not in DISPATCH:
        return CleanResult(src, False, [], f"Unsupported format: {ext}")

    if overwrite:
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            result = DISPATCH[ext](src, tmp_path)
            shutil.move(tmp_path, src)
        except Exception as e:
            tmp_path.unlink(missing_ok=True)
            return CleanResult(src, False, [], str(e))
    else:
        if output_dir is None:
            output_dir = src.parent / "cleaned"
        output_dir.mkdir(parents=True, exist_ok=True)
        dst = output_dir / src.name
        # Avoid collision
        counter = 1
        while dst.exists():
            dst = output_dir / f"{src.stem}_clean{counter}{src.suffix}"
            counter += 1
        try:
            result = DISPATCH[ext](src, dst)
        except Exception as e:
            dst.unlink(missing_ok=True)
            return CleanResult(src, False, [], str(e))

    return result


# ═══════════════════════════════════════════════════════════════════════════
# GUI
# ═══════════════════════════════════════════════════════════════════════════

DARK_BG   = "#0f1117"
PANEL_BG  = "#1a1d27"
CARD_BG   = "#21253a"
ACCENT    = "#4f8ef7"
ACCENT2   = "#7c3aed"
SUCCESS   = "#22c55e"
ERROR     = "#ef4444"
TEXT_PRI  = "#e8eaf6"
TEXT_SEC  = "#7b85a0"
BORDER    = "#2e3350"


class MetaWipeApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MetaWipe")
        self.geometry("860x640")
        self.minsize(700, 500)
        self.configure(bg=DARK_BG)
        self.resizable(True, True)

        self.files: list[Path] = []
        self.output_dir: Path | None = None
        self.overwrite_var = tk.BooleanVar(value=False)
        self._build_ui()

    # ── Layout ──────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Header
        hdr = tk.Frame(self, bg=PANEL_BG, height=64)
        hdr.pack(fill="x", side="top")
        hdr.pack_propagate(False)

        tk.Label(hdr, text="⬛ MetaWipe", font=("Courier New", 18, "bold"),
                 fg=ACCENT, bg=PANEL_BG).pack(side="left", padx=20, pady=14)
        tk.Label(hdr, text="Strip metadata & traceable elements",
                 font=("Courier New", 10), fg=TEXT_SEC, bg=PANEL_BG).pack(side="left", pady=18)

        badge_text = "  " + "  ·  ".join(e.lstrip(".").upper() for e in SUPPORTED_EXTENSIONS) + "  "
        tk.Label(hdr, text=badge_text, font=("Courier New", 8),
                 fg=TEXT_SEC, bg=DARK_BG, relief="flat", pady=2).pack(side="right", padx=16, pady=18)

        # Main body
        body = tk.Frame(self, bg=DARK_BG)
        body.pack(fill="both", expand=True, padx=16, pady=(12, 0))

        # Left column: drop zone + file list
        left = tk.Frame(body, bg=DARK_BG)
        left.pack(side="left", fill="both", expand=True)

        self._build_dropzone(left)
        self._build_filelist(left)

        # Right column: options + log
        right = tk.Frame(body, bg=DARK_BG, width=260)
        right.pack(side="right", fill="y", padx=(12, 0))
        right.pack_propagate(False)

        self._build_options(right)
        self._build_log(right)

        # Bottom bar
        self._build_bottom()

    def _build_dropzone(self, parent):
        frame = tk.Frame(parent, bg=CARD_BG, bd=0, relief="flat",
                         highlightthickness=1, highlightbackground=BORDER)
        frame.pack(fill="x", pady=(0, 8))

        inner = tk.Frame(frame, bg=CARD_BG, pady=18)
        inner.pack(fill="x")

        tk.Label(inner, text="＋ Add Files", font=("Courier New", 13, "bold"),
                 fg=ACCENT, bg=CARD_BG, cursor="hand2").pack()
        tk.Label(inner, text="click to browse or drag & drop",
                 font=("Courier New", 9), fg=TEXT_SEC, bg=CARD_BG).pack(pady=(2, 0))

        frame.bind("<Button-1>", lambda e: self._browse_files())
        inner.bind("<Button-1>", lambda e: self._browse_files())
        for w in inner.winfo_children():
            w.bind("<Button-1>", lambda e: self._browse_files())

        # Enable drag-and-drop if tkinterdnd2 is available
        try:
            self.drop_target_register("DND_Files")  # type: ignore
            self.dnd_bind("<<Drop>>", self._on_drop)  # type: ignore
        except Exception:
            pass

    def _build_filelist(self, parent):
        lbl_row = tk.Frame(parent, bg=DARK_BG)
        lbl_row.pack(fill="x", pady=(0, 4))
        tk.Label(lbl_row, text="FILES", font=("Courier New", 9, "bold"),
                 fg=TEXT_SEC, bg=DARK_BG).pack(side="left")
        tk.Label(lbl_row, text="— 0 selected", font=("Courier New", 9),
                 fg=TEXT_SEC, bg=DARK_BG).pack(side="left", padx=4)
        self._count_label = lbl_row.winfo_children()[-1]

        tk.Button(lbl_row, text="Clear", font=("Courier New", 8),
                  fg=TEXT_SEC, bg=PANEL_BG, relief="flat", cursor="hand2",
                  activebackground=CARD_BG, activeforeground=TEXT_PRI,
                  command=self._clear_files).pack(side="right")

        list_frame = tk.Frame(parent, bg=CARD_BG, bd=0, relief="flat",
                              highlightthickness=1, highlightbackground=BORDER)
        list_frame.pack(fill="both", expand=True)

        self.file_listbox = tk.Listbox(
            list_frame,
            bg=CARD_BG, fg=TEXT_PRI,
            selectbackground=ACCENT2, selectforeground="#fff",
            font=("Courier New", 9),
            relief="flat", bd=0,
            activestyle="none",
            highlightthickness=0,
        )
        scroll = tk.Scrollbar(list_frame, command=self.file_listbox.yview,
                              bg=PANEL_BG, troughcolor=CARD_BG, relief="flat", width=8)
        self.file_listbox.config(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.file_listbox.pack(fill="both", expand=True, padx=4, pady=4)

        # Right-click to remove
        menu = tk.Menu(self, tearoff=0, bg=PANEL_BG, fg=TEXT_PRI,
                       activebackground=ACCENT, activeforeground="#fff")
        menu.add_command(label="Remove selected", command=self._remove_selected)
        self.file_listbox.bind("<Button-3>", lambda e: menu.post(e.x_root, e.y_root))

    def _build_options(self, parent):
        card = tk.Frame(parent, bg=CARD_BG, bd=0, relief="flat",
                        highlightthickness=1, highlightbackground=BORDER)
        card.pack(fill="x", pady=(0, 10))

        tk.Label(card, text="OPTIONS", font=("Courier New", 9, "bold"),
                 fg=TEXT_SEC, bg=CARD_BG).pack(anchor="w", padx=12, pady=(10, 6))

        sep = tk.Frame(card, bg=BORDER, height=1)
        sep.pack(fill="x", padx=12)

        # Overwrite toggle
        ow_row = tk.Frame(card, bg=CARD_BG)
        ow_row.pack(fill="x", padx=12, pady=8)
        tk.Checkbutton(
            ow_row, text="Overwrite originals",
            variable=self.overwrite_var,
            font=("Courier New", 9), fg=TEXT_PRI, bg=CARD_BG,
            selectcolor=ACCENT2, activebackground=CARD_BG,
            activeforeground=TEXT_PRI, relief="flat",
            command=self._toggle_overwrite,
        ).pack(side="left")

        # Output folder
        out_row = tk.Frame(card, bg=CARD_BG)
        out_row.pack(fill="x", padx=12, pady=(0, 10))
        tk.Label(out_row, text="Output folder:", font=("Courier New", 8),
                 fg=TEXT_SEC, bg=CARD_BG).pack(side="left")
        self._outdir_btn = tk.Button(
            out_row, text="Set…", font=("Courier New", 8),
            fg=ACCENT, bg=PANEL_BG, relief="flat", cursor="hand2",
            activebackground=CARD_BG, activeforeground=ACCENT,
            command=self._pick_output_dir,
        )
        self._outdir_btn.pack(side="right")

        self._outdir_label = tk.Label(card, text="Default: ./cleaned/",
                                      font=("Courier New", 8), fg=TEXT_SEC,
                                      bg=CARD_BG, wraplength=220, justify="left")
        self._outdir_label.pack(anchor="w", padx=12, pady=(0, 10))

    def _build_log(self, parent):
        tk.Label(parent, text="LOG", font=("Courier New", 9, "bold"),
                 fg=TEXT_SEC, bg=DARK_BG).pack(anchor="w", pady=(0, 4))

        log_frame = tk.Frame(parent, bg=CARD_BG, bd=0, relief="flat",
                             highlightthickness=1, highlightbackground=BORDER)
        log_frame.pack(fill="both", expand=True)

        self.log_text = tk.Text(
            log_frame,
            bg=CARD_BG, fg=TEXT_PRI,
            font=("Courier New", 8),
            relief="flat", bd=0,
            state="disabled",
            wrap="word",
            highlightthickness=0,
        )
        log_scroll = tk.Scrollbar(log_frame, command=self.log_text.yview,
                                  bg=PANEL_BG, troughcolor=CARD_BG, relief="flat", width=8)
        self.log_text.config(yscrollcommand=log_scroll.set)
        log_scroll.pack(side="right", fill="y")
        self.log_text.pack(fill="both", expand=True, padx=4, pady=4)

        self.log_text.tag_config("ok",  foreground=SUCCESS)
        self.log_text.tag_config("err", foreground=ERROR)
        self.log_text.tag_config("info", foreground=ACCENT)
        self.log_text.tag_config("dim", foreground=TEXT_SEC)

    def _build_bottom(self):
        bar = tk.Frame(self, bg=PANEL_BG, height=60)
        bar.pack(fill="x", side="bottom")
        bar.pack_propagate(False)

        self.progress = ttk.Progressbar(bar, mode="determinate", length=300)
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TProgressbar",
                        troughcolor=CARD_BG,
                        background=ACCENT,
                        darkcolor=ACCENT,
                        lightcolor=ACCENT,
                        bordercolor=PANEL_BG,
                        thickness=4)
        self.progress.pack(side="left", padx=20, pady=22)

        self._status_label = tk.Label(bar, text="Ready", font=("Courier New", 9),
                                      fg=TEXT_SEC, bg=PANEL_BG)
        self._status_label.pack(side="left")

        self.clean_btn = tk.Button(
            bar, text="  ⬛ CLEAN FILES  ",
            font=("Courier New", 11, "bold"),
            fg="#fff", bg=ACCENT,
            activebackground=ACCENT2, activeforeground="#fff",
            relief="flat", cursor="hand2",
            command=self._start_cleaning,
            pady=8, padx=4,
        )
        self.clean_btn.pack(side="right", padx=20, pady=10)

    # ── File management ──────────────────────────────────────────────────────

    def _browse_files(self):
        types = [("Supported files",
                  " ".join(f"*{e}" for e in SUPPORTED_EXTENSIONS)),
                 ("All files", "*.*")]
        paths = filedialog.askopenfilenames(filetypes=types)
        self._add_files([Path(p) for p in paths])

    def _on_drop(self, event):
        raw = event.data
        paths = self.tk.splitlist(raw)
        self._add_files([Path(p) for p in paths])

    def _add_files(self, paths: list[Path]):
        for p in paths:
            if p not in self.files and p.suffix.lower() in DISPATCH:
                self.files.append(p)
                self.file_listbox.insert("end", f"  {p.name}")
        self._update_count()

    def _remove_selected(self):
        sel = list(self.file_listbox.curselection())
        for idx in reversed(sel):
            self.file_listbox.delete(idx)
            del self.files[idx]
        self._update_count()

    def _clear_files(self):
        self.files.clear()
        self.file_listbox.delete(0, "end")
        self._update_count()

    def _update_count(self):
        n = len(self.files)
        self._count_label.config(text=f"— {n} selected")

    def _toggle_overwrite(self):
        ow = self.overwrite_var.get()
        self._outdir_btn.config(state="disabled" if ow else "normal")
        if ow:
            self._outdir_label.config(text="Files will be modified in place!", fg=ERROR)
        else:
            self._update_outdir_label()

    def _pick_output_dir(self):
        d = filedialog.askdirectory()
        if d:
            self.output_dir = Path(d)
            self._update_outdir_label()

    def _update_outdir_label(self):
        if self.output_dir:
            self._outdir_label.config(text=str(self.output_dir), fg=TEXT_SEC)
        else:
            self._outdir_label.config(text="Default: ./cleaned/", fg=TEXT_SEC)

    # ── Cleaning ─────────────────────────────────────────────────────────────

    def _log(self, msg: str, tag: str = ""):
        self.log_text.config(state="normal")
        self.log_text.insert("end", msg + "\n", tag)
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _start_cleaning(self):
        if not self.files:
            messagebox.showwarning("No files", "Please add files to clean.")
            return

        if self.overwrite_var.get():
            if not messagebox.askyesno(
                "Overwrite originals?",
                "This will permanently modify your original files.\n\nContinue?"
            ):
                return

        self.clean_btn.config(state="disabled")
        self.progress["value"] = 0
        self.progress["maximum"] = len(self.files)
        threading.Thread(target=self._run_cleaning, daemon=True).start()

    def _run_cleaning(self):
        total = len(self.files)
        ok = 0
        fail = 0
        self._log(f"\n{'─'*36}", "dim")
        self._log(f"  Starting — {total} file(s)  {datetime.now():%H:%M:%S}", "info")
        self._log(f"{'─'*36}", "dim")

        for i, src in enumerate(self.files):
            self._set_status(f"Processing {src.name}…")
            try:
                result = clean_file(src, self.output_dir, self.overwrite_var.get())
                if result.success:
                    ok += 1
                    self._log(f"\n✓ {src.name}", "ok")
                    for r in result.removed:
                        self._log(f"    · {r}", "dim")
                else:
                    fail += 1
                    self._log(f"\n✗ {src.name} — {result.note}", "err")
            except Exception as e:
                fail += 1
                self._log(f"\n✗ {src.name}", "err")
                self._log(f"    {traceback.format_exc(limit=2)}", "err")

            self.progress["value"] = i + 1

        self._log(f"\n{'─'*36}", "dim")
        self._log(f"  Done  ✓{ok}  ✗{fail}", "info")
        self._log(f"{'─'*36}\n", "dim")
        self._set_status(f"Done — {ok} cleaned, {fail} failed")
        self.clean_btn.config(state="normal")

    def _set_status(self, msg: str):
        self._status_label.config(text=msg)
        self.update_idletasks()


# ═══════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    app = MetaWipeApp()
    app.mainloop()
