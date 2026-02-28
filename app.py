"""
=============================================================================
 IoT Camera System - Web Server Core
 Phien ban: 2.0 (IoT Protocol)
 Giao thuc: HTTP REST + MQTT + WebSocket
=============================================================================
"""
import sys
import os
# Fix Unicode encoding tren Windows (cho phep print emoji va tieng Viet)
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    os.environ['PYTHONIOENCODING'] = 'utf-8'


# ========================= THƯ VIỆN =========================
from flask import Flask, request, jsonify, render_template, send_from_directory
from flask_socketio import SocketIO, emit
from flask_cors import CORS
import paho.mqtt.client as mqtt
import sqlite3
import os
import json
import time
import uuid
import threading
import subprocess
import signal
import atexit
from datetime import datetime, timezone

# ========================= CẤU HÌNH =========================
app = Flask(__name__)
app.config['SECRET_KEY'] = 'iot_camera_secret_key_2026'

# Cho phép Cross-Origin (để browser khác domain cũng truy cập được API)
CORS(app)

# Khởi tạo WebSocket server (Flask-SocketIO)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# Thư mục lưu ảnh
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Cấu hình MQTT Broker
MQTT_BROKER_HOST = '127.0.0.1'  # Mosquitto chạy trên cùng máy
MQTT_BROKER_PORT = 1883
MQTT_CLIENT_ID = 'iot_web_server'

# Cấu hình Heartbeat
HEARTBEAT_TIMEOUT = 90  # Giây — nếu thiết bị không gửi heartbeat trong 90s → offline
HEARTBEAT_CHECK_INTERVAL = 15  # Kiểm tra mỗi 15 giây

# Thời gian server khởi động (để tính uptime)
SERVER_START_TIME = time.time()

# Luu trang thai thiet bi trong bo nho (nhanh hon query DB lien tuc)
device_status_cache = {}

# Quan ly cac process simulator
sim_processes = {}
SIM_SCRIPTS = {
    'camera': 'sim_camera.py',
    'display': 'sim_display.py'
}

# ========================= DATABASE =========================

def get_db():
    """Tạo kết nối database cho mỗi request/thread."""
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row  # Trả kết quả dạng dict thay vì tuple
    return conn


