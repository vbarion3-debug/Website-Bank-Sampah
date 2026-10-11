import os
import secrets
from fastapi import HTTPException, Header
from passlib.context import CryptContext
from app.core.redis_client import redis_client

# ========== CONFIG ==========
SESSION_EXPIRE_SECONDS = 60 * 60 * 24  # 24 jam, sama seperti sebelumnya

IOT_API_KEY = os.getenv("IOT_API_KEY")
ML_API_KEY = os.getenv("ML_API_KEY")
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

LOGIN_RATE_LIMIT_MAX = 5                    # maksimal percobaan
LOGIN_RATE_LIMIT_WINDOW = 15 * 60           # 15 menit dalam detik
OTP_REQUEST_RATE_LIMIT_MAX = 3              # maksimal 3x kirim OTP
OTP_REQUEST_RATE_LIMIT_WINDOW = 10 * 60     # per 10 menit

# ========== PASSWORD UTILITIES (tetap dipakai untuk Admin) ==========
def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

# ========== SESSION UTILITIES (Redis) ==========
def create_session(subject: str, extra: dict = None) -> str:
    """Buat session baru di Redis. Return session_id (opaque token, bukan JWT)."""
    session_id = secrets.token_urlsafe(32)
    data = {"sub": subject}
    if extra:
        data.update(extra)
    key = f"session:{session_id}"
    redis_client.hset(key, mapping=data)
    redis_client.expire(key, SESSION_EXPIRE_SECONDS)
    return session_id

def get_session(session_id: str) -> dict | None:
    data = redis_client.hgetall(f"session:{session_id}")
    return data if data else None

def delete_session(session_id: str):
    redis_client.delete(f"session:{session_id}")

def refresh_session(session_id: str):
    """Perpanjang masa aktif session tiap kali dipakai (sliding expiration)"""
    redis_client.expire(f"session:{session_id}", SESSION_EXPIRE_SECONDS)

def extract_session_id(authorization: str) -> str:
    """Ambil session_id dari header 'Authorization: Bearer <session_id>'"""
    if not authorization:
        raise HTTPException(status_code=401, detail="Token tidak ditemukan")
    try:
        scheme, token = authorization.split()
        if scheme.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Invalid authentication scheme")
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid token format")
    return token

def delete_sessions_by_subject(subject: str, roles: tuple) -> int:
    """Hapus semua session aktif milik 1 user (dipakai saat akun dinonaktifkan/dihapus/ganti password)."""
    jumlah = 0
    for key in redis_client.scan_iter(match="session:*", count=200):
        sub, role = redis_client.hmget(key, "sub", "role")
        if sub == subject and role in roles:
            redis_client.delete(key)
            jumlah += 1
    return jumlah

# ========== DEPENDENCY untuk verify session ==========
def get_current_nasabah(authorization: str = None) -> str:
    """Extract NIK dari session yang aktif di Redis"""
    session_id = extract_session_id(authorization)
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Session tidak ditemukan/sudah expired, silakan login ulang")
    if session.get("role") != "nasabah":
        raise HTTPException(status_code=403, detail="Token ini bukan milik nasabah")
    refresh_session(session_id)
    return session["sub"]

def get_current_admin(authorization: str = None) -> dict:
    """Extract data admin dari session yang aktif di Redis"""
    session_id = extract_session_id(authorization)
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Session tidak ditemukan/sudah expired, silakan login ulang")
    if session.get("role") not in ("admin", "super_admin"):
        raise HTTPException(status_code=403, detail="Token ini bukan milik admin")
    refresh_session(session_id)
    return session  # {"sub": username, "role": "..."}

def require_super_admin(authorization: str = Header(None)) -> dict:
    """Dependency: pastikan yang akses adalah super_admin"""
    admin_session = get_current_admin(authorization)
    if admin_session.get("role") != "super_admin":
        raise HTTPException(status_code=403, detail="Hanya super admin yang bisa mengakses endpoint ini")
    return admin_session

def get_current_session(authorization: str = None) -> dict:
    """
    Dependency generik: pastikan ada session valid (bisa nasabah ATAU admin),
    tanpa peduli role-nya. Dipakai untuk endpoint yang boleh diakses siapa saja
    yang sudah login, seperti lihat pengumuman.
    """
    session_id = extract_session_id(authorization)
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Session tidak ditemukan/sudah expired, silakan login ulang")
    refresh_session(session_id)
    return session  

def verify_iot_api_key(x_api_key: str = Header(None)) -> None:
    """Dependency: pastikan request datang dari device IoT yang sah (pakai API Key statis)"""
    if not IOT_API_KEY:
        raise HTTPException(status_code=500, detail="IOT_API_KEY belum dikonfigurasi di server")
    if not x_api_key or x_api_key != IOT_API_KEY:
        raise HTTPException(status_code=401, detail="API Key tidak valid")

def verify_ml_api_key(x_api_key: str = Header(None)) -> None:
    """Dependency: pastikan request datang dari tim ML (API key terpisah dari IoT)"""
    if not ML_API_KEY:
        raise HTTPException(status_code=500, detail="ML_API_KEY belum dikonfigurasi di server")
    if not x_api_key or not secrets.compare_digest(x_api_key, ML_API_KEY):
        raise HTTPException(status_code=401, detail="API Key tidak valid")

def check_login_rate_limit(identifier: str):
    """
    Batasi percobaan login per identifier (NIK/no_hp/username) - max 5x per 15 menit.
    Dipakai untuk cegah enumerasi/brute force, terutama penting untuk login NIK-only.
    """
    key = f"login_attempt:{identifier}"
    current = redis_client.get(key)

    if current and int(current) >= LOGIN_RATE_LIMIT_MAX:
        ttl = redis_client.ttl(key)
        raise HTTPException(
            status_code=429,
            detail=f"Terlalu banyak percobaan login. Coba lagi dalam {ttl // 60 + 1} menit."
        )

    pipe = redis_client.pipeline()
    pipe.incr(key)
    pipe.expire(key, LOGIN_RATE_LIMIT_WINDOW, nx=True)  # set expire cuma kalau belum ada TTL
    pipe.execute()


def reset_login_rate_limit(identifier: str):
    """Reset counter setelah login berhasil"""
    redis_client.delete(f"login_attempt:{identifier}")

def check_otp_request_rate_limit(identifier: str):
    """
    Batasi permintaan OTP (email) per identifier - max 3x per 10 menit.
    Beda dari check_login_rate_limit yang membatasi percobaan password salah;
    ini khusus mencegah spam pengiriman email OTP.
    """
    key = f"otp_request:{identifier}"
    current = redis_client.get(key)

    if current and int(current) >= OTP_REQUEST_RATE_LIMIT_MAX:
        ttl = redis_client.ttl(key)
        raise HTTPException(
            status_code=429,
            detail=f"Terlalu banyak permintaan OTP. Coba lagi dalam {ttl // 60 + 1} menit."
        )

    pipe = redis_client.pipeline()
    pipe.incr(key)
    pipe.expire(key, OTP_REQUEST_RATE_LIMIT_WINDOW, nx=True)
    pipe.execute()