import cv2
import requests
import time
import os

# Đường dẫn URL của Web Server bạn vừa chạy (dùng IP cục bộ vì file này chạy cùng máy)
UPLOAD_URL = 'http://127.0.0.1:5000/api/upload'

def capture_and_upload():
    print("Initializing Webcam...")
    # Khởi tạo VideoCapture với index 1 (Webcam số 1)
    cap = cv2.VideoCapture(1)


    if not cap.isOpened():
        print("Error: Could not open Webcam!")
        return

    print("Camera opened. Wait 2s for auto-adjust...")
    # Chờ 1 chút để camera tự động chỉnh sáng
    time.sleep(2)

    # Đọc 1 frame từ camera
    ret, frame = cap.read()
    
    if ret:
        print("Image captured successfully! Saving temporarily...")
        # Lưu tạm ảnh ra file local
        temp_filename = "test_capture.jpg"
        cv2.imwrite(temp_filename, frame)
        
        print("Uploading image to Web Server at 127.0.0.1:5000...")
        try:
            # Gửi HTTP POST request với file multipart/form-data
            with open(temp_filename, 'rb') as f:
                # Key 'image' phải khớp với phần request.files['image'] trong file app.py của server
                files = {'image': f}
                response = requests.post(UPLOAD_URL, files=files)
            
            if response.status_code == 201:
                result = response.json()
                print(f"[OK] Upload successful! Server saved file as: {result['filename']}")
            else:
                print(f"[FAILED] Upload failed. Status Code: {response.status_code}")
                print(f"Response: {response.text}")
                
        except requests.exceptions.ConnectionError:
            print("[ERROR] Connection Error: Could not connect to Server. Ensure 'python app.py' is running!")
        except Exception as e:
            print(f"[ERROR] An error occurred: {str(e)}")
            
        finally:
            # Xóa file tạm
            if os.path.exists(temp_filename):
                os.remove(temp_filename)
                
    else:
        print("[ERROR] Could not read frame from Webcam!")

    # Giải phóng camera
    cap.release()
    print("Camera closed.")

if __name__ == "__main__":
    capture_and_upload()
    print("\n-> Open your browser to http://127.0.0.1:5000 to see the result!")