def init_db():
    """Khởi tạo toàn bộ schema database theo thiết kế IoT Protocol."""
    conn = get_db()
    cursor = conn.cursor()

    # ----- Bảng 1: Lưu thông tin ảnh (nâng cấp) -----
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL UNIQUE,
            file_size INTEGER NOT NULL,
            device_id TEXT DEFAULT 'UNKNOWN',
            resolution TEXT DEFAULT '160x120',
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # ----- Bảng 2: Lưu lệnh điều khiển -----
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS commands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cmd_id TEXT NOT NULL UNIQUE,
            target_device TEXT NOT NULL,
            command TEXT NOT NULL,
            params TEXT DEFAULT '{}',
            status TEXT DEFAULT 'PENDING',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            ack_at DATETIME,
            ack_message TEXT
        )
    ''')

    # ----- Bảng 3: Quản lý thiết bị -----
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS devices (
            device_id TEXT PRIMARY KEY,
            device_type TEXT NOT NULL,
            ip_address TEXT,
            status TEXT DEFAULT 'offline',
            last_seen DATETIME,
            wifi_rssi INTEGER,
            free_heap INTEGER,
            total_uploads INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # ----- Bảng 4: Log sự kiện -----
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS event_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            level TEXT DEFAULT 'INFO',
            event TEXT NOT NULL,
            message TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # ----- Indexes cho truy vấn nhanh -----
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_images_timestamp ON images(timestamp DESC)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_commands_status ON commands(status)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON event_logs(timestamp DESC)')

    conn.commit()
    conn.close()
    print("[DB] Database schema da duoc khoi tao thanh cong!")


def load_devices_from_db():
    """Load trang thai thiet bi tu DB vao RAM cache khi server khoi dong."""
    global device_status_cache
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM devices')
        rows = cursor.fetchall()
        conn.close()

        for row in rows:
            device_status_cache[row['device_id']] = {
                'device_type': row['device_type'],
                'ip_address': row['ip_address'] or '',
                'status': 'offline',  # Mac dinh offline khi server moi khoi dong
                'last_seen': row['last_seen'] or '',
                'wifi_rssi': row['wifi_rssi'] or 0,
                'free_heap': row['free_heap'] or 0,
                'total_uploads': row['total_uploads'] or 0
            }

        if rows:
            print(f"[DB] Da load {len(rows)} thiet bi tu database vao cache")
        else:
            print(f"[DB] Chua co thiet bi nao trong database")
    except Exception as e:
        print(f"[DB] Loi load devices: {e}")


# ========================= MQTT CLIENT =========================

mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=MQTT_CLIENT_ID)


def on_mqtt_connect(client, userdata, flags, rc, properties=None):
    """Callback khi kết nối thành công đến MQTT Broker."""
    if rc == 0:
        print("[MQTT] ✅ Đã kết nối đến Mosquitto Broker!")
        # Subscribe vào các topic cần lắng nghe
        topics = [
            ('iot/camera/ack', 1),       # Camera xác nhận lệnh
            ('iot/camera/status', 1),    # Camera báo trạng thái
            ('iot/display/status', 1),   # Display báo trạng thái
            ('iot/system/heartbeat', 0), # Nhịp tim từ tất cả thiết bị
            ('iot/system/log', 0),       # Log sự kiện
            ('iot/notify/new_image', 1), # Thông báo ảnh mới (để forward lên WebSocket)
        ]
        for topic, qos in topics:
            client.subscribe(topic, qos)
            print(f"  → Subscribed: {topic} (QoS {qos})")
    else:
        print(f"[MQTT] ❌ Kết nối thất bại, mã lỗi: {rc}")


def on_mqtt_message(client, userdata, msg):
    """Callback khi nhận được message từ bất kỳ topic nào đã subscribe."""
    topic = msg.topic
    try:
        payload = json.loads(msg.payload.decode('utf-8'))
    except json.JSONDecodeError:
        print(f"[MQTT] ⚠️ Payload không phải JSON: {msg.payload}")
        return

    print(f"[MQTT] 📩 Topic: {topic} | Payload: {json.dumps(payload, ensure_ascii=False)[:200]}")

    # ----- Xử lý theo từng topic -----
    if topic == 'iot/system/heartbeat':
        handle_heartbeat(payload)

    elif topic == 'iot/camera/ack':
        handle_command_ack(payload)

    elif topic == 'iot/system/log':
        handle_device_log(payload)

    elif topic == 'iot/notify/new_image':
        # Forward thông báo ảnh mới lên WebSocket cho browser
        socketio.emit('new_image', payload.get('data', payload))

    elif topic in ('iot/camera/status', 'iot/display/status'):
        handle_device_status(payload)


def handle_heartbeat(payload):
    """Xử lý heartbeat từ thiết bị → cập nhật trạng thái online."""
    device_id = payload.get('device_id', 'UNKNOWN')
    device_type = payload.get('device_type', 'unknown')
    ip_address = payload.get('ip_address', '')
    wifi_rssi = payload.get('wifi_rssi', 0)
    free_heap = payload.get('free_heap', 0)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # Cập nhật cache trong bộ nhớ
    old_status = device_status_cache.get(device_id, {}).get('status', 'offline')
    device_status_cache[device_id] = {
        'device_id': device_id,
        'device_type': device_type,
        'status': 'online',
        'ip_address': ip_address,
        'wifi_rssi': wifi_rssi,
        'free_heap': free_heap,
        'last_seen': now
    }

    # Cập nhật vào database
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO devices (device_id, device_type, ip_address, status, last_seen, wifi_rssi, free_heap)
            VALUES (?, ?, ?, 'online', ?, ?, ?)
            ON CONFLICT(device_id) DO UPDATE SET
                ip_address = excluded.ip_address,
                status = 'online',
                last_seen = excluded.last_seen,
                wifi_rssi = excluded.wifi_rssi,
                free_heap = excluded.free_heap
        ''', (device_id, device_type, ip_address, now, wifi_rssi, free_heap))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[DB] ⚠️ Lỗi lưu heartbeat: {e}")

    # Nếu thiết bị vừa chuyển từ offline → online, thông báo
    if old_status == 'offline':
        print(f"[HEARTBEAT] 🟢 {device_id} đã Online!")

    # Gửi WebSocket cập nhật trạng thái cho browser
    socketio.emit('device_update', device_status_cache[device_id])


