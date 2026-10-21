import re
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from app.core.timezone import now_wib
from app.models.models import Nasabah, Notifikasi

TIPE_SETORAN = "setoran"
TIPE_PENARIKAN_SELESAI = "penarikan_selesai"
TIPE_PENARIKAN_DITOLAK = "penarikan_ditolak"
TIPE_SALDO = "saldo"
TIPE_PENGUMUMAN = "pengumuman"
TIPE_KEAMANAN = "keamanan"


# ---------- format teks ----------
def _rp(n) -> str:
    return "Rp" + f"{Decimal(n):,.0f}".replace(",", ".")


def _kg(n) -> str:
    teks = f"{Decimal(n):.2f}".rstrip("0").rstrip(".")
    return teks.replace(".", ",") + " kg"


def _samarkan(no_rekening: str) -> str:
    """Tampilkan 4 digit terakhir saja: ****1234"""
    return "****" + (no_rekening or "")[-4:]


def _ringkas(teks: str, maks: int = 140) -> str:
    bersih = re.sub(r"[#*_`>~\[\]]", "", teks or "")
    bersih = re.sub(r"\s+", " ", bersih).strip()
    return bersih if len(bersih) <= maks else bersih[: maks - 1].rstrip() + "…"


def adalah_terbit(status) -> bool:
    """Status pengumuman bisa berupa enum model, enum schema, atau string."""
    return str(getattr(status, "value", status)) == "terbit"


# ---------- inti ----------
def kirim_notifikasi(
    db: Session,
    nik: str,
    tipe: str,
    judul: str,
    isi: str,
    id_referensi: Optional[int] = None,
) -> Notifikasi:
    n = Notifikasi(nik=nik, tipe=tipe, judul=judul[:150], isi=isi, id_referensi=id_referensi)
    db.add(n)
    return n


# ---------- pemicu per kejadian ----------
def notif_setoran(db: Session, nik: str, id_transaksi: int, total_berat, total_nilai) -> None:
    kirim_notifikasi(
        db, nik, TIPE_SETORAN,
        "Setoran sampah berhasil",
        f"{_kg(total_berat)} sampahmu sudah tercatat. Saldo bertambah {_rp(total_nilai)}.",
        id_transaksi,
    )


def notif_penarikan_selesai(db: Session, penarikan) -> None:
    kirim_notifikasi(
        db, penarikan.nik, TIPE_PENARIKAN_SELESAI,
        "Penarikan saldo berhasil",
        f"{_rp(penarikan.jumlah)} sudah ditransfer ke {penarikan.nama_bank} "
        f"{_samarkan(penarikan.no_rekening)}. Bukti transfer bisa dilihat di halaman Penarikan.",
        penarikan.id_penarikan,
    )


def notif_penarikan_ditolak(db: Session, penarikan) -> None:
    kirim_notifikasi(
        db, penarikan.nik, TIPE_PENARIKAN_DITOLAK,
        "Penarikan saldo ditolak",
        f"Pengajuan {_rp(penarikan.jumlah)} ditolak. Alasan: {penarikan.keterangan}. "
        f"Saldo sudah dikembalikan.",
        penarikan.id_penarikan,
    )


def notif_saldo_manual(db: Session, nik: str, jumlah, saldo_sesudah, keterangan: str) -> None:
    jumlah = Decimal(jumlah)
    arah = "bertambah" if jumlah >= 0 else "berkurang"
    kirim_notifikasi(
        db, nik, TIPE_SALDO,
        "Saldo disesuaikan admin",
        f"Saldo kamu {arah} {_rp(abs(jumlah))}. Alasan: {keterangan}. "
        f"Saldo sekarang {_rp(saldo_sesudah)}.",
    )


def notif_rekening_diubah(db: Session, nasabah: Nasabah) -> None:
    kirim_notifikasi(
        db, nasabah.nik, TIPE_KEAMANAN,
        "Rekening pencairan diubah",
        f"Rekening tujuan penarikan sekarang {nasabah.nama_bank} {_samarkan(nasabah.no_rekening)}. "
        f"Bukan kamu yang mengubahnya? Segera hubungi admin lewat Live Chat.",
    )


def notif_pengumuman_terbit(db: Session, pengumuman) -> int:
    """
    Kirim ke semua nasabah aktif (satu baris per nasabah, supaya status 'sudah dibaca'
    bisa dicatat per orang). Nasabah yang daftar SETELAH ini tidak menerima pengumuman lama.
    Butuh pengumuman.id_pengumuman sudah terisi (db.flush() dulu kalau baru dibuat).
    """
    daftar_nik = [nik for (nik,) in db.query(Nasabah.nik).filter(Nasabah.is_active.is_(True)).all()]
    if not daftar_nik:
        return 0
    sekarang = now_wib()
    ringkas = _ringkas(pengumuman.isi)
    db.bulk_insert_mappings(Notifikasi, [
        {
            "nik": nik,
            "tipe": TIPE_PENGUMUMAN,
            "judul": pengumuman.judul[:150],
            "isi": ringkas,
            "id_referensi": pengumuman.id_pengumuman,
            "is_read": False,
            "tanggal": sekarang,
        }
        for nik in daftar_nik
    ])
    return len(daftar_nik)