"""
IoT & ML — sisi mesin.

PERUBAHAN dari versi sebelumnya:
- Sesi sekarang dimulai & ditutup ADMIN lewat app/api/admin_iot.py (endpoint nasabah /sesi/mulai
  dan /sesi/selesai dihapus supaya tidak ada dua cara memulai sesi).
- Tambah POST /iot-ml/heartbeat (alat IoT lapor "saya hidup") -> dasar status "Tersambung".
- ScanPending.id_jenis_override: admin bisa memilih jenis sampah manual (menimpa / melengkapi label ML).
- Logika menutup sesi dipindah ke fungsi bersama tutup_sesi() supaya dipakai admin_iot.py.
"""
import os
from datetime import timedelta
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, or_, text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.timezone import now_wib
from app.models.models import (
    JenisSampah, PerangkatIoT, ScanPending, SesiSetoran, StatusScan, StatusSesi,
)
from app.schemas.admin_iot import HeartbeatRequest
from app.schemas.iot_ml import (
    IoTBeratCreate, MLDeteksiCreate, ScanResponse, SesiResponse,
)
from app.utils.security import verify_iot_api_key, verify_ml_api_key
from app.api.transaksi import buat_transaksi_iot, _save_base64_image

router = APIRouter(prefix="/iot-ml", tags=["IoT & ML (terpisah)"])

WINDOW_DETIK = 15        # batas menjodohkan berat (IoT) dengan label (ML) dari mesin yang sama
SESI_IDLE_MENIT = 30     # sesi tanpa aktivitas selama ini dianggap basi -> dibatalkan (dulu 10, admin yang menutup sekarang)
ONLINE_DETIK = 30        # alat dianggap "Tersambung" kalau heartbeat terakhir <= ini (alat kirim tiap ~10 detik)
CONFIDENCE_RENDAH = 80   # sama dengan CONFIDENCE_THRESHOLD di transaksi.py


def _test_mode() -> bool:
    """IOT_ML_TEST_MODE=true di .env -> /berat & /deteksi cuma print ke terminal (tanpa sesi, tanpa DB)."""
    return os.getenv("IOT_ML_TEST_MODE", "false").strip().lower() in ("1", "true", "yes")


def _print_test(sumber: str, payload: dict):
    print(f"\n{'=' * 60}")
    print(f"[TEST {sumber}] {now_wib():%Y-%m-%d %H:%M:%S}")
    for k, v in payload.items():
        print(f"  {k}: {v}")
    print(f"{'=' * 60}\n", flush=True)


# ====================================================================
# HELPER (dipakai juga oleh admin_iot.py)
# ====================================================================
def is_online(perangkat: PerangkatIoT) -> bool:
    return bool(
        perangkat.last_seen
        and perangkat.last_seen >= now_wib() - timedelta(seconds=ONLINE_DETIK)
    )


def kunci_mesin(db: Session, machine_id: str):
    """Kunci advisory per mesin selama transaksi DB berjalan (lepas otomatis saat commit/rollback)."""
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:m))"), {"m": machine_id})


def lengkap(item: ScanPending) -> bool:
    """Lengkap = ada berat DAN ada jenis (label ML atau pilihan manual admin)."""
    return item.berat is not None and (
        item.jenis_terdeteksi is not None or item.id_jenis_override is not None
    )


def batalkan_sesi(db: Session, sesi: SesiSetoran):
    """Sesi basi/dibatalkan: item lengkap -> butuh_review (data tidak hilang), item setengah -> kadaluarsa."""
    sesi.status = StatusSesi.batal
    sesi.selesai_at = now_wib()
    items = db.query(ScanPending).filter(
        ScanPending.sesi_id == sesi.id, ScanPending.status == StatusScan.menunggu
    ).all()
    for it in items:
        it.status = StatusScan.butuh_review if lengkap(it) else StatusScan.kadaluarsa


