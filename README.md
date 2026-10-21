# Python Photo Booth (Photo Strip Style)

Program ini mengambil beberapa foto berturut-turut dan menyusunnya sesuai layout template, lalu membuat JPG dan GIF dengan bingkai yang sama.

## Cara Instalasi

1. Pastikan Python sudah terinstall.
2. Install library yang dibutuhkan:
   ```bash
   pip install -r requirements.txt
   ```

## Cara Menjalankan

Buka PowerShell di folder utama proyek, lalu jalankan photobooth dengan salah satu perintah berikut:

```powershell
.\run_app.bat
```

Atau jalankan langsung dengan Python:

```powershell
python app.py
```

## Cara Import Template

1. Buat subfolder baru di dalam `assets/templates`, misalnya `template_baru`.
2. Masukkan gambar template berformat PNG ke subfolder tersebut, lalu ganti nama filenya menjadi `overlay.png`. Sebaiknya area untuk foto dibuat transparan agar dapat dideteksi otomatis.

   Contoh struktur folder:

   ```text
   assets/
     templates/
       template_baru/
         overlay.png
   ```

3. Buka PowerShell di folder utama proyek, lalu jalankan:

   ```powershell
   .\run_import.bat
   ```

4. Script akan memindai semua subfolder di `assets/templates`, membuat atau memperbarui `config.json` jika area foto berhasil dideteksi, dan membuat `thumbnail.png` jika belum ada.
5. Setelah import selesai, jalankan kembali photobooth dengan `.\run_app.bat` untuk menggunakan template yang sudah diimport.

## Cara Kerja

### Antarmuka dan Navigasi Keyboard

- Template aktif tetap tersorot setelah pointer berpindah, jadi pilihan mudah dikenali.
- Saat kamera terputus, preview menghapus frame lama dan menampilkan status koneksi; gambar live muncul lagi setelah kamera tersambung.
- Jendela preview menyesuaikan ukuran layar. Pada layar sempit, panel ditata agar tetap muat.
- Di preview, **Tab** dan **Shift+Tab** berpindah antar tombol. **Enter** atau **Spasi** menjalankan tombol terpilih; **ESC** menutup preview dan menyimpan pilihan saat ini.

### Pemulihan Saat Kamera Bermasalah

- Aplikasi tetap terbuka dan standby di halaman pilihan template setelah sesi selesai, dibatalkan, atau melewati batas idle; tidak perlu dijalankan ulang untuk tamu berikutnya.

- Aplikasi tetap terbuka dan standby di halaman pilihan template setelah sesi selesai, dibatalkan, atau melewati batas idle; tidak perlu dijalankan ulang untuk tamu berikutnya.
- Setelah 60 detik tanpa aktivitas di halaman pilihan template, live preview dijeda dan kamera dilepas. Gerakkan mouse, klik, atau tekan tombol untuk mengaktifkannya kembali.
- Jika koneksi kamera terputus sebelum tombol SPASI ditekan, sesi kembali ke halaman awal tanpa menahan pengguna di dialog error. Percobaan membuka ulang kamera di background tidak menunggu thread lama pada UI.
- Jendela kamera yang menunggu tombol SPASI otomatis membatalkan sesi setelah 2 menit tanpa tombol ditekan. Preview hasil juga otomatis selesai setelah 2 menit tanpa interaksi; hasilnya tetap tersimpan di `riwayat`.
- QR otomatis kembali ke halaman pilihan template setelah 40 detik. Tombol **Tutup** masih bisa digunakan untuk kembali ke hasil foto.
- Preview dan QR tidak menghentikan antrean callback aplikasi saat terbuka. Timer dibersihkan ketika jendela ditutup, dan callback Tk yang gagal dicatat di log.
- Live preview otomatis mencoba menyambungkan ulang kamera jika pembacaan frame gagal, misalnya setelah kamera terputus atau laptop bangun dari sleep. Percobaan ulang diberi jeda 2 detik.
- Jika kamera terputus saat sesi foto, sesi dibatalkan dan kamera dilepas. Setelah koneksi kembali, pilih template lagi untuk mengulang sesi.
- Jika pemrosesan foto gagal, tombol template diaktifkan kembali agar aplikasi dapat digunakan tanpa harus ditutup.
- Error dari live preview, sesi foto, dan callback antrean UI dicatat di `photobooth.log` saat aplikasi dijalankan melalui `.\run_app.bat`. Log dibatasi sekitar 1 MB per file dengan maksimal 2 file cadangan.