def handle_command_ack(payload):
    """Xử lý ACK từ Camera khi nhận xong lệnh."""
    cmd_id = payload.get('cmd_id', '')
    status = payload.get('status', 'UNKNOWN')
    message = payload.get('message', '')
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    print(f"[ACK] Lệnh {cmd_id}: {status} - {message}")

    # Cập nhật trạng thái lệnh trong database
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE commands SET status = ?, ack_at = ?, ack_message = ?
            WHERE cmd_id = ?
        ''', (status, now, message, cmd_id))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[DB] ⚠️ Lỗi cập nhật ACK: {e}")

    # Forward kết quả lên Dashboard qua WebSocket
    socketio.emit('command_result', {
        'cmd_id': cmd_id,
        'status': status,
        'message': message
    })


def handle_device_log(payload):
    """Lưu log sự kiện từ thiết bị vào database."""
    device_id = payload.get('device_id', 'UNKNOWN')
    level = payload.get('level', 'INFO')
    event = payload.get('event', '')
    message = payload.get('message', '')

    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO event_logs (device_id, level, event, message)
            VALUES (?, ?, ?, ?)
        ''', (device_id, level, event, message))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[DB] ⚠️ Lỗi lưu log: {e}")

    # Forward log lên Dashboard
    socketio.emit('log_entry', {
        'device_id': device_id,
        'level': level,
        'event': event,
        'message': message,
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    })


def handle_device_status(payload):
    """Xử lý thông báo thay đổi trạng thái từ thiết bị."""
    device_id = payload.get('device_id', 'UNKNOWN')
    status = payload.get('status', 'unknown')

    if device_id in device_status_cache:
        device_status_cache[device_id]['status'] = status

    socketio.emit('device_update', {
        'device_id': device_id,
        'status': status
    })


def setup_mqtt():
    """Khởi tạo và kết nối MQTT Client."""
    mqtt_client.on_connect = on_mqtt_connect
    mqtt_client.on_message = on_mqtt_message

    try:
        mqtt_client.connect(MQTT_BROKER_HOST, MQTT_BROKER_PORT, keepalive=60)
        # Chạy MQTT loop trong thread riêng (không block Flask)
        mqtt_client.loop_start()
        print(f"[MQTT] Đang kết nối đến {MQTT_BROKER_HOST}:{MQTT_BROKER_PORT}...")
    except ConnectionRefusedError:
        print("[MQTT] ⚠️ Không thể kết nối đến Mosquitto Broker!")
        print("       Hãy đảm bảo Mosquitto đang chạy: net start mosquitto")
    except Exception as e:
        print(f"[MQTT] ⚠️ Lỗi kết nối: {e}")


# ========================= HEARTBEAT MONITOR =========================

