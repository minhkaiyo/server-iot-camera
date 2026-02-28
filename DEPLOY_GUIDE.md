# 🚀 Hướng Dẫn Deploy IoT Camera System lên Render.com

> **Tại sao Render mà không phải Vercel?**
> - Render hỗ trợ chạy server Python 24/7 (Web Service), giữ được kết nối MQTT + WebSocket
> - Vercel là Serverless → MQTT bị ngắt, WebSocket không hoạt động, SQLite mất dữ liệu
> - Render có **Free Plan** (750 giờ/tháng — đủ dùng cho demo)

---

## Bước 0 — Chuẩn bị trên máy tính

### 0.1. Cài Git (nếu chưa có)
Kiểm tra bằng lệnh:
```powershell
git --version
```
Nếu chưa có, tải tại: https://git-scm.com/download/win

### 0.2. Tạo tài khoản GitHub (nếu chưa có)
- Vào https://github.com → Sign up
- Xác nhận email

### 0.3. Tạo tài khoản Render
- Vào https://render.com → Sign up
- **Khuyên dùng:** Đăng ký bằng tài khoản GitHub (nhanh nhất, tự liên kết luôn)

---

## Bước 1 — Đẩy code lên GitHub

### 1.1. Mở PowerShell tại thư mục dự án
```powershell
cd "c:\Users\Minh\OneDrive\Mon_Chuyen_Nganh\Project1\IoT_Camera_System\Web_Server_Node"
```

### 1.2. Tạo file `.gitignore` (để không đẩy file rác lên GitHub)
Tạo file `.gitignore` với nội dung:
```
__pycache__/
*.pyc
*.pyo
database.db
uploads/
downloaded_images/
.env
*.log
```

### 1.3. Khởi tạo Git repo
```powershell
git init
git add .
git commit -m "Initial commit: IoT Camera System v2.0"
```

### 1.4. Tạo Repository trên GitHub
1. Vào https://github.com/new
2. **Repository name:** `iot-camera-system` (hoặc tên bạn thích)
3. **Visibility:** Public (hoặc Private — Render đều hỗ trợ)
4. **KHÔNG tick** "Add a README file" (vì bạn đã có code)
5. Nhấn **Create repository**

### 1.5. Kết nối và đẩy code lên
GitHub sẽ hiện hướng dẫn cho bạn, chạy lệnh tương tự:
```powershell
git remote add origin https://github.com/YOUR_USERNAME/iot-camera-system.git
git branch -M main
git push -u origin main
```
> ⚠️ Thay `YOUR_USERNAME` bằng username GitHub của bạn.

---

## Bước 2 — Tạo Web Service trên Render

### 2.1. Đăng nhập Render
- Vào https://dashboard.render.com
- Đăng nhập bằng tài khoản GitHub

### 2.2. Tạo Web Service mới
1. Nhấn **New +** → chọn **Web Service**
2. Chọn **Build and deploy from a Git repository** → Next
3. Tìm repo `iot-camera-system` → nhấn **Connect**

### 2.3. Cấu hình Service
Điền các thông tin:

| Mục | Giá trị |
|:---|:---|
| **Name** | `iot-camera-system` |
| **Region** | Singapore (gần Việt Nam nhất) |
| **Branch** | `main` |
| **Runtime** | `Python 3` |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `python app.py` |
| **Instance Type** | **Free** |

### 2.4. Nhấn **Create Web Service**

Render sẽ:
1. Clone code từ GitHub
2. Cài thư viện từ `requirements.txt`
3. Chạy `python app.py`
4. Cấp cho bạn URL dạng: `https://iot-camera-system.onrender.com`

> ⏱️ Lần deploy đầu tiên mất khoảng 2-5 phút.

---

## Bước 3 — Xử lý MQTT Broker trên Cloud

### ⚠️ Vấn đề quan trọng
Trên máy tính bạn, Mosquitto chạy tại `127.0.0.1:1883`. Nhưng trên Render, không có Mosquitto.

### Giải pháp: Dùng MQTT Broker miễn phí trên Cloud

**Lựa chọn 1 — HiveMQ Cloud (Khuyên dùng):**
1. Vào https://www.hivemq.com/mqtt-cloud-broker/ → Sign up
2. Tạo Free cluster → nhận thông tin:
   - **Host:** `xxxxx.hivemq.cloud`
   - **Port:** `8883` (TLS)
   - **Username & Password:** bạn tự đặt
