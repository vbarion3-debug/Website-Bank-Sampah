import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from html import escape

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")

NAMA_APLIKASI = "Bank Sampah"
MASA_BERLAKU_MENIT = 5  # samakan dengan timedelta di OTPService.create_otp

# Palet warna
WARNA_UTAMA = "#1B7F4B"
WARNA_UTAMA_GELAP = "#14633A"
WARNA_AKSEN_BG = "#EAF6EF"
WARNA_TEKS = "#1F2937"
WARNA_TEKS_PUDAR = "#6B7280"
WARNA_BORDER = "#E5E7EB"
WARNA_LATAR = "#F3F5F4"


def _buat_html(kode_otp: str, peran: str | None) -> str:
    kode = escape(kode_otp)
    keterangan_peran = (
        f"Kami menerima permintaan masuk ke akun <strong>{escape(peran)}</strong> {NAMA_APLIKASI}."
        if peran
        else f"Kami menerima permintaan masuk ke akun {NAMA_APLIKASI} Anda."
    )

    return f"""\
<!DOCTYPE html>
<html lang="id">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta name="color-scheme" content="light">
  <title>Kode Verifikasi {NAMA_APLIKASI}</title>
</head>
<body style="margin:0;padding:0;background-color:{WARNA_LATAR};">
  <!-- Teks pratinjau di daftar inbox -->
  <div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">
    Kode verifikasi Anda: {kode}. Berlaku {MASA_BERLAKU_MENIT} menit.
  </div>

  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
         style="background-color:{WARNA_LATAR};">
    <tr>
      <td align="center" style="padding:32px 16px;">

        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
               style="max-width:520px;background-color:#FFFFFF;border:1px solid {WARNA_BORDER};border-radius:12px;overflow:hidden;">

          <!-- Header -->
          <tr>
            <td align="center" style="background-color:{WARNA_UTAMA};padding:28px 24px;">
              <div style="font-family:Segoe UI,Helvetica,Arial,sans-serif;font-size:22px;font-weight:700;letter-spacing:0.5px;color:#FFFFFF;">
                {NAMA_APLIKASI}
              </div>
              <div style="font-family:Segoe UI,Helvetica,Arial,sans-serif;font-size:13px;color:#CDEBDA;margin-top:4px;">
                Verifikasi Keamanan Akun
              </div>
            </td>
          </tr>

          <!-- Isi -->
          <tr>
            <td style="padding:32px 32px 8px 32px;font-family:Segoe UI,Helvetica,Arial,sans-serif;color:{WARNA_TEKS};">
              <p style="margin:0 0 12px 0;font-size:16px;font-weight:600;">Yth. Pengguna,</p>
              <p style="margin:0;font-size:14px;line-height:1.7;color:{WARNA_TEKS};">
                {keterangan_peran} Gunakan kode verifikasi berikut untuk melanjutkan proses masuk:
              </p>
            </td>
          </tr>

          <!-- Kode OTP -->
          <tr>
            <td align="center" style="padding:20px 32px;">
              <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
                     style="background-color:{WARNA_AKSEN_BG};border:1px solid #BFE3CE;border-radius:10px;">
                <tr>
                  <td align="center" style="padding:22px 12px 8px 12px;font-family:Segoe UI,Helvetica,Arial,sans-serif;font-size:12px;letter-spacing:1.5px;text-transform:uppercase;color:{WARNA_UTAMA_GELAP};">
                    Kode Verifikasi
                  </td>
                </tr>
                <tr>
                  <td align="center" style="padding:0 12px 22px 12px;font-family:Consolas,'Courier New',monospace;font-size:38px;font-weight:700;letter-spacing:10px;color:{WARNA_UTAMA_GELAP};">
                    {kode}
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Masa berlaku -->
          <tr>
            <td align="center" style="padding:0 32px 20px 32px;font-family:Segoe UI,Helvetica,Arial,sans-serif;font-size:14px;color:{WARNA_TEKS};">
              Kode ini berlaku selama <strong>{MASA_BERLAKU_MENIT} menit</strong> dan hanya dapat digunakan satu kali.
            </td>
          </tr>

          <!-- Peringatan keamanan -->
          <tr>
            <td style="padding:0 32px 32px 32px;">
              <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
                     style="background-color:#FFFBEB;border-left:4px solid #F59E0B;border-radius:6px;">
                <tr>
                  <td style="padding:14px 16px;font-family:Segoe UI,Helvetica,Arial,sans-serif;font-size:13px;line-height:1.6;color:#78350F;">
                    <strong>Demi keamanan akun Anda:</strong><br>
                    Jangan membagikan kode ini kepada siapa pun, termasuk pihak yang mengaku sebagai petugas {NAMA_APLIKASI}.
                    Jika Anda tidak merasa melakukan permintaan ini, abaikan email ini dan segera hubungi administrator.
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td align="center" style="background-color:#FAFAFA;border-top:1px solid {WARNA_BORDER};padding:20px 24px;font-family:Segoe UI,Helvetica,Arial,sans-serif;font-size:12px;line-height:1.6;color:{WARNA_TEKS_PUDAR};">
              Email ini dikirim otomatis oleh sistem {NAMA_APLIKASI}.<br>
              Mohon tidak membalas email ini.
            </td>
          </tr>

        </table>

        <div style="font-family:Segoe UI,Helvetica,Arial,sans-serif;font-size:11px;color:#9CA3AF;margin-top:16px;">
          &copy; {NAMA_APLIKASI}. Semua hak dilindungi.
        </div>

      </td>
    </tr>
  </table>
</body>
</html>
"""


def _buat_teks(kode_otp: str, peran: str | None) -> str:
    """Versi teks polos, untuk klien email yang tidak menampilkan HTML."""
    akun = f"akun {peran} {NAMA_APLIKASI}" if peran else f"akun {NAMA_APLIKASI}"
    return (
        f"{NAMA_APLIKASI} - Verifikasi Keamanan Akun\n\n"
        f"Yth. Pengguna,\n\n"
        f"Kami menerima permintaan masuk ke {akun}. "
        f"Gunakan kode verifikasi berikut untuk melanjutkan:\n\n"
        f"    {kode_otp}\n\n"
        f"Kode berlaku selama {MASA_BERLAKU_MENIT} menit dan hanya dapat digunakan satu kali.\n\n"
        f"Demi keamanan, jangan bagikan kode ini kepada siapa pun. "
        f"Jika Anda tidak merasa melakukan permintaan ini, abaikan email ini "
        f"dan segera hubungi administrator.\n\n"
        f"Email ini dikirim otomatis. Mohon tidak membalas.\n"
    )


def send_otp_email(to_email: str, kode_otp: str, peran: str | None = None):
    """
    Kirim email OTP (HTML + fallback teks polos).

    `peran` opsional (mis. "Admin" / "Super Admin") untuk ditampilkan di isi email.
    Kalau tidak diisi, teks email tetap umum, jadi pemanggilan lama
    send_otp_email(email, kode_otp) tetap berfungsi.
    """
    msg = MIMEMultipart("alternative")
    msg["From"] = formataddr((NAMA_APLIKASI, SMTP_USER))
    msg["To"] = to_email
    msg["Subject"] = f"Kode Verifikasi Login - {NAMA_APLIKASI}"

    # Urutan penting: teks polos dulu, HTML terakhir (klien email menampilkan bagian terakhir yang didukung)
    msg.attach(MIMEText(_buat_teks(kode_otp, peran), "plain", "utf-8"))
    msg.attach(MIMEText(_buat_html(kode_otp, peran), "html", "utf-8"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)