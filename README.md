# ⬛ MetaWipe — Metadata & Traceable Element Cleaner

A desktop GUI tool that strips embedded metadata and traceable elements from your files — cleanly, locally, and without uploading anything anywhere.

---

## 🧹 What It Does

Many file formats silently store sensitive information inside them — camera GPS coordinates, author names, software versions, edit history, and more. MetaWipe removes all of it.

| Format | What Gets Removed |
|--------|------------------|
| **JPG / JPEG** | EXIF data, ICC profile, XMP metadata, Comments, Photoshop IRB data |
| **PNG** | All ancillary metadata chunks (tEXt, zTXt, iTXt, etc.) |
| **PDF** | Document info dictionary, XMP metadata stream, embedded page thumbnails |
| **DOCX** | Core properties (author, dates), app properties, custom XML parts |
| **MP3 / OGG / FLAC / M4A** | ID3, APEv2, Vorbis, and all other audio tag formats via mutagen |
| **GIF** | All embedded metadata chunks |
| **TIFF** | EXIF data and all auxiliary metadata |
| **BMP / WEBP** | All embedded metadata |
| **CSV** | BOM markers, comment lines, cell whitespace, empty rows, normalises line endings |

---

## ✨ Features

- **Dark-mode GUI** — clean, minimal interface built with Tkinter
- **Multi-threaded processing** — UI stays responsive while files are being cleaned
- **Drag & drop support** — drop files directly onto the window (requires `tkinterdnd2`)
- **Batch processing** — add as many files as you need and clean them all at once
- **Two output modes:**
  - Save cleaned copies to a `./cleaned/` folder (default, non-destructive)
  - Overwrite originals in place (with confirmation prompt)
- **Live log** — see exactly what was removed from each file in real time
- **Progress bar** — track batch progress at a glance
- **Right-click to remove** individual files from the queue

---

## 🚀 Getting Started

### 1. Clone the repository
```bash
git clone https://github.com/yourusername/metawipe.git
cd metawipe
```

### 2. Install dependencies
```bash
pip install Pillow piexif pikepdf mutagen
```

> Optional — for drag & drop support:
> ```bash
> pip install tkinterdnd2
> ```

### 3. Run
```bash
python metadata_cleaner.py
```

---

## 📋 Requirements

- Python 3.10+
- Tkinter (included with standard Python on Windows/macOS; on Linux: `sudo apt install python3-tk`)
- Pillow
- piexif
- pikepdf
- mutagen

---

## 🖥️ Usage

1. Launch the app
2. Click **"+ Add Files"** or drag & drop files onto the window
3. *(Optional)* Set a custom output folder or toggle **"Overwrite originals"**
4. Click **"CLEAN FILES"**
5. Check the log panel to see exactly what was stripped from each file
6. Find your cleaned files in the `./cleaned/` subfolder (or in place if overwrite was selected)

---

## ⚠️ Notes

- Cleaning is **lossless for image quality** — images are re-saved at high quality (JPEG at 95%)
- The original file is never touched unless you explicitly enable "Overwrite originals"
- Unsupported file formats are skipped gracefully with a log message

---

## 📄 License

MIT License — free to use, modify, and distribute.
