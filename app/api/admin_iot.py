"""
Admin — kelola alat IoT & sesi setoran IoT+ML.

Alur: admin pilih alat yang "Tersambung" + NIK nasabah -> POST /admin/iot/sesi/mulai
      -> timbangan mengirim berat ke /iot-ml/berat; admin menekan SCAN di browser -> POST /admin/iot/sesi/{id}/scan
         (foto dikirim, YOLO jalan di backend, label + confidence masuk ke item sesi)
      -> admin memantau lewat GET /admin/iot/sesi/{id} (polling), boleh koreksi jenis tiap item
      -> POST /admin/iot/sesi/{id}/selesai  => Transaksi dibuat (algoritma sama seperti transaksi biasa).
"""
import base64
from datetime import timedelta
from decimal import Decimal
from typing import List

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.iot_ml import (
    CONFIDENCE_RENDAH, SESI_IDLE_MENIT, batalkan_sesi, is_online, kunci_mesin,
    lengkap, peta_jenis_item, sesi_aktif, tutup_sesi,
)
from app.api.transaksi import _save_base64_image
from app.core.database import get_db
from app.core.timezone import now_wib
from app.models.models import (
    Admin, JenisSampah, Nasabah, PerangkatIoT, ScanPending, SesiSetoran, StatusScan, StatusSesi,
)
from app.schemas.admin_iot import (
    DeteksiItem, ItemJenisUpdate, ItemSesiResponse, KelasModel, ModelInfoResponse,
    PerangkatCreate, PerangkatResponse, PerangkatUpdate, ScanHasilResponse, ScanRequest,
    SesiDetailResponse, SesiMulaiAdminRequest,
)
from app.schemas.iot_ml import SesiResponse
from app.utils.detector import MODEL_PATH, DetektorError, deteksi_gambar, nama_kelas
from app.utils.security import get_current_admin

router = APIRouter(prefix="/admin/iot", tags=["Admin - IoT & Sesi Setoran"])


# ========== HELPER ==========
def _get_admin_object(authorization: str, db: Session) -> Admin:
    session = get_current_admin(authorization)
    admin = db.query(Admin).filter(Admin.username == session["sub"]).first()
    if not admin:
        raise HTTPException(status_code=404, detail="Admin tidak ditemukan")
    return admin


def _bersihkan_sesi_basi(db: Session):
    """Sesi aktif yang idle terlalu lama dibatalkan, supaya alat tidak 'Sedang dipakai' selamanya."""
    batas = now_wib() - timedelta(minutes=SESI_IDLE_MENIT)
    basi = db.query(SesiSetoran).filter(
        SesiSetoran.status == StatusSesi.aktif, SesiSetoran.terakhir_aktif < batas
    ).all()
    for s in basi:
        batalkan_sesi(db, s)
    if basi:
        db.commit()


def _status_perangkat(p: PerangkatIoT, sesi) -> str:
    if not p.is_active:
        return "nonaktif"
    if sesi:
        return "dipakai"
    return "tersambung" if is_online(p) else "offline"


def _to_perangkat_response(p: PerangkatIoT, sesi, nama_by_nik: dict) -> PerangkatResponse:
    return PerangkatResponse(
        id=p.id, machine_id=p.machine_id, nama=p.nama, lokasi=p.lokasi,
        is_active=p.is_active, last_seen=p.last_seen, online=is_online(p),
        status=_status_perangkat(p, sesi),
        sesi_id=sesi.id if sesi else None,
        nik_pemakai=sesi.nik if sesi else None,
        nama_pemakai=nama_by_nik.get(sesi.nik) if sesi else None,
    )


