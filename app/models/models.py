import enum
from datetime import datetime
from app.core.timezone import now_wib
from sqlalchemy import (
    Column, Integer, String, Text, Numeric, DateTime, 
    ForeignKey, Enum as SQLEnum, CHAR, Boolean,
    Index, text
)
from sqlalchemy.orm import relationship
from app.core.database import Base

# ===== ENUM DEFINITIONS =====

class RoleAdmin(str, enum.Enum):
    admin = "admin"
    super_admin = "super_admin"

class StatusPenarikan(str, enum.Enum):
    menunggu = "menunggu"
    disetujui = "disetujui"
    ditolak = "ditolak"
    selesai = "selesai"
    dibatalkan = "dibatalkan"

class StatusPengumuman(str, enum.Enum):
    draft = "draft"
    terbit = "terbit"
    arsip = "arsip"

# ===== TABEL-TABEL DATABASE =====

class Nasabah(Base):
    """Tabel Nasabah - LOGIN PAKAI OTP, TIDAK ADA PASSWORD"""
    __tablename__ = "nasabah"

    nik = Column(CHAR(16), primary_key=True, comment="NIK 16 digit sebagai PK")
    nama_nasabah = Column(String(100), nullable=False)
    no_hp = Column(String(15), unique=True, nullable=False, comment="Nomor HP untuk login/kontak")
    no_rekening = Column(String(30), nullable=False)
    nama_bank = Column(String(50), nullable=False)
    nama_pemilik_rekening = Column(String(100), nullable=False)
    alamat = Column(Text, nullable=True)
    foto_profil = Column(String(255), nullable=True)
    saldo = Column(
        Numeric(15, 2),
        nullable=False,
        default=0,
        server_default="0",
        comment="Saldo nasabah - HANYA berubah lewat endpoint transaksi (admin only)"
    )
    tanggal_daftar = Column(DateTime, default=now_wib, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False, comment="Status akun aktif/nonaktif")

    # Relasi
    transaksi = relationship("Transaksi", back_populates="nasabah", cascade="all, delete-orphan")
    penarikan = relationship("PenarikanSaldo", back_populates="nasabah", cascade="all, delete-orphan")


class Admin(Base):
    """Tabel Admin - LOGIN PAKAI USERNAME + PASSWORD (super_admin pakai email + password + OTP)"""
    __tablename__ = "admin"

    id_admin = Column(Integer, primary_key=True, autoincrement=True)
    nama_admin = Column(String(100), nullable=False)
    username = Column(String(50), unique=True, nullable=False)
    email = Column(String(100), unique=True, nullable=True, comment="Wajib untuk super_admin, dipakai OTP login")
    password = Column(String(255), nullable=False, comment="Password harus di-hash!")
    no_hp = Column(String(15), nullable=True)
    role = Column(SQLEnum(RoleAdmin), nullable=False, default=RoleAdmin.admin)
    tanggal_dibuat = Column(DateTime, default=now_wib, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False,
                   server_default=text("true"), comment="Akun admin aktif/nonaktif")

    # Relasi
    penarikan_diproses = relationship("PenarikanSaldo", back_populates="admin")
    pengumuman = relationship("Pengumuman", back_populates="admin", cascade="all, delete-orphan")

class RiwayatSaldoAdjustment(Base):
    """Tabel log audit untuk koreksi saldo manual oleh admin (di luar transaksi normal)"""
    __tablename__ = "riwayat_saldo_adjustment"

    id_adjustment = Column(Integer, primary_key=True, autoincrement=True)
    nik = Column(CHAR(16), ForeignKey("nasabah.nik"), nullable=False)
    id_admin = Column(Integer, ForeignKey("admin.id_admin"), nullable=False, comment="Admin yang melakukan koreksi")
    jumlah = Column(Numeric(15, 2), nullable=False, comment="Positif = tambah, negatif = kurangi")
    saldo_sebelum = Column(Numeric(15, 2), nullable=False)
    saldo_sesudah = Column(Numeric(15, 2), nullable=False)
    keterangan = Column(Text, nullable=False, comment="Alasan koreksi - wajib diisi")
    tanggal = Column(DateTime, default=now_wib, nullable=False)

    # Relasi
    nasabah = relationship("Nasabah", backref="riwayat_saldo_adjustment")
    admin = relationship("Admin", backref="riwayat_saldo_adjustment_dibuat")

