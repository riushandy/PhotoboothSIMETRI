   import cv2
   for i in range(10):
       cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
       ok, f = cap.read() if cap.isOpened() else (False, None)
       print(i, "terbuka" if cap.isOpened() else "-",
             f"frame OK {f.shape[1]}x{f.shape[0]}" if ok else "tanpa frame")
       cap.release()