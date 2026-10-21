# Panduan Setup Google Drive Upload

Fitur upload otomatis ke Google Drive memerlukan file `credentials.json` dari Google Cloud Console.

## Langkah-langkah:

1.  **Buka Google Cloud Console**: [https://console.cloud.google.com/](https://console.cloud.google.com/)
2.  **Buat Project Baru**: Beri nama misalnya "PhotoBooth".
3.  **Aktifkan Google Drive API**:
    *   Cari "Google Drive API" di kolom pencarian.
    *   Klik "Enable".
4.  **Konfigurasi OAuth Consent Screen**:
    *   Pilih "External".
    *   Isi nama aplikasi dan email support.
    *   Tambahkan email Anda sebagai "Test User".
5.  **Buat Credentials**:
    *   Pergi ke menu "Credentials".
    *   Klik "Create Credentials" -> "OAuth client ID".
    *   Pilih "Desktop app".
    *   Download file JSON yang dihasilkan.
6.  **Simpan File**:
    *   Rename file tersebut menjadi `credentials.json`.
    *   Pindahkan ke folder project ini: `c:\Users\Gading\Documents\github\photobooth\credentials.json`.

## Cara Penggunaan:
Saat pertama kali dijalankan, browser akan terbuka meminta izin akses ke Google Drive Anda. Login dengan akun Google Anda. Setelah itu, file `token.pickle` akan dibuat dan Anda tidak perlu login lagi.
