"""
Ganti semua sumber waktu backend (UTC / jam server) menjadi WIB.

Jalankan dari ROOT project backend (folder yang berisi app/ dan alembic.ini):

    python apply_wib.py            # dry-run: hanya menampilkan apa yang akan diubah
    python apply_wib.py --apply    # benar-benar menulis perubahan

Aman dijalankan berulang (idempotent). Commit dulu ke git sebelum --apply.
"""
import re
import sys
from pathlib import Path

APPLY = "--apply" in sys.argv
ROOT = Path(".")
APP = ROOT / "app"
IMPORT_LINE = "from app.core.timezone import now_wib"

# urutan penting: pola yang lebih spesifik dulu
RULES = [
    (re.compile(r"datetime\.now\(timezone\.utc\)\.replace\(tzinfo=None\)"), "now_wib()"),
    (re.compile(r"datetime\.utcnow\(\)"), "now_wib()"),
    (re.compile(r"datetime\.utcnow\b(?!\()"), "now_wib"),   # default=..., onupdate=...
    (re.compile(r"datetime\.now\(\)"), "now_wib()"),
]


def add_import(text: str) -> str:
    if IMPORT_LINE in text:
        return text
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("from datetime import"):
            lines.insert(i + 1, IMPORT_LINE)
            return "\n".join(lines)
    # cadangan: setelah import top-level terakhir di 80 baris pertama
    last = -1
    for i, line in enumerate(lines[:80]):
        if line.startswith(("import ", "from ")):
            last = i
    lines.insert(last + 1, IMPORT_LINE)
    return "\n".join(lines)


def main() -> None:
    if not APP.is_dir():
        sys.exit("Folder app/ tidak ditemukan. Jalankan dari root project backend.")
    if not (APP / "core" / "timezone.py").exists():
        sys.exit("Salin dulu app/core/timezone.py ke project, baru jalankan skrip ini.")

    changed = 0
    for path in sorted(APP.rglob("*.py")):
        if path.name == "timezone.py" or "__pycache__" in path.parts:
            continue
        original = path.read_text(encoding="utf-8")
        text, total = original, 0
        for pattern, repl in RULES:
            text, n = pattern.subn(repl, text)
            total += n
        if total == 0:
            continue
        text = add_import(text)
        changed += 1
        print(f"{'UBAH ' if APPLY else 'AKAN UBAH'} {path}  ({total} penggantian)")
        if APPLY:
            path.write_text(text, encoding="utf-8")

    print(f"\n{changed} file {'diubah' if APPLY else 'akan diubah'}.")
    if not APPLY:
        print("Ini dry-run. Jalankan lagi dengan --apply untuk menulis perubahan.")
    print("Cek sisa manual (harus kosong untuk folder app/):")
    print('  grep -rnE "utcnow|datetime\\.now" app/ --include=*.py')


main()
