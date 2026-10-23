from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
import os
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import logging

# Import database & models
from app.core.database import engine, Base
from app.models import models

# Import routers
from app.api import (admin, nasabah, auth, transaksi, pengumuman, 
                     jenis_sampah, admin_nasabah, mitra_pengepul, transaksi_jual_mitra, 
                     admin_faq, nasabah_faq, chat, iot_ml, admin_iot, penarikan, 
                     admin_penarikan, notifikasi)

# Load .env
load_dotenv()

# Configure logging
logger = logging.getLogger("uvicorn.error")

# Create tables
Base.metadata.create_all(bind=engine)

# Create FastAPI app
app = FastAPI(
    title="API Bank Sampah",
    description="Backend untuk proyek Bank Sampah (IoT, ML, Web)",
    version="1.0.0"
)

# Global Exception Handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled error di {request.url}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Terjadi kesalahan di server. Coba lagi nanti."}
    )

# ========== CORSMiddleware ==========
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ========== STATIC FILES ==========
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "static")
os.makedirs(os.path.join(STATIC_DIR, "uploads", "sampah"), exist_ok=True)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# ========== INCLUDE ROUTERS ==========
app.include_router(auth.router)                     # /auth
app.include_router(nasabah.router)                  # /nasabah
app.include_router(admin.router)                    # /admin
app.include_router(transaksi.router)                # /transaksi
app.include_router(pengumuman.router)               # /pengumuman
app.include_router(jenis_sampah.router)             # /jenis-sampah
app.include_router(admin_nasabah.router)            # /admin/nasabah
app.include_router(mitra_pengepul.router)           # /mitra-pengepul
app.include_router(transaksi_jual_mitra.router)     # /transaksi-jual-mitra
app.include_router(admin_faq.router)                # /admin/faq
app.include_router(nasabah_faq.router)              # /faq
app.include_router(chat.router)                     # /livechat
app.include_router(iot_ml.router)                   # /iot-ml
app.include_router(admin_iot.router)                # /admin/iot
app.include_router(penarikan.router)                # /penarikan
app.include_router(admin_penarikan.router)          # /admin/penarikan
app.include_router(notifikasi.router)               # /notifikasi

# ========== HEALTH CHECK ENDPOINT ==========

@app.get("/")
def read_root():
    return {
        "status": "sukses",
        "pesan": "Server Bank Sampah berjalan dengan lancar!",
        "mode": os.getenv("ENVIRONMENT", "development"),
        "endpoints": {
            "auth": {
                "request_otp": "POST /auth/nasabah/request-otp",
                "verify_otp": "POST /auth/nasabah/verify-otp",
                "admin_login": "POST /auth/admin/login"
            },
            "nasabah": {
                "register": "POST /nasabah/register",
                "profile": "GET /nasabah/profile (Protected)",
                "history": "GET /nasabah/history (Protected)"
            },
            "transaksi": {
                "create": "POST /transaksi/create (Protected)",
                "history": "GET /transaksi/history (Protected)"
            }
        }
    }