class JenisSampah(Base):
    """Tabel Jenis Sampah"""
    __tablename__ = "jenis_sampah"
    
    id_jenis = Column(Integer, primary_key=True, autoincrement=True)
    nama_jenis = Column(String(100), nullable=False)
    kategori = Column(String(50), nullable=True)
    harga_per_kg = Column(Numeric(12, 2), nullable=False)
    kode_ml = Column(String(50), unique=True, nullable=True, comment="Label dari model ML, contoh: metal_can")
    
    # Relasi
    detail_transaksi = relationship("DetailTransaksi", back_populates="jenis_sampah")


class Transaksi(Base):
    """Tabel Transaksi - History transaksi sampah"""
    __tablename__ = "transaksi"
    
    id_transaksi = Column(Integer, primary_key=True, autoincrement=True)
    nik = Column(CHAR(16), ForeignKey("nasabah.nik"), nullable=False)
    tanggal_transaksi = Column(DateTime, default=now_wib, nullable=False)
    total_berat = Column(Numeric(10, 2), nullable=False)
    total_nilai = Column(Numeric(15, 2), nullable=False, comment="Saldo yang didapat")
    keterangan = Column(Text, nullable=True)
    dibuat_manual = Column(Boolean, default=False, nullable=False)
    dibuat_oleh = Column(String(50), nullable=True, comment="Username admin yang mencatat (kalau manual)")
    
    # Relasi
    nasabah = relationship("Nasabah", back_populates="transaksi")
    details = relationship("DetailTransaksi", back_populates="transaksi", cascade="all, delete-orphan")
    data_iot = relationship("DataIoT", back_populates="transaksi", cascade="all, delete-orphan")


class DetailTransaksi(Base):
    """Tabel Detail Transaksi - Breakdown per jenis sampah"""
    __tablename__ = "detail_transaksi"
    
    id_detail = Column(Integer, primary_key=True, autoincrement=True)
    id_transaksi = Column(Integer, ForeignKey("transaksi.id_transaksi"), nullable=False)
    id_jenis = Column(Integer, ForeignKey("jenis_sampah.id_jenis"), nullable=False)
    berat = Column(Numeric(10, 2), nullable=False)
    harga_per_kg = Column(Numeric(12, 2), nullable=False, comment="Harga saat transaksi")
    subtotal = Column(Numeric(15, 2), nullable=False, comment="Berat × harga_per_kg")

    ditambahkan_manual = Column(Boolean, default=False, nullable=False)
    foto_url = Column(String(255), nullable=True, comment="Foto item untuk transaksi manual")
    dikoreksi_oleh = Column(String(50), nullable=True)
    dikoreksi_at = Column(DateTime, nullable=True)
    
    # Relasi
    transaksi = relationship("Transaksi", back_populates="details")
    jenis_sampah = relationship("JenisSampah", back_populates="detail_transaksi")


class PenarikanSaldo(Base):
    """Tabel Penarikan Saldo - Nasabah tarik saldo"""
    __tablename__ = "penarikan_saldo"
    
    id_penarikan = Column(Integer, primary_key=True, autoincrement=True)
    nik = Column(CHAR(16), ForeignKey("nasabah.nik"), nullable=False)
    tanggal_pengajuan = Column(DateTime, default=now_wib, nullable=False)
    jumlah = Column(Numeric(15, 2), nullable=False, comment="Jumlah saldo yang ditarik")
    status = Column(
        SQLEnum(StatusPenarikan), 
        default=StatusPenarikan.menunggu, 
        nullable=False
    )
    keterangan = Column(Text, nullable=True, comment="Catatan dari admin")
    tanggal_diproses = Column(DateTime, nullable=True)
    id_admin = Column(Integer, ForeignKey("admin.id_admin"), nullable=True)

    nama_bank = Column(String(50), nullable=False)               # BARU (snapshot)
    no_rekening = Column(String(30), nullable=False)             # BARU (snapshot)
    nama_pemilik_rekening = Column(String(100), nullable=False)  # BARU (snapshot)
    bukti_transfer = Column(String(255), nullable=True)          # BARU (nama file)

    __table_args__ = (
        Index("uq_penarikan_menunggu_nik", "nik", unique=True,
              postgresql_where=text("status = 'menunggu'")),
    )

    @property
    def ada_bukti(self) -> bool:
        return self.bukti_transfer is not None
    
    # Relasi
    nasabah = relationship("Nasabah", back_populates="penarikan")
    admin = relationship("Admin", back_populates="penarikan_diproses")


