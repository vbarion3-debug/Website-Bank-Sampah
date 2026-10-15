import os
from datetime import datetime, timedelta
from app.core.timezone import now_wib
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import or_, text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.models import (
    ScanPending, StatusScan, SesiSetoran, StatusSesi, JenisSampah, Nasabah,
)
from app.schemas.iot_ml import (
    IoTBeratCreate, MLDeteksiCreate, ScanResponse, SesiMulaiRequest, SesiResponse,
)
from app.utils.security import verify_iot_api_key, verify_ml_api_key, get_current_nasabah
from app.api.transaksi import buat_transaksi_iot, _save_base64_image

router = APIRouter(prefix="/iot-ml", tags=["IoT & ML (terpisah)"])

WINDOW_DETIK = 15        # batas menjodohkan berat (IoT) dengan label (ML) dari mesin yang sama
SESI_IDLE_MENIT = 10     # sesi tanpa aktivitas selama ini dianggap basi -> dibatalkan


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
# HELPER
# ====================================================================
def _kunci_mesin(db: Session, machine_id: str):
    """Kunci advisory per mesin selama transaksi DB berjalan (lepas otomatis saat commit/rollback)."""
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:m))"), {"m": machine_id})


def _lengkap(item: ScanPending) -> bool:
    return item.berat is not None and item.jenis_terdeteksi is not None


def _batalkan_sesi(db: Session, sesi: SesiSetoran):
    """Sesi basi: item lengkap -> butuh_review (admin bisa tindak lanjut), item setengah -> kadaluarsa."""
    sesi.status = StatusSesi.batal
    sesi.selesai_at = now_wib()
    items = db.query(ScanPending).filter(
        ScanPending.sesi_id == sesi.id, ScanPending.status == StatusScan.menunggu
    ).all()
    for it in items:
        it.status = StatusScan.butuh_review if _lengkap(it) else StatusScan.kadaluarsa


def _sesi_aktif(db: Session, machine_id: str) -> Optional[SesiSetoran]:
    """Sesi aktif di mesin ini. Kalau sudah basi, dibatalkan dan dianggap tidak ada. Panggil SETELAH _kunci_mesin."""
    sesi = db.query(SesiSetoran).filter(
        SesiSetoran.machine_id == machine_id, SesiSetoran.status == StatusSesi.aktif
    ).first()
    if sesi and sesi.terakhir_aktif < now_wib() - timedelta(minutes=SESI_IDLE_MENIT):
        _batalkan_sesi(db, sesi)
        return None
    return sesi


def _expire_setengah(db: Session, machine_id: str):
    """Item yang baru satu sisi dan lewat jendela waktu -> kadaluarsa. Item lengkap TIDAK ikut (menunggu sesi ditutup)."""
    batas = now_wib() - timedelta(seconds=WINDOW_DETIK)
    db.query(ScanPending).filter(
        ScanPending.machine_id == machine_id,
        ScanPending.status == StatusScan.menunggu,
        ScanPending.created_at < batas,
        or_(ScanPending.berat.is_(None), ScanPending.jenis_terdeteksi.is_(None)),
    ).update({"status": StatusScan.kadaluarsa}, synchronize_session=False)