def heartbeat_monitor():
    """
    Background thread: kiểm tra định kỳ xem thiết bị nào đã mất tín hiệu.
    Nếu > HEARTBEAT_TIMEOUT giây không nhận heartbeat → đánh dấu offline.
    """
    while True:
        socketio.sleep(HEARTBEAT_CHECK_INTERVAL)
        now = datetime.now()

        for device_id, info in list(device_status_cache.items()):
            if info['status'] == 'online':
                try:
                    last_seen = datetime.strptime(info['last_seen'], '%Y-%m-%d %H:%M:%S')
                    elapsed = (now - last_seen).total_seconds()

                    if elapsed > HEARTBEAT_TIMEOUT:
                        # Thiết bị đã offline!
                        device_status_cache[device_id]['status'] = 'offline'
                        print(f"[HEARTBEAT] 🔴 {device_id} đã Offline! (Không có tín hiệu {elapsed:.0f}s)")

                        # Cập nhật DB
                        try:
                            conn = get_db()
                            cursor = conn.cursor()
                            cursor.execute(
                                'UPDATE devices SET status = ? WHERE device_id = ?',
                                ('offline', device_id)
                            )
                            conn.commit()
                            conn.close()
                        except Exception as e:
                            print(f"[DB] ⚠️ Lỗi cập nhật offline: {e}")

                        # Thông báo Dashboard
                        socketio.emit('device_update', {
                            'device_id': device_id,
                            'status': 'offline',
                            'last_seen': info['last_seen']
                        })
                except Exception as e:
                    print(f"[HEARTBEAT] ⚠️ Lỗi kiểm tra {device_id}: {e}")


# ========================= HTTP REST API =========================

# ----- API 1: Upload Ảnh (Camera Node → Server) -----
@app.route('/api/upload', methods=['POST'])
def upload_image():
    """
    Camera Node gửi ảnh lên server qua HTTP POST multipart/form-data.
    Sau khi lưu xong:
      1. Ghi metadata vào SQLite
      2. Phát thông báo MQTT lên topic iot/notify/new_image
      3. Emit WebSocket event 'new_image' cho Dashboard
    """
    if 'image' not in request.files:
        return jsonify({'status': 'error', 'error_code': 'NO_IMAGE', 'message': 'No image file in request'}), 400
    file = request.files['image']
    if file.filename == '':
        return jsonify({'status': 'error', 'error_code': 'NO_FILENAME', 'message': 'No selected file'}), 400

    # Lấy thông tin bổ sung từ form data
    device_id = request.form.get('device_id', 'WEB_SIMULATOR')
    resolution = request.form.get('resolution', '320x240')

    # Tạo tên file theo thời gian (tránh trùng)
    filename = datetime.now().strftime("%Y%m%d_%H%M%S") + ".jpg"
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    file.save(filepath)

    file_size = os.path.getsize(filepath)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # Lưu vào database
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO images (filename, file_size, device_id, resolution) VALUES (?, ?, ?, ?)',
            (filename, file_size, device_id, resolution)
        )
        image_id = cursor.lastrowid

        # Cập nhật số ảnh đã upload cho thiết bị
        cursor.execute('''
            UPDATE devices SET total_uploads = total_uploads + 1
            WHERE device_id = ?
        ''', (device_id,))

        conn.commit()
        conn.close()
    except Exception as e:
        return jsonify({'status': 'error', 'error_code': 'DB_ERROR', 'message': str(e)}), 500

    # Dữ liệu ảnh mới
    image_data = {
        'id': image_id,
        'filename': filename,
        'url': f'/uploads/{filename}',
        'file_size': file_size,
        'device_id': device_id,
        'resolution': resolution,
        'timestamp': now
    }

    # Phát MQTT thông báo ảnh mới (cho Display Node và các subscriber khác)
    try:
        mqtt_payload = json.dumps({
            'event': 'NEW_IMAGE',
            'data': image_data
        })
        mqtt_client.publish('iot/notify/new_image', mqtt_payload, qos=1)
        print(f"[MQTT] 📤 Đã phát thông báo ảnh mới: {filename}")
    except Exception as e:
        print(f"[MQTT] ⚠️ Không gửi được thông báo: {e}")

    # Emit WebSocket cho Dashboard (cập nhật real-time)
    socketio.emit('new_image', image_data)
    print(f"[WS] 📤 Đã emit new_image cho Dashboard")

    print(f"[UPLOAD] ✅ {filename} ({file_size} bytes) từ {device_id}")

    return jsonify({
        'status': 'success',
        'message': 'Image uploaded successfully',
        'data': image_data
    }), 201


