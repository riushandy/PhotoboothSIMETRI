"""Upload hasil photobooth ke Google Drive dan hasilkan link untuk QR.

Setup sekali (lihat README_DRIVE.md):
    python drive_manager.py --auth
"""
import json
import logging
import mimetypes
import os
import threading
from datetime import datetime, timedelta, timezone

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    GOOGLE_LIBS_OK = True
except ImportError:  # library belum di-install
    GOOGLE_LIBS_OK = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_PATH = os.path.join(BASE_DIR, "credentials.json")
TOKEN_PATH = os.path.join(BASE_DIR, "token.json")
FOLDER_CACHE_PATH = os.path.join(BASE_DIR, "drive_folder.json")

# drive.file: aplikasi hanya bisa mengakses file/folder yang dibuatnya sendiri
SCOPES = ["https://www.googleapis.com/auth/drive.file"]
FOLDER_NAME = "PhotoBooth SIMETRI"

logger = logging.getLogger(__name__)


class DriveManager:
    def __init__(self, credentials_path=CREDENTIALS_PATH, token_path=TOKEN_PATH):
        self.credentials_path = credentials_path
        self.token_path = token_path
        self.creds = None
        self._lock = threading.Lock()
        self._folder_id = None

    # ── Auth ────────────────────────────────────────────────
    def authenticate(self, interactive=False):
        """Muat token. interactive=True membuka browser (hanya untuk setup)."""
        if not GOOGLE_LIBS_OK:
            logger.warning("Library Google belum terpasang: pip install -r requirements.txt")
            return False
        creds = None
        if os.path.exists(self.token_path):
            try:
                creds = Credentials.from_authorized_user_file(self.token_path, SCOPES)
            except Exception:
                logger.exception("token.json rusak")

        if creds and creds.valid:
            self.creds = creds
            return True

        try:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            elif interactive and os.path.exists(self.credentials_path):
                flow = InstalledAppFlow.from_client_secrets_file(self.credentials_path, SCOPES)
                creds = flow.run_local_server(port=0)
            else:
                return False
            with open(self.token_path, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
            self.creds = creds
            return True
        except Exception:
            logger.exception("Autentikasi Google Drive gagal")
            self.creds = None
            return False

    def is_ready(self):
        if self.creds and self.creds.valid:
            return True
        with self._lock:
            return self.authenticate(interactive=False)

    def _service(self):
        return build("drive", "v3", credentials=self.creds, cache_discovery=False)

    # ── Folder ──────────────────────────────────────────────
    def _ensure_folder(self, service):
        if self._folder_id:
            return self._folder_id
        if os.path.exists(FOLDER_CACHE_PATH):
            try:
                with open(FOLDER_CACHE_PATH, encoding="utf-8") as f:
                    cached = json.load(f).get("folder_id")
                if cached:
                    meta = service.files().get(fileId=cached, fields="id, trashed").execute()
                    if not meta.get("trashed"):
                        self._folder_id = cached
                        return cached
            except Exception:
                logger.info("Cache folder Drive tidak valid, membuat ulang")

        folder = service.files().create(
            body={"name": FOLDER_NAME, "mimeType": "application/vnd.google-apps.folder"},
            fields="id",
        ).execute()
        self._folder_id = folder["id"]
        with open(FOLDER_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump({"folder_id": self._folder_id}, f)
        return self._folder_id

    # ── Upload ──────────────────────────────────────────────
    def upload_file(self, file_path):
        """Upload file, buat 'anyone with link', kembalikan URL (atau None)."""
        if not self.is_ready():
            return None
        try:
            with self._lock:  # client Google API tidak thread-safe
                service = self._service()
                folder_id = self._ensure_folder(service)
                mime = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
                media = MediaFileUpload(file_path, mimetype=mime, resumable=True)
                file = service.files().create(
                    body={"name": os.path.basename(file_path), "parents": [folder_id]},
                    media_body=media,
                    fields="id",
                ).execute(num_retries=3)
                file_id = file["id"]
                service.permissions().create(
                    fileId=file_id,
                    body={"role": "reader", "type": "anyone"},
                ).execute(num_retries=3)
            return self.share_link(file_id, file_path)
        except Exception:
            logger.exception("Upload ke Google Drive gagal")
            return None

    @staticmethod
    def share_link(file_id, file_path):
        # ZIP tidak punya preview di Drive -> langsung unduh.
        if file_path.lower().endswith(".zip"):
            return f"https://drive.google.com/uc?export=download&id={file_id}"
        return f"https://drive.google.com/file/d/{file_id}/view"

    # ── Pembersihan (privasi tamu) ──────────────────────────
    def cleanup_old(self, hours=24):
        """Hapus file di folder PhotoBooth yang lebih tua dari `hours` jam."""
        if not self.is_ready():
            return 0
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        deleted = 0
        with self._lock:
            service = self._service()
            folder_id = self._ensure_folder(service)
            page = None
            while True:
                res = service.files().list(
                    q=f"'{folder_id}' in parents and createdTime < '{cutoff}' and trashed = false",
                    fields="nextPageToken, files(id)", pageToken=page,
                ).execute()
                for item in res.get("files", []):
                    service.files().delete(fileId=item["id"]).execute()
                    deleted += 1
                page = res.get("nextPageToken")
                if not page:
                    break
        return deleted


_instance = None
_instance_lock = threading.Lock()


def get_drive_manager():
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = DriveManager()
        return _instance


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Setup/maintenance Google Drive photobooth")
    parser.add_argument("--auth", action="store_true", help="login Google (sekali saja)")
    parser.add_argument("--cleanup", type=int, metavar="JAM", help="hapus file lebih tua dari N jam")
    args = parser.parse_args()

    dm = DriveManager()
    if args.auth:
        ok = dm.authenticate(interactive=True)
        print("Login berhasil. token.json dibuat." if ok else "Login gagal.")
    if args.cleanup:
        print(f"{dm.cleanup_old(args.cleanup)} file dihapus dari Drive.")
