from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict


class NotifikasiResponse(BaseModel):
    id_notifikasi: int
    tipe: str  # setoran | penarikan_selesai | penarikan_ditolak | saldo | pengumuman | keamanan
    judul: str
    isi: str
    id_referensi: Optional[int] = None  # id transaksi / penarikan / pengumuman terkait
    is_read: bool
    tanggal: datetime

    model_config = ConfigDict(from_attributes=True)


class NotifikasiListResponse(BaseModel):
    total: int            # jumlah notifikasi sesuai filter (untuk paginasi)
    belum_dibaca: int     # total belum dibaca milik nasabah (tidak terpengaruh filter)
    data: List[NotifikasiResponse]


class NotifikasiRingkasanResponse(BaseModel):
    """Ringan, dipanggil berkala oleh aplikasi nasabah (polling)."""
    belum_dibaca: int
    chat_belum_dibaca: int          # balasan admin yang belum dibuka di Live Chat
    id_terbaru: Optional[int] = None  # id notifikasi paling baru (untuk deteksi notifikasi baru)


class AksiMassalResponse(BaseModel):
    diperbarui: int