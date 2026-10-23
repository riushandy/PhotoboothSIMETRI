# Prototipe Tablet (Laptop = Server, Tablet = Layar Sentuh)

Prototipe terpisah dari aplikasi lama (`app.py` tidak diubah). Tujuannya hanya menguji koneksi laptop ↔ tablet.

## Cara menjalankan

1. Pasang dependensi: `pip install -r requirements.txt`
2. Laptop dan tablet harus di **WiFi yang sama**.
3. Di laptop, jalankan:
   ```bash
   python tablet_server.py
   ```
   Tanpa kamera (hanya tes koneksi): `python tablet_server.py --fake`
4. Windows menampilkan peringatan firewall → klik **Allow access** (centang jaringan Private).
5. Terminal mencetak `Buka di tablet: http://192.168.x.x:5000`. Ketik alamat itu di browser tablet.
6. Alur uji: pilih template → countdown 3 detik per foto → hasil → **Upload & QR** (memakai `upload_manager.py` + Google Drive).

## Jika tablet tidak bisa membuka halaman

| Gejala | Solusi |
|---|---|
| Timeout / tidak terbuka | Cek WiFi sama, izinkan firewall (langkah 4), matikan "AP isolation" di router |
| Preview hitam | Kamera dipakai aplikasi lain; tutup `app.py` dulu |
| Upload gagal | Selesaikan setup Google Drive (`README_DRIVE.md`) |
