import os
import pickle
import qrcode
import cv2
import numpy as np
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# If modifying these scopes, delete the file token.pickle.
SCOPES = ['https://www.googleapis.com/auth/drive.file']

class DriveManager:
    def __init__(self, credentials_path='credentials.json'):
        self.creds = None
        self.service = None
        self.credentials_path = credentials_path
        self.authenticated = False
        
        if os.path.exists(credentials_path):
            self.authenticate()
        else:
            print(f"Warning: {credentials_path} not found. Google Drive upload will be disabled.")

    def authenticate(self):
        try:
            if os.path.exists('token.pickle'):
                with open('token.pickle', 'rb') as token:
                    self.creds = pickle.load(token)
            
            if not self.creds or not self.creds.valid:
                if self.creds and self.creds.expired and self.creds.refresh_token:
                    self.creds.refresh(Request())
                else:
                    flow = InstalledAppFlow.from_client_secrets_file(
                        self.credentials_path, SCOPES)
                    self.creds = flow.run_local_server(port=0)
                
                with open('token.pickle', 'wb') as token:
                    pickle.dump(self.creds, token)

            self.service = build('drive', 'v3', credentials=self.creds)
            self.authenticated = True
            print("Google Drive authenticated successfully.")
        except Exception as e:
            print(f"Authentication failed: {e}")
            self.authenticated = False

    def upload_file(self, file_path, folder_id=None):
        if not self.authenticated:
            print("Not authenticated. Skipping upload.")
            return None

        try:
            file_metadata = {'name': os.path.basename(file_path)}
            if folder_id:
                file_metadata['parents'] = [folder_id]
            
            media = MediaFileUpload(file_path, mimetype='image/jpeg')
            
            file = self.service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id, webViewLink').execute()
            
            file_id = file.get('id')
            print(f"File ID: {file_id}")
            
            # Make file readable by anyone with the link
            self.service.permissions().create(
                fileId=file_id,
                body={'role': 'reader', 'type': 'anyone'}
            ).execute()
            
            return file.get('webViewLink')
            
        except Exception as e:
            print(f"Upload failed: {e}")
            return None

    def generate_qr(self, data, output_path="qr_code.png"):
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(data)
        qr.make(fit=True)

        img = qr.make_image(fill_color="black", back_color="white")
        img.save(output_path)
        return output_path

    def show_qr_window(self, qr_path, timeout=10):
        """Displays the QR code in an OpenCV window."""
        if not os.path.exists(qr_path):
            return

        img = cv2.imread(qr_path)
        if img is None:
            return

        # Add text instruction
        h, w, _ = img.shape
        # Add padding for text
        padding = 60
        new_h = h + padding
        canvas = np.ones((new_h, w, 3), dtype=np.uint8) * 255
        canvas[padding:new_h, 0:w] = img
        
        cv2.putText(canvas, "Scan to Download", (w//2 - 100, 40), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2)

        cv2.imshow("Scan QR Code", canvas)
        print(f"Displaying QR code for {timeout} seconds...")
        
        start = cv2.getTickCount()
        while True:
            if cv2.waitKey(100) == 27: # ESC
                break
            
            # Check timeout
            now = cv2.getTickCount()
            if (now - start) / cv2.getTickFrequency() > timeout:
                break
                
        cv2.destroyWindow("Scan QR Code")