# ----- API 2: Lấy Ảnh Mới Nhất -----
@app.route('/api/latest', methods=['GET'])
def get_latest_image():
    """Trả ảnh mới nhất. Hỗ trợ ?format=json để trả metadata."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM images ORDER BY timestamp DESC LIMIT 1')
    row = cursor.fetchone()
    conn.close()

    if not row:
        return jsonify({'status': 'error', 'error_code': 'NO_IMAGES', 'message': 'No images found'}), 404

    # Nếu yêu cầu JSON metadata
    if request.args.get('format') == 'json':
        return jsonify({
            'status': 'success',
            'data': {
                'id': row['id'],
                'filename': row['filename'],
                'url': f'/uploads/{row["filename"]}',
                'file_size': row['file_size'],
                'device_id': row['device_id'],
                'resolution': row['resolution'],
                'timestamp': row['timestamp']
            }
        })

    # Mặc định trả file ảnh binary
    return send_from_directory(UPLOAD_FOLDER, row['filename'])


# ----- API 3: Danh Sách Ảnh (Phân Trang) -----
@app.route('/api/images', methods=['GET'])
def get_images():
    """Trả danh sách ảnh có phân trang."""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    offset = (page - 1) * per_page

    conn = get_db()
    cursor = conn.cursor()

    # Đếm tổng
    cursor.execute('SELECT COUNT(*) as total FROM images')
    total = cursor.fetchone()['total']

    # Lấy danh sách theo trang
    cursor.execute(
        'SELECT * FROM images ORDER BY timestamp DESC LIMIT ? OFFSET ?',
        (per_page, offset)
    )
    rows = cursor.fetchall()
    conn.close()

    images = [{
        'id': row['id'],
        'filename': row['filename'],
        'url': f'/uploads/{row["filename"]}',
        'file_size': row['file_size'],
        'device_id': row['device_id'],
        'resolution': row['resolution'],
        'timestamp': row['timestamp']
    } for row in rows]

    return jsonify({
        'status': 'success',
        'data': {
            'images': images,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total_images': total,
                'total_pages': (total + per_page - 1) // per_page
            }
        }
    })


# ----- API 4: Chi Tiết 1 Ảnh -----
@app.route('/api/images/<int:image_id>', methods=['GET'])
def get_image_detail(image_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM images WHERE id = ?', (image_id,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return jsonify({'status': 'error', 'message': 'Image not found'}), 404

    return jsonify({
        'status': 'success',
        'data': {
            'id': row['id'],
            'filename': row['filename'],
            'url': f'/uploads/{row["filename"]}',
            'file_size': row['file_size'],
            'device_id': row['device_id'],
            'resolution': row['resolution'],
            'timestamp': row['timestamp']
        }
    })


# ----- API 5: Xóa Ảnh -----
@app.route('/api/images/<int:image_id>', methods=['DELETE'])
def delete_image(image_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT filename FROM images WHERE id = ?', (image_id,))
    row = cursor.fetchone()

    if not row:
        conn.close()
        return jsonify({'status': 'error', 'message': 'Image not found'}), 404

    # Xóa file vật lý
    filepath = os.path.join(UPLOAD_FOLDER, row['filename'])
    if os.path.exists(filepath):
        os.remove(filepath)

    # Xóa khỏi database
    cursor.execute('DELETE FROM images WHERE id = ?', (image_id,))
    conn.commit()
    conn.close()

    # Thông báo Dashboard
    socketio.emit('image_deleted', {'id': image_id})

    return jsonify({'status': 'success', 'message': f'Image {image_id} deleted'})


# ----- API 6: Trạng Thái Hệ Thống -----
@app.route('/api/status', methods=['GET'])
def get_system_status():
    """Trả thông tin tổng quan về hệ thống."""
    conn = get_db()
    cursor = conn.cursor()

    # Tổng ảnh
    cursor.execute('SELECT COUNT(*) as total FROM images')
    total_images = cursor.fetchone()['total']

    # Dung lượng ổ đĩa đã dùng
    total_size = 0
    for f in os.listdir(UPLOAD_FOLDER):
        fp = os.path.join(UPLOAD_FOLDER, f)
        if os.path.isfile(fp):
            total_size += os.path.getsize(fp)

    # Danh sách thiết bị
    cursor.execute('SELECT * FROM devices')
    devices_rows = cursor.fetchall()
    conn.close()

    devices = {}
    for row in devices_rows:
        devices[row['device_id']] = {
            'device_type': row['device_type'],
            'status': device_status_cache.get(row['device_id'], {}).get('status', row['status']),
            'ip_address': row['ip_address'],
            'last_seen': row['last_seen'],
            'wifi_rssi': row['wifi_rssi'],
            'total_uploads': row['total_uploads']
        }

    uptime = int(time.time() - SERVER_START_TIME)

    return jsonify({
        'status': 'success',
        'data': {
            'server': {
                'uptime_seconds': uptime,
                'version': '2.0.0',
                'storage_used_mb': round(total_size / (1024 * 1024), 2),
                'total_images': total_images
            },
            'devices': devices
        }
    })


# ----- Phục vụ file ảnh tĩnh -----
@app.route('/uploads/<filename>')
def serve_upload(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)


# ----- Giao diện Web Dashboard -----
@app.route('/')
def index():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM images ORDER BY timestamp DESC')
    images = cursor.fetchall()
    conn.close()
    return render_template('index.html', images=images)


# ========================= SIMULATOR MANAGEMENT =========================

def cleanup_sim_processes():
    """Tat tat ca simulator khi server tat."""
    for name, proc in sim_processes.items():
        if proc and proc.poll() is None:
            proc.terminate()
            print(f"[SIM] Da tat {name}")

atexit.register(cleanup_sim_processes)


@app.route('/api/sim/start', methods=['POST'])
def start_simulator():
    """Khoi dong simulator process."""
    data = request.get_json() or {}
    sim_type = data.get('type', '')  # 'camera' hoac 'display'

    if sim_type not in SIM_SCRIPTS:
        return jsonify({'status': 'error', 'message': f'Loai sim khong hop le: {sim_type}'}), 400

    script = SIM_SCRIPTS[sim_type]
    script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), script)

    # Kiem tra da chay chua
    if sim_type in sim_processes and sim_processes[sim_type] is not None:
        if sim_processes[sim_type].poll() is None:  # Van dang chay
            return jsonify({'status': 'error', 'message': f'{sim_type} simulator da dang chay'}), 409

    # Khoi dong process moi
    try:
        proc = subprocess.Popen(
            [sys.executable, script_path],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        )
        sim_processes[sim_type] = proc
        print(f"[SIM] ✅ Da khoi dong {sim_type} simulator (PID: {proc.pid})")
        return jsonify({'status': 'success', 'message': f'{sim_type} simulator da khoi dong', 'pid': proc.pid})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/sim/stop', methods=['POST'])
def stop_simulator():
    """Tat simulator process."""
    data = request.get_json() or {}
    sim_type = data.get('type', '')

    if sim_type not in SIM_SCRIPTS:
        return jsonify({'status': 'error', 'message': f'Loai sim khong hop le: {sim_type}'}), 400

    if sim_type not in sim_processes or sim_processes[sim_type] is None:
        return jsonify({'status': 'error', 'message': f'{sim_type} simulator chua chay'}), 404

    proc = sim_processes[sim_type]
    if proc.poll() is not None:  # Da tat roi
        sim_processes[sim_type] = None
        return jsonify({'status': 'error', 'message': f'{sim_type} simulator da tat'}), 404

    try:
        proc.terminate()
        proc.wait(timeout=5)
        sim_processes[sim_type] = None
        print(f"[SIM] ⏹️ Da tat {sim_type} simulator")
        return jsonify({'status': 'success', 'message': f'{sim_type} simulator da tat'})
    except Exception as e:
        proc.kill()
        sim_processes[sim_type] = None
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/sim/status', methods=['GET'])
def get_sim_status():
    """Tra ve trang thai cac simulator."""
    statuses = {}
    for sim_type in SIM_SCRIPTS:
        proc = sim_processes.get(sim_type)
        if proc and proc.poll() is None:
            statuses[sim_type] = {'running': True, 'pid': proc.pid}
        else:
            statuses[sim_type] = {'running': False, 'pid': None}
    return jsonify({'status': 'success', 'data': statuses})


# ========================= WEBSOCKET EVENTS =========================

@socketio.on('connect')
def handle_ws_connect():
    """Khi browser mới kết nối WebSocket → gửi trạng thái hiện tại."""
    print(f"[WS] 🟢 Browser đã kết nối!")
    # Gửi trạng thái tất cả thiết bị hiện tại
    emit('system_status', {
        'devices': device_status_cache,
        'server_uptime': int(time.time() - SERVER_START_TIME)
    })


@socketio.on('disconnect')
def handle_ws_disconnect():
    print(f"[WS] 🔴 Browser đã ngắt kết nối.")


@socketio.on('send_command')
def handle_send_command(data):
    """
    Browser gửi lệnh điều khiển → Server tạo cmd_id → publish MQTT → Camera nhận.
    data = { "target": "camera", "command": "CAPTURE", "params": {} }
    """
    target = data.get('target', 'camera')
    command = data.get('command', '')
    params = data.get('params', {})

    # Tạo cmd_id duy nhất
    cmd_id = f"cmd_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{str(uuid.uuid4())[:4]}"

    # Lưu lệnh vào database
    target_device = f"CAM_NODE_01" if target == 'camera' else f"DISP_NODE_01"
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO commands (cmd_id, target_device, command, params, status)
            VALUES (?, ?, ?, ?, 'PENDING')
        ''', (cmd_id, target_device, command, json.dumps(params)))
        conn.commit()
        conn.close()
    except Exception as e:
        emit('command_result', {'cmd_id': cmd_id, 'status': 'ERROR', 'message': str(e)})
        return

    # Publish lệnh lên MQTT
    mqtt_topic = f"iot/{target}/cmd"
    mqtt_payload = json.dumps({
        'cmd_id': cmd_id,
        'command': command,
        'timestamp': datetime.now().strftime('%Y-%m-%dT%H:%M:%SZ'),
        'params': params
    })

    try:
        mqtt_client.publish(mqtt_topic, mqtt_payload, qos=1)
        print(f"[MQTT] 📤 Đã gửi lệnh {command} (ID: {cmd_id}) lên {mqtt_topic}")
    except Exception as e:
        print(f"[MQTT] ⚠️ Lỗi gửi lệnh: {e}")

    # Phản hồi ngay cho browser biết lệnh đã được gửi đi
    emit('command_sent', {
        'cmd_id': cmd_id,
        'command': command,
        'target': target,
        'status': 'PENDING'
    })


@socketio.on('request_status')
def handle_request_status():
    """Browser yêu cầu cập nhật trạng thái hệ thống."""
    emit('system_status', {
        'devices': device_status_cache,
        'server_uptime': int(time.time() - SERVER_START_TIME)
    })


# ========================= KHỞI ĐỘNG SERVER =========================

if __name__ == '__main__':
    print("=" * 60)
    print("  🚀 IoT Camera System — Web Server v2.0")
    print("  📡 Giao thức: HTTP REST + MQTT + WebSocket")
    print("=" * 60)

    # Buoc 1: Khoi tao Database
    init_db()

    # Buoc 1.5: Load trang thai thiet bi tu DB vao cache
    load_devices_from_db()

    # Bước 2: Kết nối MQTT Broker
    setup_mqtt()

    # Bước 3: Khởi động Heartbeat Monitor (background thread)
    socketio.start_background_task(heartbeat_monitor)
    print(f"[HEARTBEAT] Monitor đã chạy (timeout: {HEARTBEAT_TIMEOUT}s)")

    # Bước 4: Khởi động Flask + SocketIO Server
    print(f"\n[SERVER] 🌐 Dashboard: http://127.0.0.1:5000")
    print(f"[SERVER] 📡 MQTT Broker: {MQTT_BROKER_HOST}:{MQTT_BROKER_PORT}")
    print(f"[SERVER] Đang chờ kết nối từ Camera Node và Display Node...\n")

    socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)