class Pengumuman(Base):
    """Tabel Pengumuman - Announcement/Berita dari admin"""
    __tablename__ = "pengumuman"
    
    id_pengumuman = Column(Integer, primary_key=True, autoincrement=True)
    id_admin = Column(Integer, ForeignKey("admin.id_admin"), nullable=False)
    judul = Column(String(200), nullable=False)
    isi = Column(Text, nullable=False)
    gambar = Column(String(255), nullable=True, comment="Path ke gambar")
    tanggal_publikasi = Column(DateTime, default=now_wib, nullable=False)
    status = Column(
        SQLEnum(StatusPengumuman), 
        default=StatusPengumuman.draft, 
        nullable=False
    )
    
    # Relasi
    admin = relationship("Admin", back_populates="pengumuman")


class MitraPengepul(Base):
    """Tabel Mitra Pengepul - tempat Bank Sampah menjual stok sampah yang terkumpul"""
    __tablename__ = "mitra_pengepul"

    id_mitra = Column(Integer, primary_key=True, autoincrement=True)
    nama_mitra = Column(String(150), nullable=False)
    deskripsi = Column(Text, nullable=True)
    alamat = Column(Text, nullable=True)
    no_hp = Column(String(15), nullable=True)
    email = Column(String(100), nullable=True)
    nama_penanggung_jawab = Column(String(100), nullable=True, comment="Nama kontak/PIC di pihak mitra")
    is_active = Column(Boolean, default=True, nullable=False, comment="Status kerjasama aktif/tidak")
    tanggal_ditambahkan = Column(DateTime, default=now_wib, nullable=False)
    tanggal_diperbarui = Column(DateTime, default=now_wib, onupdate=now_wib, nullable=False)

    # Relasi
    harga_jenis_sampah = relationship("HargaMitraPengepul", back_populates="mitra", cascade="all, delete-orphan")


class HargaMitraPengepul(Base):
    """Tabel harga beli per jenis sampah, spesifik per mitra pengepul (many-to-many dengan harga)"""
    __tablename__ = "harga_mitra_pengepul"

    id_harga = Column(Integer, primary_key=True, autoincrement=True)
    id_mitra = Column(Integer, ForeignKey("mitra_pengepul.id_mitra"), nullable=False)
    id_jenis = Column(Integer, ForeignKey("jenis_sampah.id_jenis"), nullable=False)
    harga_beli_per_kg = Column(Numeric(12, 2), nullable=False, comment="Harga yang mitra ini bayar per kg untuk jenis sampah ini")
    tanggal_diperbarui = Column(DateTime, default=now_wib, onupdate=now_wib, nullable=False)

    # Relasi
    mitra = relationship("MitraPengepul", back_populates="harga_jenis_sampah")
    jenis_sampah = relationship("JenisSampah")

    @property
    def nama_jenis(self):
        """Dibaca schema HargaMitraResponse (from_attributes) supaya nama_jenis tidak null."""
        return self.jenis_sampah.nama_jenis if self.jenis_sampah else None


class DataIoT(Base):
    """Tabel Data IoT - Integrasi sensor ESP32"""
    __tablename__ = "data_iot"
    
    id_data_iot = Column(Integer, primary_key=True, autoincrement=True)
    id_transaksi = Column(Integer, ForeignKey("transaksi.id_transaksi"), nullable=True)
    id_detail = Column(Integer, ForeignKey("detail_transaksi.id_detail"), nullable=True)
    jenis_terdeteksi = Column(String(100), nullable=True, comment="Hasil deteksi ML")
    berat_sensor = Column(Numeric(10, 2), nullable=True, comment="Berat dari sensor")
    confidence = Column(Numeric(5, 2), nullable=True, comment="Confidence level deteksi")
    foto_url = Column(String(255), nullable=True, comment="URL foto hasil capture kamera IoT")
    waktu_scan = Column(DateTime, default=now_wib, nullable=False)
    
    # Relasi
    transaksi = relationship("Transaksi", back_populates="data_iot")


