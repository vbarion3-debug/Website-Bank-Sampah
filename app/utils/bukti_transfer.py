"""
Helper penyimpanan bukti transfer penarikan saldo.

File disimpan di folder PRIVAT (bukan /static), jadi tidak bisa dibuka lewat URL langsung.
Satu-satunya jalan baca adalah endpoint yang mengecek session (admin, atau nasabah pemilik).
"""
import os
import uuid
from typing import Optional

from fastapi import HTTPException, UploadFile

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Di production (mis. Railway) arahkan ke folder volume persisten lewat env BUKTI_TRANSFER_DIR,
# kalau tidak file akan hilang setiap redeploy.
BUKTI_DIR = os.getenv("BUKTI_TRANSFER_DIR") or os.path.join(_ROOT, "private_uploads", "bukti_transfer")
os.makedirs(BUKTI_DIR, exist_ok=True)

MAX_UKURAN_BYTES = 5 * 1024 * 1024  # 5 MB

_MEDIA_TYPE = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def _deteksi_ekstensi(head: bytes) -> Optional[str]:
    """Tentukan tipe dari isi file (magic bytes), bukan dari nama/content-type kiriman client."""
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    return None


def simpan_bukti_transfer(upload: UploadFile) -> str:
    """Validasi lalu simpan. Return NAMA FILE saja (bukan path/URL) untuk disimpan di DB."""
    data = upload.file.read(MAX_UKURAN_BYTES + 1)
    if not data:
        raise HTTPException(status_code=400, detail="File bukti transfer kosong")
    if len(data) > MAX_UKURAN_BYTES:
        raise HTTPException(status_code=413, detail="Ukuran file maksimal 5 MB")

    ekstensi = _deteksi_ekstensi(data[:12])
    if ekstensi is None:
        raise HTTPException(status_code=415, detail="Format file harus gambar JPG, PNG, atau WEBP")

    nama_file = f"{uuid.uuid4().hex}{ekstensi}"
    with open(os.path.join(BUKTI_DIR, nama_file), "wb") as f:
        f.write(data)
    return nama_file


def path_bukti_transfer(nama_file: str) -> str:
    # basename: jaga-jaga supaya tidak bisa keluar dari folder
    return os.path.join(BUKTI_DIR, os.path.basename(nama_file))


def media_type_bukti(nama_file: str) -> str:
    return _MEDIA_TYPE.get(os.path.splitext(nama_file)[1].lower(), "application/octet-stream")


def hapus_bukti_transfer(nama_file: Optional[str]) -> None:
    """Dipakai untuk bersih-bersih kalau commit DB gagal setelah file terlanjur tersimpan."""
    if not nama_file:
        return
    try:
        os.remove(path_bukti_transfer(nama_file))
    except OSError:
        pass
