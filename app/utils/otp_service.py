import secrets
import string
from datetime import datetime, timedelta, timezone
from app.core.timezone import now_wib
from sqlalchemy.orm import Session
from app.models.models import OTP

class OTPService:
    @staticmethod
    def generate_otp() -> str:
        return ''.join(secrets.choice(string.digits) for _ in range(6))

    @staticmethod
    def create_otp(db: Session, identifier: str) -> str:
        """identifier bisa nomor HP (nasabah) atau email (super admin)"""
        db.query(OTP).filter(OTP.identifier == identifier).delete()
        db.commit()

        kode_otp = OTPService.generate_otp()
        expires_at = now_wib() + timedelta(minutes=5)

        otp_record = OTP(identifier=identifier, kode_otp=kode_otp, expires_at=expires_at)
        db.add(otp_record)
        db.commit()
        return kode_otp

    @staticmethod
    def verify_otp(db: Session, identifier: str, kode_otp: str) -> bool:
        otp_record = db.query(OTP).filter(
            OTP.identifier == identifier,
            OTP.kode_otp == kode_otp,
            OTP.is_used == False
        ).first()

        if not otp_record:
            return False
        if now_wib() > otp_record.expires_at:
            return False

        otp_record.is_used = True
        db.commit()
        return True