def _detail_sesi(db: Session, sesi: SesiSetoran) -> SesiDetailResponse:
    perangkat = db.query(PerangkatIoT).filter(PerangkatIoT.machine_id == sesi.machine_id).first()
    nasabah = db.query(Nasabah).filter(Nasabah.nik == sesi.nik).first()

    # Sesi aktif: tampilkan item yang sedang berjalan. Sesi yang sudah ditutup: semua kecuali kadaluarsa.
    q = db.query(ScanPending).filter(ScanPending.sesi_id == sesi.id)
    if sesi.status == StatusSesi.aktif:
        q = q.filter(ScanPending.status == StatusScan.menunggu)
    else:
        q = q.filter(ScanPending.status != StatusScan.kadaluarsa)
    scans = q.order_by(ScanPending.created_at.asc()).all()
    peta = peta_jenis_item(db, scans)

    items: List[ItemSesiResponse] = []
    siap, total_berat, total_nilai = 0, Decimal(0), Decimal(0)
    for s in scans:
        jenis = peta.get(s.id)
        subtotal = (s.berat * jenis.harga_per_kg) if (s.berat is not None and jenis) else None
        diubah = s.id_jenis_override is not None
        conf_rendah = (s.confidence is not None and s.confidence < CONFIDENCE_RENDAH and not diubah)
        butuh_jenis = lengkap(s) and jenis is None
        if s.status == StatusScan.menunggu and lengkap(s) and jenis is not None:
            siap += 1
            total_berat += s.berat
            total_nilai += subtotal
        items.append(ItemSesiResponse(
            id=s.id, status=s.status.value, berat=s.berat,
            jenis_terdeteksi=s.jenis_terdeteksi, confidence=s.confidence, foto_url=s.foto_url,
            id_jenis=jenis.id_jenis if jenis else None,
            nama_jenis=jenis.nama_jenis if jenis else None,
            harga_per_kg=jenis.harga_per_kg if jenis else None,
            subtotal=subtotal, diubah_manual=diubah, lengkap=lengkap(s),
            perlu_perhatian=conf_rendah or butuh_jenis, created_at=s.created_at,
        ))

    return SesiDetailResponse(
        id_sesi=sesi.id, machine_id=sesi.machine_id,
        nama_perangkat=perangkat.nama if perangkat else None,
        perangkat_online=is_online(perangkat) if perangkat else False,
        nik=sesi.nik, nama_nasabah=nasabah.nama_nasabah if nasabah else None,
        status=sesi.status.value, started_at=sesi.started_at, terakhir_aktif=sesi.terakhir_aktif,
        items=items, jumlah_item_siap=siap, total_berat=total_berat, total_nilai=total_nilai,
    )


def _ambil_sesi(db: Session, id_sesi: int) -> SesiSetoran:
    sesi = db.query(SesiSetoran).filter(SesiSetoran.id == id_sesi).first()
    if not sesi:
        raise HTTPException(status_code=404, detail="Sesi tidak ditemukan")
    return sesi


def _ambil_sesi_aktif_terkunci(db: Session, id_sesi: int) -> SesiSetoran:
    """Ambil sesi, kunci mesinnya, lalu pastikan masih aktif (bisa saja baru ditutup request lain)."""
    sesi = _ambil_sesi(db, id_sesi)
    kunci_mesin(db, sesi.machine_id)
    db.refresh(sesi)
    if sesi.status != StatusSesi.aktif:
        db.commit()
        raise HTTPException(status_code=409, detail=f"Sesi ini sudah berstatus '{sesi.status.value}'")
    return sesi


# ========== DATABASE PERANGKAT ==========
@router.get("/perangkat", response_model=List[PerangkatResponse])
def list_perangkat(authorization: str = Header(None), db: Session = Depends(get_db)):
    """Daftar alat + status: offline / tersambung / dipakai / nonaktif."""
    get_current_admin(authorization)
    _bersihkan_sesi_basi(db)
    perangkat = db.query(PerangkatIoT).order_by(PerangkatIoT.nama.asc()).all()
    aktif = {
        s.machine_id: s
        for s in db.query(SesiSetoran).filter(SesiSetoran.status == StatusSesi.aktif).all()
    }
    niks = [s.nik for s in aktif.values()]
    nama_by_nik = (
        {n.nik: n.nama_nasabah for n in db.query(Nasabah).filter(Nasabah.nik.in_(niks)).all()}
        if niks else {}
    )
    return [_to_perangkat_response(p, aktif.get(p.machine_id), nama_by_nik) for p in perangkat]