def sesi_aktif(db: Session, machine_id: str) -> Optional[SesiSetoran]:
    """Sesi aktif di mesin ini. Kalau sudah basi, dibatalkan dan dianggap tidak ada. Panggil SETELAH kunci_mesin."""
    sesi = db.query(SesiSetoran).filter(
        SesiSetoran.machine_id == machine_id, SesiSetoran.status == StatusSesi.aktif
    ).first()
    if sesi and sesi.terakhir_aktif < now_wib() - timedelta(minutes=SESI_IDLE_MENIT):
        batalkan_sesi(db, sesi)
        return None
    return sesi


def _expire_setengah(db: Session, machine_id: str):
    """Item yang baru satu sisi dan lewat jendela waktu -> kadaluarsa. Item lengkap TIDAK ikut."""
    batas = now_wib() - timedelta(seconds=WINDOW_DETIK)
    db.query(ScanPending).filter(
        ScanPending.machine_id == machine_id,
        ScanPending.status == StatusScan.menunggu,
        ScanPending.created_at < batas,
        or_(
            ScanPending.berat.is_(None),
            and_(ScanPending.jenis_terdeteksi.is_(None), ScanPending.id_jenis_override.is_(None)),
        ),
    ).update({"status": StatusScan.kadaluarsa}, synchronize_session=False)


def _wajib_sesi(db: Session, machine_id: str) -> SesiSetoran:
    """Kunci mesin, bersihkan item basi, lalu pastikan ada sesi aktif. Kalau tidak ada -> 409."""
    kunci_mesin(db, machine_id)
    _expire_setengah(db, machine_id)
    sesi = sesi_aktif(db, machine_id)
    if not sesi:
        db.commit()  # simpan efek samping (sesi basi dibatalkan / item kadaluarsa) sebelum menolak
        raise HTTPException(
            status_code=409,
            detail="Tidak ada sesi setoran aktif di mesin ini. Admin harus memilih alat & nasabah dulu di halaman Transaksi Nasabah.",
        )
    return sesi


def _cari_scan(db: Session, sesi_id: int, scan_id: Optional[str], kolom_kosong):
    q = db.query(ScanPending).filter(
        ScanPending.sesi_id == sesi_id,
        ScanPending.status == StatusScan.menunggu,
        kolom_kosong.is_(None),
    )
    if scan_id:
        q = q.filter(ScanPending.scan_id == scan_id)
    return q.order_by(ScanPending.created_at.asc()).first()


def hitung_lengkap(db: Session, sesi_id: int) -> int:
    items = db.query(ScanPending).filter(
        ScanPending.sesi_id == sesi_id, ScanPending.status == StatusScan.menunggu
    ).all()
    return sum(1 for i in items if lengkap(i))


def peta_jenis_item(db: Session, items: list) -> Dict[int, Optional[JenisSampah]]:
    """
    Tentukan jenis sampah FINAL tiap item: pilihan manual admin menang, kalau tidak ada
    dipetakan dari label ML (kode_ml). Return {scan.id: JenisSampah | None}.
    """
    kode = {i.jenis_terdeteksi for i in items if i.id_jenis_override is None and i.jenis_terdeteksi}
    ids = {i.id_jenis_override for i in items if i.id_jenis_override is not None}
    peta_kode = (
        {j.kode_ml: j for j in db.query(JenisSampah).filter(JenisSampah.kode_ml.in_(kode)).all()}
        if kode else {}
    )
    peta_id = (
        {j.id_jenis: j for j in db.query(JenisSampah).filter(JenisSampah.id_jenis.in_(ids)).all()}
        if ids else {}
    )
    hasil = {}
    for i in items:
        if i.id_jenis_override is not None:
            hasil[i.id] = peta_id.get(i.id_jenis_override)
        else:
            hasil[i.id] = peta_kode.get(i.jenis_terdeteksi) if i.jenis_terdeteksi else None
    return hasil


