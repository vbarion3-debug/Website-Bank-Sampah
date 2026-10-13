"""Satu-satunya sumber waktu "sekarang" di backend: WIB (UTC+7).

Kolom DateTime di database menyimpan waktu TANPA info zona (naive), jadi
now_wib() mengembalikan datetime naive yang nilainya sudah waktu WIB.
WIB tidak punya DST, jadi offset tetap +7 aman dan tidak butuh paket tzdata.
"""
from datetime import datetime, timedelta, timezone

WIB = timezone(timedelta(hours=7), "WIB")


def now_wib() -> datetime:
    return datetime.now(WIB).replace(tzinfo=None)