3. Cập nhật trong `app.py`:
   ```python
   MQTT_BROKER_HOST = 'xxxxx.hivemq.cloud'
   MQTT_BROKER_PORT = 8883
   # Thêm TLS + auth
   mqtt_client.tls_set()
   mqtt_client.username_pw_set('your_username', 'your_password')
   ```

**Lựa chọn 2 — EMQX Cloud:**
1. Vào https://www.emqx.com/en/cloud → Sign up
2. Tạo Serverless deployment (miễn phí)
3. Cấu hình tương tự

**Lựa chọn 3 — test.mosquitto.org (KHÔNG khuyên cho production):**
- Host: `test.mosquitto.org`, Port: `1883`
- Không cần đăng ký, nhưng không bảo mật và không ổn định

---

## Bước 4 — Cấu hình biến môi trường trên Render

Thay vì hardcode MQTT credentials trong code, dùng **Environment Variables** trên Render:

### 4.1. Trên Dashboard Render → vào Service → tab **Environment**

Thêm các biến:

| Key | Value |
|:---|:---|
| `MQTT_BROKER_HOST` | `xxxxx.hivemq.cloud` |
| `MQTT_BROKER_PORT` | `8883` |
| `MQTT_USERNAME` | `your_username` |
| `MQTT_PASSWORD` | `your_password` |
| `PYTHONIOENCODING` | `utf-8` |

### 4.2. Trong code `app.py`, đọc từ biến môi trường:
```python
import os
MQTT_BROKER_HOST = os.environ.get('MQTT_BROKER_HOST', '127.0.0.1')
MQTT_BROKER_PORT = int(os.environ.get('MQTT_BROKER_PORT', 1883))
```

> 💡 Cách này cho phép code chạy được CẢ trên máy local (dùng giá trị mặc định) LẪN trên Render (dùng biến môi trường).

---

## Bước 5 — Kiểm Tra

### 5.1. Mở URL Render
- Vào `https://iot-camera-system.onrender.com`
- Dashboard phải hiện ra giống hệt trên máy local

### 5.2. Test nhanh
- Mở Dashboard → các thiết bị hiện Offline (chưa có sim)
- Upload ảnh qua Webcam Simulator → ảnh xuất hiện trong gallery
- Ảnh cũ vẫn còn sau khi refresh

---

## Lưu ý quan trọng ⚠️

### Free Plan của Render
- **Ưu điểm:** Miễn phí, đủ dùng cho demo
- **Nhược điểm:**
  - Server sẽ **"ngủ" sau 15 phút không có request** → lần truy cập đầu tiên sẽ chậm ~30 giây (cold start)
  - Giới hạn 750 giờ/tháng
  - Disk không persistent (SQLite + uploads sẽ bị mất khi server restart)

### Giải pháp cho Disk (nếu cần dữ liệu bền vững)
- **Render Persistent Disk:** $0.25/GB/tháng (rất rẻ) → gắn vào `/data` → lưu DB + uploads vào đó
- Hoặc chuyển sang dùng **PostgreSQL** (Render cung cấp miễn phí) + **Cloudinary** (lưu ảnh miễn phí)

> 💡 **Cho demo trước thầy:** Free plan + SQLite là đủ. Chỉ cần chạy `python sim_camera.py` trên máy tính → ảnh sẽ upload lên server trên Render → thầy mở link xem real-time!

---

## Tóm Tắt Luồng Deploy

```
Code trên máy tính
    ↓ git push
GitHub Repository
    ↓ auto-deploy
Render Web Service ← MQTT Cloud Broker (HiveMQ)
    ↓
https://iot-camera-system.onrender.com
    ↑
ESP32 / Simulator (gửi MQTT + HTTP POST)
```

---

## Cần hỗ trợ thêm?

Khi bạn sẵn sàng deploy, hãy nói với mình để mình:
1. Sửa code `app.py` để đọc config từ biến môi trường
2. Tạo file `.gitignore`
3. Hướng dẫn từng bước trên Render Dashboard

**⏳ Dự kiến thời gian:** ~15-20 phút từ lúc bắt đầu đến khi có link online.