def tutup_sesi(db: Session, sesi: SesiSetoran, admin_username: Optional[str] = None) -> SesiResponse:
    """
    Tutup sesi: item lengkap & jenisnya dikenal -> 1 Transaksi (algoritma sama seperti sebelumnya),
    item lengkap tapi jenis tak dikenal -> butuh_review, item setengah -> kadaluarsa.
    Fungsi ini yang commit (atomic: transaksi + status item + status sesi).
    Caller wajib sudah memegang kunci_mesin() dan memastikan sesi.status == aktif.
    """
    items = db.query(ScanPending).filter(
        ScanPending.sesi_id == sesi.id, ScanPending.status == StatusScan.menunggu
    ).order_by(ScanPending.created_at.asc()).all()

    for i in items:
        if not lengkap(i):  # baru satu sisi saat sesi ditutup -> tidak bisa dihitung
            i.status = StatusScan.kadaluarsa
    lengkap_items = [i for i in items if lengkap(i)]

    peta = peta_jenis_item(db, lengkap_items)
    bisa = [(i, peta[i.id]) for i in lengkap_items if peta[i.id] is not None]
    review = [i for i in lengkap_items if peta[i.id] is None]
    for i in review:
        i.status = StatusScan.butuh_review

    transaksi = None
    if bisa:
        ket = f"Setoran sesi #{sesi.id} di mesin {sesi.machine_id} (IoT+ML)"
        if admin_username:
            ket += f", dikonfirmasi admin {admin_username}"
        transaksi = buat_transaksi_iot(
            db,
            nik=sesi.nik,
            items=[
                {
                    "id_jenis": j.id_jenis,
                    "berat": i.berat,
                    "jenis_terdeteksi": i.jenis_terdeteksi,
                    # jenis dipilih manual oleh admin = sudah diverifikasi manusia -> jangan kena flag confidence rendah
                    "confidence": None if i.id_jenis_override is not None else i.confidence,
                    "foto_url": i.foto_url,
                }
                for i, j in bisa
            ],
            keterangan=ket,
        )
        for i, _ in bisa:
            i.status = StatusScan.selesai
            i.id_transaksi = transaksi.id_transaksi
        sesi.id_transaksi = transaksi.id_transaksi

    sesi.status = StatusSesi.selesai
    sesi.selesai_at = now_wib()
    db.commit()

    if transaksi:
        pesan = f"Sesi selesai, {len(bisa)} item masuk transaksi"
        if review:
            pesan += f", {len(review)} item butuh review (jenis belum dipilih)"
    elif review:
        pesan = f"Sesi selesai, tapi {len(review)} item butuh review (jenis belum dipilih)"
    else:
        pesan = "Sesi selesai, tidak ada item lengkap yang tercatat"

    return SesiResponse(
        id_sesi=sesi.id, machine_id=sesi.machine_id, nik=sesi.nik, status=sesi.status.value,
        pesan=pesan, jumlah_item_lengkap=len(bisa), jumlah_item_butuh_review=len(review),
        started_at=sesi.started_at, transaksi=transaksi,
    )


def _respon_item(db: Session, scan: ScanPending) -> ScanResponse:
    if lengkap(scan):
        pesan = f"Item lengkap. Total item lengkap di sesi ini: {hitung_lengkap(db, scan.sesi_id)}"
    else:
        sisi = "ML (jenis sampah)" if scan.jenis_terdeteksi is None else "IoT (berat)"
        pesan = f"Data diterima, menunggu data dari {sisi}"
    return ScanResponse(id=scan.id, machine_id=scan.machine_id, status=scan.status.value, pesan=pesan)


# ====================================================================
# TIM IoT: HEARTBEAT (status "Tersambung")
# ====================================================================
@router.post("/heartbeat")
def heartbeat(
    data: HeartbeatRequest,
    db: Session = Depends(get_db),
    _: None = Depends(verify_iot_api_key),
):
    """
    Dipanggil alat IoT tiap ~10 detik (header X-API-Key sama seperti /berat).
    Alat harus sudah didaftarkan admin di halaman Database IoT.
    """
    perangkat = db.query(PerangkatIoT).filter(PerangkatIoT.machine_id == data.machine_id).first()
    if not perangkat:
        raise HTTPException(status_code=404, detail="machine_id belum terdaftar. Minta admin menambahkannya di Database IoT.")
    if not perangkat.is_active:
        raise HTTPException(status_code=403, detail="Perangkat dinonaktifkan oleh admin")
    perangkat.last_seen = now_wib()
    sesi = db.query(SesiSetoran).filter(
        SesiSetoran.machine_id == data.machine_id, SesiSetoran.status == StatusSesi.aktif
    ).first()
    db.commit()
    return {"status": "ok", "machine_id": data.machine_id, "sesi_aktif": sesi is not None}