class OTP(Base):
    """Tabel OTP - One Time Password untuk verifikasi via HP atau Email"""
    __tablename__ = "otp"

    id_otp = Column(Integer, primary_key=True, autoincrement=True)
    identifier = Column(String(100), nullable=False, unique=True, comment="No HP atau email peminta OTP")
    kode_otp = Column(String(6), nullable=False)
    is_used = Column(Boolean, default=False, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=now_wib, nullable=False)

class TransaksiJualMitra(Base):
    """Tabel transaksi penjualan sampah dari Bank Sampah ke mitra pengepul"""
    __tablename__ = "transaksi_jual_mitra"

    id_transaksi_jual = Column(Integer, primary_key=True, autoincrement=True)
    id_mitra = Column(Integer, ForeignKey("mitra_pengepul.id_mitra"), nullable=False)
    id_admin = Column(Integer, ForeignKey("admin.id_admin"), nullable=False, comment="Admin yang mencatat transaksi ini")
    tanggal_transaksi = Column(DateTime, default=now_wib, nullable=False)
    total_berat = Column(Numeric(10, 2), nullable=False)
    total_nilai = Column(Numeric(15, 2), nullable=False, comment="Total uang yang diterima dari mitra")
    keterangan = Column(Text, nullable=True)

    # Relasi
    mitra = relationship("MitraPengepul")
    admin = relationship("Admin")
    details = relationship("DetailTransaksiJualMitra", back_populates="transaksi_jual", cascade="all, delete-orphan")


class DetailTransaksiJualMitra(Base):
    """Detail breakdown per jenis sampah dalam 1 transaksi jual ke mitra"""
    __tablename__ = "detail_transaksi_jual_mitra"

    id_detail_jual = Column(Integer, primary_key=True, autoincrement=True)
    id_transaksi_jual = Column(Integer, ForeignKey("transaksi_jual_mitra.id_transaksi_jual"), nullable=False)
    id_jenis = Column(Integer, ForeignKey("jenis_sampah.id_jenis"), nullable=False)
    berat = Column(Numeric(10, 2), nullable=False)
    harga_per_kg = Column(Numeric(12, 2), nullable=False, comment="Harga saat transaksi, snapshot dari HargaMitraPengepul")
    subtotal = Column(Numeric(15, 2), nullable=False)

    # Relasi
    transaksi_jual = relationship("TransaksiJualMitra", back_populates="details")
    jenis_sampah = relationship("JenisSampah")

class FAQ(Base):
    __tablename__ = "faq"

    id = Column(Integer, primary_key=True, index=True)
    pertanyaan = Column(String, nullable=False)
    jawaban = Column(Text, nullable=False)
    kategori = Column(String, nullable=True)  # "Saldo", "Sampah", "Akun", dll
    urutan = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=now_wib)
    updated_at = Column(DateTime, default=now_wib, onupdate=now_wib)

class PesanChat(Base):
    __tablename__ = "pesan_chat"

    id = Column(Integer, primary_key=True, index=True)
    nik_nasabah = Column(String, ForeignKey("nasabah.nik"), nullable=False, index=True)
    sender_type = Column(String, nullable=False)   # "nasabah" atau "admin"
    sender_id = Column(String, nullable=True)      # username admin kalau sender_type = "admin", null kalau nasabah
    isi_pesan = Column(Text, nullable=False)
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=now_wib)

class ChatAssignment(Base):
    __tablename__ = "chat_assignment"

    nik_nasabah = Column(String, ForeignKey("nasabah.nik"), primary_key=True)
    admin_username = Column(String, nullable=False)
    claimed_at = Column(DateTime, default=now_wib)