@router.post("/perangkat", response_model=PerangkatResponse)
def create_perangkat(
    data: PerangkatCreate, authorization: str = Header(None), db: Session = Depends(get_db)
):
    get_current_admin(authorization)
    machine_id = data.machine_id.strip()
    if db.query(PerangkatIoT).filter(PerangkatIoT.machine_id == machine_id).first():
        raise HTTPException(status_code=400, detail="machine_id sudah terdaftar")
    p = PerangkatIoT(
        machine_id=machine_id, nama=data.nama.strip(),
        lokasi=(data.lokasi or "").strip() or None, is_active=data.is_active,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return _to_perangkat_response(p, None, {})


@router.patch("/perangkat/{machine_id}", response_model=PerangkatResponse)
def update_perangkat(
    machine_id: str, data: PerangkatUpdate,
    authorization: str = Header(None), db: Session = Depends(get_db),
):
    get_current_admin(authorization)
    p = db.query(PerangkatIoT).filter(PerangkatIoT.machine_id == machine_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Perangkat tidak ditemukan")
    update = data.model_dump(exclude_unset=True)
    if update.get("is_active") is False:
        sesi = db.query(SesiSetoran).filter(
            SesiSetoran.machine_id == machine_id, SesiSetoran.status == StatusSesi.aktif
        ).first()
        if sesi:
            raise HTTPException(status_code=409, detail="Alat sedang dipakai. Selesaikan atau batalkan sesinya dulu.")
    for k, v in update.items():
        setattr(p, k, v.strip() if isinstance(v, str) else v)
    db.commit()
    db.refresh(p)
    return _to_perangkat_response(p, None, {})


@router.delete("/perangkat/{machine_id}")
def delete_perangkat(
    machine_id: str, authorization: str = Header(None), db: Session = Depends(get_db)
):
    get_current_admin(authorization)
    p = db.query(PerangkatIoT).filter(PerangkatIoT.machine_id == machine_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Perangkat tidak ditemukan")
    sesi = db.query(SesiSetoran).filter(
        SesiSetoran.machine_id == machine_id, SesiSetoran.status == StatusSesi.aktif
    ).first()
    if sesi:
        raise HTTPException(status_code=409, detail="Alat sedang dipakai. Selesaikan atau batalkan sesinya dulu.")
    db.delete(p)
    db.commit()
    return {"message": "Perangkat berhasil dihapus"}


# ========== SESI SETORAN ==========
@router.post("/sesi/mulai", response_model=SesiDetailResponse)
def mulai_sesi(
    data: SesiMulaiAdminRequest, authorization: str = Header(None), db: Session = Depends(get_db)
):
    """Admin meng-assign alat ke nasabah. Alat harus terdaftar, aktif, TERSAMBUNG, dan tidak sedang dipakai."""
    admin = _get_admin_object(authorization, db)

    perangkat = db.query(PerangkatIoT).filter(PerangkatIoT.machine_id == data.machine_id).first()
    if not perangkat:
        raise HTTPException(status_code=404, detail="Perangkat tidak terdaftar")
    if not perangkat.is_active:
        raise HTTPException(status_code=400, detail="Perangkat sedang dinonaktifkan")
    if not is_online(perangkat):
        raise HTTPException(status_code=409, detail="Alat belum tersambung. Pastikan alat menyala dan terhubung internet.")

    nasabah = db.query(Nasabah).filter(Nasabah.nik == data.nik).first()
    if not nasabah:
        raise HTTPException(status_code=404, detail="Nasabah tidak ditemukan")
    if not nasabah.is_active:
        raise HTTPException(status_code=403, detail="Akun nasabah ini nonaktif")

    kunci_mesin(db, data.machine_id)
    if sesi_aktif(db, data.machine_id):
        db.commit()
        raise HTTPException(status_code=409, detail="Alat ini sedang dipakai sesi lain")

    # Satu nasabah hanya boleh punya satu sesi aktif (di alat mana pun)
    lain = db.query(SesiSetoran).filter(
        SesiSetoran.nik == data.nik, SesiSetoran.status == StatusSesi.aktif
    ).first()
    if lain:
        if lain.terakhir_aktif < now_wib() - timedelta(minutes=SESI_IDLE_MENIT):
            batalkan_sesi(db, lain)
            db.flush()
        else:
            db.commit()
            raise HTTPException(
                status_code=409,
                detail=f"Nasabah ini masih punya sesi aktif di alat {lain.machine_id}. Selesaikan dulu.",
            )

    sesi = SesiSetoran(machine_id=data.machine_id, nik=data.nik, id_admin=admin.id_admin)
    db.add(sesi)
    try:
        db.commit()
    except IntegrityError:  # pengaman terakhir dari unique index sesi aktif
        db.rollback()
        raise HTTPException(status_code=409, detail="Alat atau nasabah ini baru saja dipakai sesi lain")
    db.refresh(sesi)
    return _detail_sesi(db, sesi)


@router.get("/sesi/{id_sesi}", response_model=SesiDetailResponse)
def get_sesi(id_sesi: int, authorization: str = Header(None), db: Session = Depends(get_db)):
    """Dipolling frontend tiap 2-3 detik selama sesi berjalan."""
    get_current_admin(authorization)
    return _detail_sesi(db, _ambil_sesi(db, id_sesi))


@router.patch("/sesi/{id_sesi}/item/{scan_id}", response_model=SesiDetailResponse)
def ubah_jenis_item(
    id_sesi: int, scan_id: int, data: ItemJenisUpdate,
    authorization: str = Header(None), db: Session = Depends(get_db),
):
    """Admin memilih jenis sampah manual untuk 1 item (menimpa/melengkapi hasil kamera)."""
    get_current_admin(authorization)
    sesi = _ambil_sesi_aktif_terkunci(db, id_sesi)
    scan = db.query(ScanPending).filter(
        ScanPending.id == scan_id, ScanPending.sesi_id == sesi.id,
        ScanPending.status == StatusScan.menunggu,
    ).first()
    if not scan:
        raise HTTPException(status_code=404, detail="Item tidak ditemukan di sesi ini")
    if not db.query(JenisSampah).filter(JenisSampah.id_jenis == data.id_jenis).first():
        raise HTTPException(status_code=404, detail="Jenis sampah tidak ditemukan")
    scan.id_jenis_override = data.id_jenis
    sesi.terakhir_aktif = now_wib()
    db.commit()
    return _detail_sesi(db, sesi)


@router.delete("/sesi/{id_sesi}/item/{scan_id}", response_model=SesiDetailResponse)
def hapus_item(
    id_sesi: int, scan_id: int, authorization: str = Header(None), db: Session = Depends(get_db)
):
    """Buang item yang salah tangkap (mis. kamera membaca benda lain). Item ditandai kadaluarsa."""
    get_current_admin(authorization)
    sesi = _ambil_sesi_aktif_terkunci(db, id_sesi)
    scan = db.query(ScanPending).filter(
        ScanPending.id == scan_id, ScanPending.sesi_id == sesi.id,
        ScanPending.status == StatusScan.menunggu,
    ).first()
    if not scan:
        raise HTTPException(status_code=404, detail="Item tidak ditemukan di sesi ini")
    scan.status = StatusScan.kadaluarsa
    sesi.terakhir_aktif = now_wib()
    db.commit()
    return _detail_sesi(db, sesi)


# ========== SCAN KAMERA (YOLO di backend) ==========
def _target_scan(db: Session, sesi: SesiSetoran):
    """
    Item mana yang menerima label dari scan ini:
      (a) item yang sudah ditimbang tapi belum punya label  -> diisi (yang paling lama dulu)
      (b) item yang baru punya label & menunggu timbangan    -> label ditimpa (scan ulang)
      (c) tidak ada keduanya                                 -> None, caller membuat item baru (menunggu timbangan)
    Item yang jenisnya sudah dipilih manual oleh admin tidak disentuh.
    """
    dasar = db.query(ScanPending).filter(
        ScanPending.sesi_id == sesi.id,
        ScanPending.status == StatusScan.menunggu,
        ScanPending.id_jenis_override.is_(None),
    )
    item = dasar.filter(
        ScanPending.berat.isnot(None), ScanPending.jenis_terdeteksi.is_(None)
    ).order_by(ScanPending.created_at.asc()).first()
    if item:
        return item
    return dasar.filter(
        ScanPending.berat.is_(None), ScanPending.jenis_terdeteksi.isnot(None)
    ).order_by(ScanPending.created_at.desc()).first()


@router.post("/sesi/{id_sesi}/scan", response_model=ScanHasilResponse)
def scan_sampah(
    id_sesi: int, data: ScanRequest,
    authorization: str = Header(None), db: Session = Depends(get_db),
):
    """
    Tombol SCAN: terima 1 foto dari kamera browser, deteksi dengan YOLO, simpan hasil teratas
    (confidence tertinggi) ke item sesi. Satu scan = satu item.
    """
    get_current_admin(authorization)
    sesi = _ambil_sesi(db, id_sesi)
    if sesi.status != StatusSesi.aktif:
        raise HTTPException(status_code=409, detail=f"Sesi ini sudah berstatus '{sesi.status.value}'")

    # 1) Inferensi DULU, di luar kunci mesin, supaya /iot-ml/berat dari timbangan tidak ikut menunggu.
    try:
        deteksi, jpeg = deteksi_gambar(data.foto_base64)
    except DetektorError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))

    if not deteksi:
        return ScanHasilResponse(
            terdeteksi=False,
            pesan="Tidak ada sampah terdeteksi. Dekatkan sampah ke kamera, atau pilih jenisnya manual.",
            sesi=_detail_sesi(db, sesi),
        )

    labels = {d.label for d in deteksi}
    nama_by_kode = {
        j.kode_ml: j.nama_jenis
        for j in db.query(JenisSampah).filter(JenisSampah.kode_ml.in_(labels)).all()
    }
    atas = deteksi[0]
    foto_url = _save_base64_image(base64.b64encode(jpeg).decode())

    # 2) Tulis hasil ke item sesi (di bawah kunci mesin)
    sesi = _ambil_sesi_aktif_terkunci(db, id_sesi)
    scan = _target_scan(db, sesi)
    if scan:
        scan.jenis_terdeteksi = atas.label
        scan.confidence = Decimal(str(atas.confidence))
        scan.foto_url = foto_url
    else:
        scan = ScanPending(
            machine_id=sesi.machine_id, nik=sesi.nik, sesi_id=sesi.id,
            jenis_terdeteksi=atas.label, confidence=Decimal(str(atas.confidence)), foto_url=foto_url,
        )
        db.add(scan)
    sesi.terakhir_aktif = now_wib()
    db.commit()

    nama_atas = nama_by_kode.get(atas.label)
    if nama_atas:
        pesan = f"Terdeteksi {nama_atas} ({atas.confidence:g}%)."
    else:
        pesan = f"Terdeteksi '{atas.label}' ({atas.confidence:g}%), tapi belum dipetakan ke jenis sampah. Pilih jenisnya manual."
    lain = sorted({d.label for d in deteksi if d.label != atas.label})
    if lain:
        pesan += f" Terdeteksi juga: {', '.join(lain)}. Ubah lewat dropdown bila yang diambil salah."

    return ScanHasilResponse(
        terdeteksi=True, pesan=pesan, foto_url=foto_url,
        deteksi=[
            DeteksiItem(label=d.label, confidence=d.confidence, nama_jenis=nama_by_kode.get(d.label))
            for d in deteksi
        ],
        sesi=_detail_sesi(db, sesi),
    )


@router.get("/model", response_model=ModelInfoResponse)
def info_model(authorization: str = Header(None), db: Session = Depends(get_db)):
    """Kelas model YOLO yang sedang dimuat + sudah dipetakan ke jenis sampah mana (lewat kode_ml). Berguna saat mengisi Database Sampah."""
    get_current_admin(authorization)
    try:
        kelas = nama_kelas()
    except DetektorError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    peta = {
        j.kode_ml: j.nama_jenis
        for j in db.query(JenisSampah).filter(JenisSampah.kode_ml.isnot(None)).all()
    }
    return ModelInfoResponse(
        model_path=MODEL_PATH, jumlah_kelas=len(kelas),
        kelas=[KelasModel(id=i, nama=n, dipetakan_ke=peta.get(n)) for i, n in sorted(kelas.items())],
    )


@router.post("/sesi/{id_sesi}/selesai", response_model=SesiResponse)
def selesai_sesi(id_sesi: int, authorization: str = Header(None), db: Session = Depends(get_db)):
    """Simpan transaksi: item lengkap dijadikan 1 Transaksi, saldo nasabah bertambah, notifikasi terkirim."""
    admin = _get_admin_object(authorization, db)
    sesi = _ambil_sesi_aktif_terkunci(db, id_sesi)
    return tutup_sesi(db, sesi, admin_username=admin.username)


@router.post("/sesi/{id_sesi}/batal")
def batal_sesi(id_sesi: int, authorization: str = Header(None), db: Session = Depends(get_db)):
    """Batalkan sesi (mis. salah pilih nasabah). Item lengkap yang sudah masuk disimpan sebagai 'butuh_review'."""
    get_current_admin(authorization)
    sesi = _ambil_sesi_aktif_terkunci(db, id_sesi)
    batalkan_sesi(db, sesi)
    db.commit()
    return {"message": "Sesi dibatalkan"}