# ====================================================================
# TIM IoT: KIRIM BERAT
# ====================================================================
@router.post("/berat", response_model=ScanResponse)
def kirim_berat(
    data: IoTBeratCreate,
    db: Session = Depends(get_db),
    _: None = Depends(verify_iot_api_key),
):
    if _test_mode():
        _print_test("IoT /berat", {"machine_id": data.machine_id, "berat": data.berat, "scan_id": data.scan_id})
        return ScanResponse(id=0, machine_id=data.machine_id, status="test",
                            pesan="[TEST MODE] Data berat diterima & dicetak di terminal server, tidak disimpan")
    # DEBUG: tampilkan data IoT yang masuk ke terminal backend (hanya di development)
    if os.getenv("ENVIRONMENT", "development") == "development":
        print(f"\n{'='*60}")
        print(f"[IoT /berat] {now_wib():%H:%M:%S} machine_id={data.machine_id} "
              f"berat={data.berat} scan_id={data.scan_id}")
        print(f"{'='*60}\n", flush=True)

    sesi = _wajib_sesi(db, data.machine_id)
    scan = _cari_scan(db, sesi.id, data.scan_id, ScanPending.berat)
    if scan:
        scan.berat = data.berat
    else:
        scan = ScanPending(machine_id=data.machine_id, scan_id=data.scan_id,
                           nik=sesi.nik, sesi_id=sesi.id, berat=data.berat)
        db.add(scan)
        db.flush()
    sesi.terakhir_aktif = now_wib()
    # kirim berat juga membuktikan alat hidup
    perangkat = db.query(PerangkatIoT).filter(PerangkatIoT.machine_id == data.machine_id).first()
    if perangkat:
        perangkat.last_seen = now_wib()
    hasil = _respon_item(db, scan)
    db.commit()
    return hasil


# ====================================================================
# TIM ML: KIRIM HASIL DETEKSI
# ====================================================================
@router.post("/deteksi", response_model=ScanResponse)
def kirim_deteksi(
    data: MLDeteksiCreate,
    db: Session = Depends(get_db),
    _: None = Depends(verify_ml_api_key),
):
    if _test_mode():
        foto = f"ada ({len(data.foto_base64)} karakter base64, tidak disimpan)" if data.foto_base64 else "tidak ada"
        _print_test("ML /deteksi", {
            "machine_id": data.machine_id,
            "jenis_terdeteksi": data.jenis_terdeteksi,
            "confidence": data.confidence,
            "foto": foto,
            "scan_id": data.scan_id,
        })
        return ScanResponse(id=0, machine_id=data.machine_id, status="test",
                            pesan="[TEST MODE] Data deteksi diterima & dicetak di terminal server, tidak disimpan")

    sesi = _wajib_sesi(db, data.machine_id)
    foto_url = _save_base64_image(data.foto_base64)
    scan = _cari_scan(db, sesi.id, data.scan_id, ScanPending.jenis_terdeteksi)
    if scan:
        scan.jenis_terdeteksi = data.jenis_terdeteksi
        scan.confidence = data.confidence
        scan.foto_url = foto_url
    else:
        scan = ScanPending(machine_id=data.machine_id, scan_id=data.scan_id, nik=sesi.nik,
                           sesi_id=sesi.id, jenis_terdeteksi=data.jenis_terdeteksi,
                           confidence=data.confidence, foto_url=foto_url)
        db.add(scan)
        db.flush()
    sesi.terakhir_aktif = now_wib()
    hasil = _respon_item(db, scan)
    db.commit()
    return hasil
