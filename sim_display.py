"""
=============================================================================
 Simulator Display Node (sim_display.py)
 Gia lap hoat dong cua ESP32 Display Node bang Python
 Giao thuc: MQTT (nhan thong bao) + HTTP (tai anh)
=============================================================================
 Chay:  python sim_display.py
 Chuc nang:
   - Ket noi MQTT Broker, subscribe topic iot/notify/new_image
   - Khi nhan thong bao anh moi → tai anh tu server → hien thi bang Pillow/OpenCV
   - Gui heartbeat moi 30 giay
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
from datetime import datetime

# ========================= CAU HINH =========================
DEVICE_ID = 'DISP_NODE_01'
DEVICE_TYPE = 'display'
MQTT_BROKER = '127.0.0.1'
MQTT_PORT = 1883
SERVER_URL = 'http://127.0.0.1:5000'
HEARTBEAT_INTERVAL = 30
SIMULATED_IP = '192.168.1.106'

# Dem so anh da nhan
image_count = 0


# ========================= MQTT CALLBACKS =========================

def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        print(f"[MQTT] ✅ Da ket noi den Broker!")
        # Subscribe cac topic can lang nghe
        client.subscribe('iot/notify/new_image', qos=1)
        client.subscribe('iot/display/cmd', qos=1)
        client.subscribe('iot/system/heartbeat', qos=0)
        print(f"[MQTT] Subscribed: iot/notify/new_image")
        print(f"[MQTT] Subscribed: iot/display/cmd")
        # Gui log
        send_log('INFO', 'CONNECTED', 'Display Node da ket noi')
    else:
        print(f"[MQTT] ❌ Ket noi that bai, ma loi: {rc}")


def on_message(client, userdata, msg):
    """Xu ly message nhan duoc."""
    global image_count
    topic = msg.topic

    try:
        payload = json.loads(msg.payload.decode('utf-8'))
    except json.JSONDecodeError:
        print(f"[MQTT] ⚠️ Payload khong phai JSON")
        return

    if topic == 'iot/notify/new_image':
        handle_new_image(payload)

    elif topic == 'iot/display/cmd':
        handle_command(payload)

    elif topic == 'iot/system/heartbeat':
        # Chi log heartbeat cua thiet bi khac
        src_device = payload.get('device_id', '')
        if src_device != DEVICE_ID:
            status = payload.get('status', 'unknown')
            print(f"[HEARTBEAT] 💓 {src_device}: {status}")


def handle_new_image(payload):
    """Xu ly khi co anh moi."""
    global image_count
    image_count += 1

    data = payload.get('data', payload)
    filename = data.get('filename', 'unknown')
    url = data.get('url', '')
    device_id = data.get('device_id', 'unknown')
    timestamp = data.get('timestamp', '')

    print(f"\n{'='*50}")
    print(f"[NEW IMAGE] 🖼️ Anh moi #{image_count}")
    print(f"  File    : {filename}")
    print(f"  Tu      : {device_id}")
    print(f"  Thoi gian: {timestamp}")

    if url:
        download_url = f"{SERVER_URL}{url}"
        print(f"  URL     : {download_url}")

        # Tai anh tu server
        try:
            response = requests.get(download_url, timeout=10)
            if response.status_code == 200:
                # Luu anh vao thu muc tam
                download_dir = 'downloaded_images'
                os.makedirs(download_dir, exist_ok=True)
                save_path = os.path.join(download_dir, filename)
                with open(save_path, 'wb') as f:
                    f.write(response.content)

                file_size = len(response.content)
                print(f"  Kich thuoc: {file_size} bytes")
                print(f"  Da luu  : {save_path}")

                # Thu hien thi anh bang Pillow
                try:
                    from PIL import Image
                    img = Image.open(save_path)
                    print(f"  Resolution: {img.size[0]}x{img.size[1]}")
                    print(f"  Format    : {img.format}")

                    # Hien thi anh (se mo cua so anh tren PC)
                    # Bo comment dong duoi neu muon xem anh:
                    # img.show()

                    print(f"  ✅ Anh hop le, san sang hien thi tren TFT!")
                except ImportError:
                    print(f"  ⚠️ Khong co Pillow, bo qua hien thi")
                except Exception as e:
                    print(f"  ⚠️ Loi doc anh: {e}")

            else:
                print(f"  ❌ Tai anh that bai: HTTP {response.status_code}")

        except requests.exceptions.ConnectionError:
            print(f"  ❌ Khong ket noi duoc den server!")
        except Exception as e:
            print(f"  ❌ Loi: {e}")

    print(f"{'='*50}\n")


def handle_command(payload):
    """Xu ly lenh gui den Display Node."""
    command = payload.get('command', '')
    cmd_id = payload.get('cmd_id', '')

    print(f"[CMD] 📩 Nhan lenh: {command} (ID: {cmd_id})")

    if command == 'REFRESH':
        print(f"[CMD] Lam moi hien thi...")
        download_latest_image()

    elif command == 'RESTART':
        print(f"[CMD] Khoi dong lai Display Node...")
        send_log('WARNING', 'RESTART', 'Display Node dang khoi dong lai')


def download_latest_image():
    """Tai anh moi nhat tu server."""
    try:
        response = requests.get(f'{SERVER_URL}/api/latest?format=json', timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get('status') == 'success':
                img_data = data['data']
                print(f"[LATEST] Anh moi nhat: {img_data['filename']}")
                handle_new_image({'data': img_data})
        else:
            print(f"[LATEST] Khong co anh nao tren server")
    except Exception as e:
        print(f"[LATEST] Loi: {e}")


# ========================= GUI TIN NHAN MQTT =========================

def send_heartbeat():
    """Gui heartbeat dinh ky."""
    import random
    payload = {
        'device_id': DEVICE_ID,
        'device_type': DEVICE_TYPE,
        'status': 'online',
        'ip_address': SIMULATED_IP,
        'wifi_rssi': random.randint(-55, -25),
        'free_heap': random.randint(160000, 210000),
        'timestamp': datetime.now().strftime('%Y-%m-%dT%H:%M:%SZ')
    }
    mqtt_client.publish('iot/system/heartbeat', json.dumps(payload), qos=0)


def send_log(level, event, message):
    """Gui log su kien."""
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
    print(f"  📺 Simulator Display Node — {DEVICE_ID}")
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

    # Bat MQTT loop
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

    # Tai anh moi nhat khi khoi dong
    print(f"\n[INIT] Dang tai anh moi nhat tu server...")
    download_latest_image()

    print(f"\n[READY] ✅ Display Node san sang!")
    print(f"[READY] Dang cho thong bao anh moi tu MQTT...")
    print(f"[READY] Nhan Ctrl+C de thoat\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print(f"\n[EXIT] Display Node dang tat...")
        send_log('WARNING', 'SHUTDOWN', 'Display Node bi tat')
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
        print(f"[EXIT] Da ngat ket noi. Tam biet!")
        print(f"[STATS] Tong so anh da nhan: {image_count}")