class StatusScan(str, enum.Enum):
    menunggu = "menunggu"          # baru satu sisi (IoT atau ML) yang masuk
    selesai = "selesai"            # kedua sisi lengkap -> sudah jadi Transaksi
    kadaluarsa = "kadaluarsa"      # sisi satunya tidak datang dalam batas waktu
    butuh_review = "butuh_review"  # lengkap, tapi NIK kosong / label ML tidak dikenal

class ScanPending(Base):
    """Buffer: satu baris diisi dari dua sumber (IoT = berat, ML = jenis). Jadi Transaksi saat lengkap."""
    __tablename__ = "scan_pending"
 
    id = Column(Integer, primary_key=True, autoincrement=True)
    machine_id = Column(String(50), nullable=False, index=True)
    scan_id = Column(String(64), nullable=True, index=True, comment="Opsional, kalau IoT & ML sepakat satu ID")
    nik = Column(CHAR(16), nullable=True)
 
    # sisi IoT
    berat = Column(Numeric(10, 2), nullable=True)
    # sisi ML
    jenis_terdeteksi = Column(String(100), nullable=True)
    confidence = Column(Numeric(5, 2), nullable=True)
    foto_url = Column(String(255), nullable=True)
 
    status = Column(SQLEnum(StatusScan), default=StatusScan.menunggu, nullable=False, index=True)
    id_transaksi = Column(Integer, ForeignKey("transaksi.id_transaksi"), nullable=True)
    created_at = Column(DateTime, default=now_wib, nullable=False)
    updated_at = Column(DateTime, default=now_wib, onupdate=now_wib, nullable=False)
    sesi_id = Column(Integer, ForeignKey("sesi_setoran.id"), nullable=True, index=True)

class StatusSesi(str, enum.Enum):
    aktif = "aktif"
    selesai = "selesai"   # ditutup nasabah -> item lengkap jadi Transaksi
    batal = "batal"       # basi (tidak ada aktivitas) -> item lengkap jadi butuh_review
 
 
class SesiSetoran(Base):
    """Satu sesi = satu kali nasabah menyetor di satu mesin. Banyak item (ScanPending) -> satu Transaksi."""
    __tablename__ = "sesi_setoran"
 
    id = Column(Integer, primary_key=True, autoincrement=True)
    machine_id = Column(String(50), nullable=False, index=True)
    nik = Column(CHAR(16), ForeignKey("nasabah.nik"), nullable=False, index=True)
    status = Column(SQLEnum(StatusSesi), default=StatusSesi.aktif, nullable=False)
    started_at = Column(DateTime, default=now_wib, nullable=False)
    terakhir_aktif = Column(DateTime, default=now_wib, nullable=False)
    selesai_at = Column(DateTime, nullable=True)
    id_transaksi = Column(Integer, ForeignKey("transaksi.id_transaksi"), nullable=True)
 
    __table_args__ = (
        # Pengaman di level DB: maksimal 1 sesi aktif per mesin & per nasabah
        Index("uq_sesi_aktif_mesin", "machine_id", unique=True, postgresql_where=text("status = 'aktif'")),
        Index("uq_sesi_aktif_nik", "nik", unique=True, postgresql_where=text("status = 'aktif'")),
    )

class Notifikasi(Base):
    """Notifikasi untuk nasabah. Satu baris = satu notifikasi untuk satu nasabah."""
    __tablename__ = "notifikasi"

    id_notifikasi = Column(Integer, primary_key=True, autoincrement=True)
    # Sengaja TANPA relationship(): cukup FK + ondelete CASCADE, supaya menghapus nasabah
    # tidak bentrok dengan ORM dan notifikasinya ikut terhapus oleh database.
    nik = Column(CHAR(16), ForeignKey("nasabah.nik", ondelete="CASCADE"), nullable=False)
    tipe = Column(String(30), nullable=False,
                  comment="setoran | penarikan_selesai | penarikan_ditolak | saldo | pengumuman | keamanan")
    judul = Column(String(150), nullable=False)
    isi = Column(Text, nullable=False)
    id_referensi = Column(Integer, nullable=True, comment="id transaksi / penarikan / pengumuman terkait")
    is_read = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    tanggal = Column(DateTime, nullable=False, default=now_wib)

    __table_args__ = (
        Index("ix_notifikasi_nik_read_tanggal", "nik", "is_read", "tanggal"),
    )