Untuk penggunaan lama, pastikan pengaturan sleep laptop sesuai kebutuhan. Pemulihan otomatis tetap bergantung pada kamera dan driver yang kembali merespons.

### Alur Foto

1. Pilih template di aplikasi. Jendela kamera akan terbuka; tekan **SPASI** untuk memulai pengambilan foto.
2. Program menghitung mundur 6 detik untuk setiap foto. Jumlah foto mengikuti konfigurasi template.
3. Hasil JPG dan GIF otomatis disimpan dengan nama unik di folder `riwayat`, sebelum ditampilkan di jendela preview. Tidak perlu menekan tombol simpan.
4. GIF memakai seluruh bingkai template. Foto bergantian di setiap slot dengan urutan boomerang; rasio bingkai dipertahankan, dengan ukuran maksimal 480 × 720 piksel.

### Riwayat Foto Otomatis

- Folder `riwayat` berada di folder utama proyek dan dibuat otomatis saat diperlukan.
- Setiap sesi yang berhasil diproses langsung menyimpan JPG dan GIF, meskipun preview dibiarkan, ditutup, atau pengguna tidak menekan **Selesai**.
- Hasil retake disimpan sebagai file baru sehingga hasil sebelumnya tetap ada. Nama file memakai waktu dan ID unik agar hasil antar sesi tidak saling menimpa.
- Jika memilih download gabungan, ZIP juga disimpan di folder yang sama.
- Tombol **Selesai** hanya menutup preview dan mengembalikan aplikasi ke pilihan template; penyimpanan sudah dilakukan sebelumnya.

### Download Foto dan GIF dengan Satu QR

1. Di jendela preview, pilih **Download Foto + GIF (1 QR)**.
2. Aplikasi membuat satu ZIP berisi `foto.jpg` dan `animasi.gif`, lalu mengupload ZIP tersebut ke tmpfiles.org.
3. Scan QR menggunakan HP, tekan **Download** di halaman tmpfiles.org yang terbuka, lalu ekstrak/buka ZIP untuk mengambil kedua file.

Tombol **Download Foto** dan **Download GIF** tetap tersedia untuk download terpisah. Tombol gabungan hanya muncul jika GIF berhasil dibuat. Upload memerlukan internet; JPG, GIF, dan ZIP tetap tersimpan lokal. Menurut [dokumentasi API tmpfiles.org](https://tmpfiles.org/api), link secara default kedaluwarsa setelah 1 jam.

### Ulang Satu Foto

1. Di jendela preview, pilih **Ulang Foto 1, 2, ...** pada thumbnail foto yang ingin diganti.
2. Jendela kamera terbuka. Tekan **SPASI** untuk memulai countdown dan mengambil satu foto pengganti.
3. JPG dan GIF dibuat ulang menggunakan foto pengganti; foto lainnya tetap sama. Download berikutnya menggunakan hasil terbaru.

Tekan **ESC** untuk membatalkan retake dan kembali ke hasil sebelumnya. Jika kamera atau pemrosesan gagal, hasil sebelumnya tetap tersedia. Setiap hasil yang diperbarui disimpan dengan nama file baru. Tombol **Ulang Semua** tetap tersedia untuk mengambil ulang seluruh sesi.

## Kustomisasi

- Anda dapat mengganti `overlay.png` dengan desain bingkai Anda sendiri (pastikan format PNG dengan background transparan di area foto).
- Ukuran, jumlah foto, dan posisi slot mengikuti `config.json` di subfolder template. Jeda pengambilan foto dapat diubah di `camera_engine.py`.
