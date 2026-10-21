# Setup Upload Google Drive (QR Download)

Hasil foto/GIF/ZIP diupload ke folder **PhotoBooth SIMETRI** di Google Drive (dibuat otomatis), lalu link-nya menjadi QR. Jika Drive belum di-setup atau gagal, aplikasi otomatis memakai layanan cadangan (tmpfiles.org / catbox.moe).

## Langkah Setup (sekali saja)

1. Install library:
   ```bash
   pip install -r requirements.txt
   ```
2. Buka https://console.cloud.google.com/ lalu **buat project baru** (mis. "PhotoBooth").
3. Menu **APIs & Services → Library** → cari **Google Drive API** → **Enable**.
4. Menu **OAuth consent screen**:
   - Pilih **External**, isi nama aplikasi dan email.
   - Tambahkan email Anda di **Test users**.
   - **PENTING:** setelah selesai, klik **Publish App** (status "In production"). Jika dibiarkan "Testing", login kedaluwarsa tiap 7 hari dan upload berhenti saat acara.
5. Menu **Credentials → Create Credentials → OAuth client ID** → tipe **Desktop app** → **Download JSON**.
6. Ganti nama file menjadi `credentials.json`, taruh di folder utama proyek (sejajar `app.py`).
7. Login satu kali:
   ```bash
   python drive_manager.py --auth
   ```
   Browser terbuka, login, izinkan akses. File `token.json` akan dibuat.
8. Jalankan `.\run_app.bat`. Upload sekarang memakai Google Drive.

## Catatan

- Folder Drive dibuat otomatis oleh aplikasi dan ID-nya disimpan di `drive_folder.json`.
- Foto dan GIF dibagikan dengan "siapa saja yang punya link"; ZIP langsung terunduh saat QR dipindai.
- Hapus file lama (privasi tamu), mis. yang lebih tua dari 24 jam:
  ```bash
  python drive_manager.py --cleanup 24
  ```
- `credentials.json`, `token.json`, dan `drive_folder.json` sudah masuk `.gitignore`. Jangan di-commit.
- Jika login perlu diulang: hapus `token.json`, lalu jalankan lagi `python drive_manager.py --auth`.
