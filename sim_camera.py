"""
=============================================================================
 Simulator Camera Node (sim_camera.py)
 Gia lap hoat dong cua ESP32 Camera Node bang Python
 Giao thuc: MQTT (nhan lenh) + HTTP (upload anh)
=============================================================================
 Chay: python sim_camera.py
 Chuc nang:
   - Ket noi MQTT Broker, subscribe topic iot/camera/cmd
   - Gui heartbeat moi 30 giay len iot/system/heartbeat
   - Nhan lenh CAPTURE → chup webcam (hoac dung anh mau) → HTTP POST /api/upload
   - Gui ACK sau khi xu ly lenh
   - Gui log su kien
=============================================================================
"""
import sys
import os
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import paho.mqtt.client as mqtt
import json
import time
import requests
import threading
import random
from datetime import datetime

# ========================= CAU HINH =========================
DEVICE_ID = 'CAM_NODE_01'
DEVICE_TYPE = 'camera'
MQTT_BROKER = '127.0.0.1'
MQTT_PORT = 1883
SERVER_URL = 'http://127.0.0.1:5000'
HEARTBEAT_INTERVAL = 30  # giay
SIMULATED_IP = '192.168.1.105'

# Trang thai
is_streaming = False
stream_interval = 5  # giay


# ========================= MQTT CALLBACKS =========================

def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        print(f"[MQTT] ✅ Da ket noi den Broker!")
        # Subscribe vao topic nhan lenh
        client.subscribe('iot/camera/cmd', qos=1)
        print(f"[MQTT] Subscribed: iot/camera/cmd")
        # Gui log
        send_log('INFO', 'CONNECTED', 'Camera Node da ket noi thanh cong')
    else:
        print(f"[MQTT] ❌ Ket noi that bai, ma loi: {rc}")


def on_message(client, userdata, msg):
    """Xu ly lenh nhan duoc tu server."""
    global is_streaming, stream_interval

    try:
        payload = json.loads(msg.payload.decode('utf-8'))
    except json.JSONDecodeError:
        print(f"[MQTT] ⚠️ Payload khong phai JSON")
        return

    command = payload.get('command', '')
    cmd_id = payload.get('cmd_id', '')
    params = payload.get('params', {})

    print(f"\n[CMD] 📩 Nhan lenh: {command} (ID: {cmd_id})")

    # Xu ly tung loai lenh
    if command == 'CAPTURE':
        handle_capture(cmd_id)

    elif command == 'STREAM_ON':
        stream_interval = params.get('interval_ms', 5000) / 1000
        is_streaming = True
        send_ack(cmd_id, 'OK', f'Stream ON, interval={stream_interval}s')
        print(f"[STREAM] ▶️ Bat dau stream moi {stream_interval}s")
        # Bat thread stream
        threading.Thread(target=stream_loop, daemon=True).start()

    elif command == 'STREAM_OFF':
        is_streaming = False
        send_ack(cmd_id, 'OK', 'Stream OFF')
        print(f"[STREAM] ⏹️ Da tat stream")

    elif command == 'RESTART':
        send_ack(cmd_id, 'OK', 'Restarting...')
        send_log('WARNING', 'RESTART', 'Camera Node dang khoi dong lai...')
        print(f"[SYS] 🔄 Gia lap khoi dong lai (doi 3 giay)...")
        time.sleep(3)
        send_log('INFO', 'BOOT', 'Camera Node da khoi dong lai thanh cong')
        print(f"[SYS] ✅ Da khoi dong lai!")

    elif command == 'CONFIG':
        send_ack(cmd_id, 'OK', 'Config updated')
        print(f"[CONFIG] Cau hinh moi: {params}")

    else:
        send_ack(cmd_id, 'ERROR', f'Lenh khong hop le: {command}')
        print(f"[CMD] ⚠️ Lenh khong ho tro: {command}")


# ========================= XU LY LENH =========================

