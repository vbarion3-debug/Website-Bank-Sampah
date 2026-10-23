"""
Deteksi sampah dengan YOLOv8, dijalankan langsung di backend.

- Model dimuat SEKALI (lazy, saat scan / cek model pertama kali), bukan saat server start,
  jadi server tetap bisa naik walau 'ultralytics' belum terpasang atau bobot belum ada.
- Satu inferensi pada satu waktu (lock) supaya memori dan CPU aman.
- Pengaturan lewat .env:
    YOLO_MODEL_PATH  default "yolov8n.pt"  (nanti ganti ke hasil training, mis. "models/best.pt")
    YOLO_CONF        default 0.25          (ambang minimum deteksi, 0-1)
    YOLO_IMGSZ       default 640
"""
import base64
import io
import os
import threading
from dataclasses import dataclass
from typing import Dict, List, Tuple

MODEL_PATH = os.getenv("YOLO_MODEL_PATH", "yolov8n.pt")
CONF = float(os.getenv("YOLO_CONF", "0.25"))
IMGSZ = int(os.getenv("YOLO_IMGSZ", "640"))
MAX_BYTES = 8 * 1024 * 1024  # foto dari browser sudah ~640 px (puluhan KB); batas ini hanya pengaman

_model = None
_lock = threading.Lock()


class DetektorError(Exception):
    """Kesalahan deteksi. status_code dipakai endpoint: 400 = gambar tidak valid, 503 = model belum siap."""

    def __init__(self, pesan: str, status_code: int = 503):
        super().__init__(pesan)
        self.status_code = status_code


@dataclass
class Deteksi:
    label: str          # nama kelas YOLO, dicocokkan ke JenisSampah.kode_ml
    confidence: float   # 0-100


def _muat_model():
    """Panggil hanya sambil memegang _lock."""
    global _model
    if _model is None:
        try:
            from ultralytics import YOLO
        except ImportError:
            raise DetektorError("Paket 'ultralytics' belum terpasang di server. Jalankan: pip install ultralytics")
        try:
            _model = YOLO(MODEL_PATH)
        except Exception as e:
            raise DetektorError(f"Gagal memuat model '{MODEL_PATH}': {e}")
    return _model


def nama_kelas() -> Dict[int, str]:
    """Daftar kelas model yang sedang dipakai: {id: nama}."""
    with _lock:
        model = _muat_model()
        return {int(k): str(v) for k, v in model.names.items()}


def _decode(foto_base64: str) -> bytes:
    data = foto_base64.strip()
    if data.startswith("data:") and "," in data:
        data = data.split(",", 1)[1]
    try:
        raw = base64.b64decode(data, validate=False)
    except Exception:
        raise DetektorError("Foto tidak valid (base64 rusak)", 400)
    if not raw:
        raise DetektorError("Foto kosong", 400)
    if len(raw) > MAX_BYTES:
        raise DetektorError("Foto terlalu besar", 400)
    return raw


def deteksi_gambar(foto_base64: str) -> Tuple[List[Deteksi], bytes]:
    """
    Jalankan YOLO pada 1 foto.
    Return (daftar deteksi urut confidence menurun, JPEG hasil dengan kotak deteksi).
    """
    from PIL import Image, ImageOps

    raw = _decode(foto_base64)
    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")
    except Exception:
        raise DetektorError("Foto tidak bisa dibaca sebagai gambar", 400)

    with _lock:
        model = _muat_model()
        hasil = model.predict(img, conf=CONF, imgsz=IMGSZ, verbose=False)[0]
        names = hasil.names
        deteksi = [
            Deteksi(label=str(names[int(b.cls[0])]), confidence=round(float(b.conf[0]) * 100, 2))
            for b in hasil.boxes
        ]
        bergambar = hasil.plot()  # ndarray BGR berisi kotak + label

    keluar = Image.fromarray(bergambar[..., ::-1])  # BGR -> RGB
    buf = io.BytesIO()
    keluar.save(buf, "JPEG", quality=85)
    deteksi.sort(key=lambda d: d.confidence, reverse=True)
    return deteksi, buf.getvalue()
