import os
import sys
import subprocess
import logging

class PrintManager:
    """Mengelola pencetakan dokumen/foto ke printer sistem."""

    @staticmethod
    def print_image(image_path: str) -> bool:
        """Kirim file gambar ke printer bawaan OS."""
        if not os.path.exists(image_path):
            logging.error(f"File gambar tidak ditemukan untuk dicetak: {image_path}")
            return False

        try:
            if sys.platform == "win32":
                # Menggunakan perintah rundll32 / ShellExecute di Windows
                os.startfile(image_path, "print")
                return True
            elif sys.platform == "darwin":  # macOS
                subprocess.run(["lp", image_path], check=True)
                return True
            else:  # Linux
                subprocess.run(["lpr", image_path], check=True)
                return True
        except Exception as e:
            logging.exception(f"Gagal mencetak foto: {e}")
            return False