-- Geser SEMUA data lama dari UTC ke WIB (+7 jam).
-- JALANKAN TEPAT SATU KALI. Kalau dua kali, waktu jadi +14 jam.
--
-- Urutan aman:
--   1. pg_dump database dulu (backup)
--   2. Matikan backend
--   3. Jalankan file ini
--   4. Pasang kode baru (apply_wib.py --apply), lalu nyalakan backend
-- Kalau backend lama masih menulis data sebelum langkah 4, baris barunya ikut tergeser dobel.
--
-- psql -U <user> -d <database> -f shift_data_lama_ke_wib.sql

BEGIN;

UPDATE nasabah                   SET tanggal_daftar      = tanggal_daftar      + INTERVAL '7 hours';
UPDATE admin                     SET tanggal_dibuat      = tanggal_dibuat      + INTERVAL '7 hours';
UPDATE riwayat_saldo_adjustment  SET tanggal             = tanggal             + INTERVAL '7 hours';
UPDATE transaksi                 SET tanggal_transaksi   = tanggal_transaksi   + INTERVAL '7 hours';
UPDATE detail_transaksi          SET dikoreksi_at        = dikoreksi_at        + INTERVAL '7 hours';
UPDATE penarikan_saldo           SET tanggal_pengajuan   = tanggal_pengajuan   + INTERVAL '7 hours',
                                     tanggal_diproses    = tanggal_diproses    + INTERVAL '7 hours';
UPDATE pengumuman                SET tanggal_publikasi   = tanggal_publikasi   + INTERVAL '7 hours';
UPDATE mitra_pengepul            SET tanggal_ditambahkan = tanggal_ditambahkan + INTERVAL '7 hours',
                                     tanggal_diperbarui  = tanggal_diperbarui  + INTERVAL '7 hours';
UPDATE harga_mitra_pengepul      SET tanggal_diperbarui  = tanggal_diperbarui  + INTERVAL '7 hours';
UPDATE data_iot                  SET waktu_scan          = waktu_scan          + INTERVAL '7 hours';
UPDATE transaksi_jual_mitra      SET tanggal_transaksi   = tanggal_transaksi   + INTERVAL '7 hours';
UPDATE faq                       SET created_at          = created_at          + INTERVAL '7 hours',
                                     updated_at          = updated_at          + INTERVAL '7 hours';
UPDATE pesan_chat                SET created_at          = created_at          + INTERVAL '7 hours';
UPDATE chat_assignment           SET claimed_at          = claimed_at          + INTERVAL '7 hours';
UPDATE scan_pending              SET created_at          = created_at          + INTERVAL '7 hours',
                                     updated_at          = updated_at          + INTERVAL '7 hours';
UPDATE sesi_setoran              SET started_at          = started_at          + INTERVAL '7 hours',
                                     terakhir_aktif      = terakhir_aktif      + INTERVAL '7 hours',
                                     selesai_at          = selesai_at          + INTERVAL '7 hours';

-- OTP hanya berlaku 5 menit, tidak perlu digeser. Hapus yang tersisa.
DELETE FROM otp;

COMMIT;