def handle_capture(cmd_id):
    """Chup anh va upload len server."""
    print(f"[CAPTURE] 📸 Dang chup anh...")

    # Tao anh mau (gradient) de test
    try:
        from PIL import Image
        import io

        # Tao anh gradient don gian lam anh test
        width, height = 160, 120
        img = Image.new('RGB', (width, height))
        for y in range(height):
            for x in range(width):
                r = int(255 * x / width)
                g = int(255 * y / height)
                b = random.randint(50, 200)
                img.putpixel((x, y), (r, g, b))

        # Them text timestamp
        timestamp_text = datetime.now().strftime("%H:%M:%S")

        # Luu vao buffer JPEG
        buffer = io.BytesIO()
        img.save(buffer, format='JPEG', quality=85)
        buffer.seek(0)

        print(f"[CAPTURE] Anh da tao ({width}x{height}), dang upload...")

        # Upload len server qua HTTP POST
        files = {'image': ('camera_capture.jpg', buffer, 'image/jpeg')}
        data = {
            'device_id': DEVICE_ID,
            'resolution': f'{width}x{height}'
        }

        response = requests.post(
            f'{SERVER_URL}/api/upload',
            files=files,
            data=data,
            timeout=10
        )

        if response.status_code == 201:
            result = response.json()
            filename = result.get('data', {}).get('filename', 'unknown')
            print(f"[UPLOAD] ✅ Upload thanh cong: {filename}")
            send_ack(cmd_id, 'OK', f'Captured and uploaded: {filename}')
            send_log('INFO', 'CAPTURE_OK', f'Upload {filename} thanh cong')
        else:
            print(f"[UPLOAD] ❌ Upload that bai: HTTP {response.status_code}")
            send_ack(cmd_id, 'ERROR', f'Upload failed: HTTP {response.status_code}')
            send_log('ERROR', 'UPLOAD_FAIL', f'HTTP {response.status_code}')

    except requests.exceptions.ConnectionError:
        print(f"[UPLOAD] ❌ Khong ket noi duoc den server!")
        send_ack(cmd_id, 'ERROR', 'Server connection failed')
        send_log('ERROR', 'CONN_FAIL', 'Khong ket noi duoc den server')

    except ImportError:
        # Neu khong co Pillow, gui anh dummy
        print(f"[CAPTURE] ⚠️ Khong co Pillow, gui anh dummy...")
        dummy_data = bytes([0xFF, 0xD8] + [random.randint(0, 255) for _ in range(1000)] + [0xFF, 0xD9])
        files = {'image': ('dummy.jpg', dummy_data, 'image/jpeg')}
        data = {'device_id': DEVICE_ID, 'resolution': '160x120'}
        try:
            response = requests.post(f'{SERVER_URL}/api/upload', files=files, data=data, timeout=10)
            send_ack(cmd_id, 'OK', 'Dummy image uploaded')
        except:
            send_ack(cmd_id, 'ERROR', 'Upload failed')

    except Exception as e:
        print(f"[CAPTURE] ❌ Loi: {e}")
        send_ack(cmd_id, 'ERROR', str(e))


def stream_loop():
    """Vong lap stream: chup va upload lien tuc."""
    stream_counter = 0
    while is_streaming:
        stream_counter += 1
        print(f"\n[STREAM] 📸 Frame #{stream_counter}")
        handle_capture(f'stream_{stream_counter}')
        time.sleep(stream_interval)

    print(f"[STREAM] Stream da dung sau {stream_counter} frames")


# ========================= GUI TIN NHAN MQTT =========================

def send_heartbeat():
    """Gui heartbeat dinh ky de server biet minh con song."""
    payload = {
        'device_id': DEVICE_ID,
        'device_type': DEVICE_TYPE,
        'status': 'online',
        'ip_address': SIMULATED_IP,
        'wifi_rssi': random.randint(-60, -30),  # Gia lap RSSI
        'free_heap': random.randint(150000, 200000),
        'timestamp': datetime.now().strftime('%Y-%m-%dT%H:%M:%SZ')
    }
    mqtt_client.publish('iot/system/heartbeat', json.dumps(payload), qos=0)


def send_ack(cmd_id, status, message):
    """Gui ACK xac nhan da xu ly lenh."""
    payload = {
        'device_id': DEVICE_ID,
        'cmd_id': cmd_id,
        'status': status,
        'message': message,
        'timestamp': datetime.now().strftime('%Y-%m-%dT%H:%M:%SZ')
    }
    mqtt_client.publish('iot/camera/ack', json.dumps(payload), qos=1)
    print(f"[ACK] 📤 {cmd_id}: {status} - {message}")


def send_log(level, event, message):
    """Gui log su kien len server."""
    payload = {
        'device_id': DEVICE_ID,
        'level': level,
        'event': event,
        'message': message,
        'timestamp': datetime.now().strftime('%Y-%m-%dT%H:%M:%SZ')
    }
    mqtt_client.publish('iot/system/log', json.dumps(payload), qos=0)


def heartbeat_thread():
    """Thread gui heartbeat moi 30 giay."""
    while True:
        send_heartbeat()
        time.sleep(HEARTBEAT_INTERVAL)


# ========================= MAIN =========================

if __name__ == '__main__':
    print("=" * 55)
    print(f"  📷 Simulator Camera Node — {DEVICE_ID}")
    print(f"  MQTT: {MQTT_BROKER}:{MQTT_PORT}")
    print(f"  Server: {SERVER_URL}")
    print("=" * 55)

    # Khoi tao MQTT Client
    mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f'sim_{DEVICE_ID}')
    mqtt_client.on_connect = on_connect
    mqtt_client.on_message = on_message

    try:
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
    except ConnectionRefusedError:
        print("[MQTT] ❌ Khong the ket noi! Hay dam bao Mosquitto dang chay.")
        sys.exit(1)

    # Bat MQTT loop (non-blocking)
    mqtt_client.loop_start()

    # Doi ket noi
    time.sleep(2)

    # Gui heartbeat dau tien
    send_heartbeat()
    print(f"[HEARTBEAT] ❤️ Heartbeat dau tien da gui")

    # Bat thread heartbeat
    hb_thread = threading.Thread(target=heartbeat_thread, daemon=True)
    hb_thread.start()
    print(f"[HEARTBEAT] Timer chay moi {HEARTBEAT_INTERVAL}s")

    print(f"\n[READY] ✅ Camera Node san sang nhan lenh!")
    print(f"[READY] Hay vao Dashboard va nhan nut 'Chup Anh Ngay'")
    print(f"[READY] Nhan Ctrl+C de thoat\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print(f"\n[EXIT] Camera Node dang tat...")
        is_streaming = False
        send_log('WARNING', 'SHUTDOWN', 'Camera Node bi tat')
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
        print("[EXIT] Da ngat ket noi. Tam biet!")