def _wajib_sesi(db: Session, machine_id: str) -> SesiSetoran:
    """Kunci mesin, bersihkan item basi, lalu pastikan ada sesi aktif. Kalau tidak ada -> 409."""
    _kunci_mesin(db, machine_id)
    _expire_setengah(db, machine_id)
    sesi = _sesi_aktif(db, machine_id)
    if not sesi:
        db.commit()  # simpan efek samping (sesi basi dibatalkan / item kadaluarsa) sebelum menolak
        raise HTTPException(
            status_code=409,
            detail="Tidak ada sesi setoran aktif di mesin ini. Nasabah harus scan QR dan mulai sesi dulu.",
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


def _hitung_lengkap(db: Session, sesi_id: int) -> int:
    return db.query(ScanPending).filter(
        ScanPending.sesi_id == sesi_id,
        ScanPending.status == StatusScan.menunggu,
        ScanPending.berat.isnot(None),
        ScanPending.jenis_terdeteksi.isnot(None),
    ).count()


def _respon_item(db: Session, scan: ScanPending) -> ScanResponse:
    if _lengkap(scan):
        pesan = f"Item lengkap. Total item lengkap di sesi ini: {_hitung_lengkap(db, scan.sesi_id)}"
    else:
        sisi = "ML (jenis sampah)" if scan.jenis_terdeteksi is None else "IoT (berat)"
        pesan = f"Data diterima, menunggu data dari {sisi}"
    return ScanResponse(id=scan.id, machine_id=scan.machine_id, status=scan.status.value, pesan=pesan)


# ====================================================================
# SESI (dipanggil WEB NASABAH, auth pakai token nasabah)
# ====================================================================
@router.post("/sesi/mulai", response_model=SesiResponse)
def mulai_sesi(
    data: SesiMulaiRequest,
    authorization: str = Header(None),
    db: Session = Depends(get_db),
):
    nik = get_current_nasabah(authorization)
    nasabah = db.query(Nasabah).filter(Nasabah.nik == nik).first()
    if not nasabah or not nasabah.is_active:
        raise HTTPException(status_code=403, detail="Akun nasabah tidak aktif")

    _kunci_mesin(db, data.machine_id)
    sesi = _sesi_aktif(db, data.machine_id)

    if sesi:
        if sesi.nik == nik:  # idempotent: tekan "mulai" dua kali tidak membuat sesi baru
            sesi.terakhir_aktif = now_wib()
            db.commit()
            return SesiResponse(
                id_sesi=sesi.id, machine_id=sesi.machine_id, nik=sesi.nik, status=sesi.status.value,
                pesan="Sesi sudah berjalan", jumlah_item_lengkap=_hitung_lengkap(db, sesi.id),
                started_at=sesi.started_at,
            )
        db.commit()
        raise HTTPException(status_code=409, detail="Mesin ini sedang dipakai nasabah lain")

    # Satu nasabah hanya boleh punya satu sesi aktif (di mesin mana pun)
    lain = db.query(SesiSetoran).filter(
        SesiSetoran.nik == nik, SesiSetoran.status == StatusSesi.aktif
    ).first()
    if lain:
        if lain.terakhir_aktif < now_wib() - timedelta(minutes=SESI_IDLE_MENIT):
            _batalkan_sesi(db, lain)
            db.flush()
        else:
            db.commit()
            raise HTTPException(
                status_code=409,
                detail=f"Kamu masih punya sesi aktif di mesin {lain.machine_id}. Selesaikan dulu.",
            )

    sesi = SesiSetoran(machine_id=data.machine_id, nik=nik)
    db.add(sesi)
    db.commit()
    db.refresh(sesi)
    return SesiResponse(
        id_sesi=sesi.id, machine_id=sesi.machine_id, nik=sesi.nik, status=sesi.status.value,
        pesan="Sesi dimulai, silakan masukkan sampah", started_at=sesi.started_at,
    )


@router.post("/sesi/selesai", response_model=SesiResponse)
def selesai_sesi(
    authorization: str = Header(None),
    db: Session = Depends(get_db),
):
    nik = get_current_nasabah(authorization)

    sesi = db.query(SesiSetoran).filter(
        SesiSetoran.nik == nik, SesiSetoran.status == StatusSesi.aktif
    ).first()
    if not sesi:
        raise HTTPException(status_code=404, detail="Tidak ada sesi aktif untuk akunmu")

    _kunci_mesin(db, sesi.machine_id)
    db.refresh(sesi)  # baca ulang setelah dapat kunci (bisa saja sudah ditutup request lain)
    if sesi.status != StatusSesi.aktif:
        raise HTTPException(status_code=409, detail="Sesi ini sudah ditutup")

    items = db.query(ScanPending).filter(
        ScanPending.sesi_id == sesi.id, ScanPending.status == StatusScan.menunggu
    ).order_by(ScanPending.created_at.asc()).all()
    lengkap = [i for i in items if _lengkap(i)]

    # Item yang baru satu sisi saat sesi ditutup tidak bisa dihitung
    for i in items:
        if not _lengkap(i):
            i.status = StatusScan.kadaluarsa

    peta = {}
    if lengkap:
        labels = {i.jenis_terdeteksi for i in lengkap}
        peta = {j.kode_ml: j for j in db.query(JenisSampah).filter(JenisSampah.kode_ml.in_(labels)).all()}

    bisa = [(i, peta[i.jenis_terdeteksi]) for i in lengkap if i.jenis_terdeteksi in peta]
    review = [i for i in lengkap if i.jenis_terdeteksi not in peta]
    for i in review:
        i.status = StatusScan.butuh_review

    transaksi = None
    if bisa:
        transaksi = buat_transaksi_iot(
            db,
            nik=nik,
            items=[
                {
                    "id_jenis": j.id_jenis,
                    "berat": i.berat,
                    "jenis_terdeteksi": i.jenis_terdeteksi,
                    "confidence": i.confidence,
                    "foto_url": i.foto_url,
                }
                for i, j in bisa
            ],
            keterangan=f"Setoran sesi #{sesi.id} di mesin {sesi.machine_id} (IoT+ML)",
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
            pesan += f", {len(review)} item butuh review admin"
    elif review:
        pesan = f"Sesi selesai, tapi {len(review)} item butuh review admin (label belum dipetakan)"
    else:
        pesan = "Sesi selesai, tidak ada item lengkap yang tercatat"

    return SesiResponse(
        id_sesi=sesi.id, machine_id=sesi.machine_id, nik=sesi.nik, status=sesi.status.value,
        pesan=pesan, jumlah_item_lengkap=len(bisa), jumlah_item_butuh_review=len(review),
        started_at=sesi.started_at, transaksi=transaksi,
    )


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