import socket
import threading
import pickle
import numpy as np
import cv2
import time
import os
import json
import pyaudio
import base64
from PyQt5.QtCore import QObject, QThread, pyqtSignal, QTimer

# Try to import mss
try:
    import mss
    MSS_AVAILABLE = True
except ImportError:
    MSS_AVAILABLE = False
    print("⚠️ mss not available. Screen sharing will not work.")

# (Configurations are the same)
VIDEO_WIDTH = 640
VIDEO_HEIGHT = 360
JPEG_QUALITY = 75
UDP_BUFFER_SIZE = 65536
TCP_BUFFER_SIZE = 4096
AUDIO_FORMAT = pyaudio.paInt16
AUDIO_CHANNELS = 1
AUDIO_RATE = 44100
AUDIO_CHUNK = 2048

# UDP packet size limit
MAX_UDP_PACKET = 60000  # Safe size for most networks


class NetworkManager(QObject):
    """Handles all networking (TCP Chat/File + UDP Video/Audio) in background threads."""
    
    # --- GUI Signals ---
    connected = pyqtSignal()
    disconnected = pyqtSignal(str)
    error = pyqtSignal(str)
    
    # Video Signals
    local_frame_ready = pyqtSignal(object)
    video_frame_received = pyqtSignal(str, object, str) # username, frame, frame_type
    
    # Chat/File Signals
    chat_message_received = pyqtSignal(str, str, bool) # from, msg, is_private
    user_list_updated = pyqtSignal(list)
    file_offer_received = pyqtSignal(dict)
    file_send_progress = pyqtSignal(str, int, int)
    file_recv_progress = pyqtSignal(str, int, int)
    file_recv_complete = pyqtSignal(str)
    file_list_received = pyqtSignal(list)
    
    def __init__(self):
        super().__init__()
        
        self.video_socket = None
        self.chat_socket = None
        self.chat_fileobj = None
        self.cap = None
        self.audio_socket = None
        self.username = "User"
        self.video_server_addr = None
        self.chat_server_addr = None
        self.audio_server_addr = None
        
        self.video_sender_thread = None
        self.video_receiver_thread = None
        self.chat_listener_thread = None
        self.file_sender_thread = None
        self.audio_sender_thread = None
        self.audio_receiver_thread = None
        
        # File receiver state
        self.file_save_handle = None
        self.file_receiving_size = 0
        self.file_received_bytes = 0
        self.current_download_filename = "download"

        self.audio_instance = None
        self.audio_stream_in = None
        self.audio_stream_out = None
        
        self._is_running = False
        self._video_enabled = True
        self._audio_enabled = True

        # --- Screen Share Properties ---
        self._screen_share_enabled = False
        self.latest_screen_frame = None
        self.screen_frame_lock = threading.Lock()
        self.screen_capture_timer = None
    
    def connect_to_servers(self, username, host_ip, video_port, chat_port):
        self.username = username
        self.video_server_addr = (host_ip, video_port)
        self.chat_server_addr = (host_ip, chat_port)
        self.audio_server_addr = (host_ip, video_port + 1)
        self._is_running = True
        
        try:
            self.chat_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.chat_socket.connect(self.chat_server_addr)
            
            prompt = self.chat_socket.recv(1024).decode()
            self.chat_socket.sendall(self.username.encode())
            reply = self.chat_socket.recv(1024).decode()
            
            if "accepted" not in reply:
                raise Exception(reply)
                
            self.chat_fileobj = self.chat_socket.makefile("rb")
            print("Connected to Chat/File Server")
            
            self.chat_listener_thread = threading.Thread(target=self.run_chat_listener, daemon=True)
            self.chat_listener_thread.start()
            
        except Exception as e:
            self.error.emit(f"Chat/File Server Error: {e}")
            self._is_running = False
            return

        try:
            self.video_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.video_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1<<20)
            self.video_socket.bind(('', 0))   # bind to an ephemeral local port so we can receive
            print("Video socket bound to", self.video_socket.getsockname())
            self.video_socket.settimeout(5.0)
            print("Video Socket Created")
            
            self.video_sender_thread = threading.Thread(target=self.run_video_sender, daemon=True)
            self.video_receiver_thread = threading.Thread(target=self.run_video_receiver, daemon=True)
            self.video_sender_thread.start()
            self.video_receiver_thread.start()
            
        except Exception as e:
            self.error.emit(f"Video Server Error: {e}")
            self.stop_all()
            return
            
        try:
            self.audio_instance = pyaudio.PyAudio()
            self.audio_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.audio_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1<<20)
            self.audio_socket.bind(('', 0))
            print("Audio socket bound to", self.audio_socket.getsockname())
            self.audio_socket.settimeout(5.0)
            print("Audio Socket Created")
            
            self.audio_sender_thread = threading.Thread(target=self.run_audio_sender, daemon=True)
            self.audio_receiver_thread = threading.Thread(target=self.run_audio_receiver, daemon=True)
            self.audio_sender_thread.start()
            self.audio_receiver_thread.start()
            
        except Exception as e:
            self.error.emit(f"Audio Server Error: {e}")
            self.stop_all()
            return
            
        self.connected.emit()

    def disconnect(self):
        self.stop_all()

    def stop_all(self):
        self._is_running = False
        
        # Stop screen capture timer
        if self.screen_capture_timer:
            self.screen_capture_timer.stop()
            self.screen_capture_timer = None
        
        if self.chat_socket:
            try: self.chat_socket.close()
            except: pass
        if self.video_socket:
            try: self.video_socket.close()
            except: pass
        if self.audio_socket:
            try: self.audio_socket.close()
            except: pass
            
        if self.cap:
            try: self.cap.release()
            except: pass
            self.cap = None
            
        if self.audio_stream_in:
            try:
                self.audio_stream_in.stop_stream()
                self.audio_stream_in.close()
            except: pass
            self.audio_stream_in = None
            
        if self.audio_stream_out:
            try:
                self.audio_stream_out.stop_stream()
                self.audio_stream_out.close()
            except: pass
            self.audio_stream_out = None

        if self.audio_instance:
            try: self.audio_instance.terminate()
            except: pass
            self.audio_instance = None
            
        if self.file_save_handle:
            try: self.file_save_handle.close()
            except: pass
            self.file_save_handle = None

        print("Disconnected from servers.")

    def set_video_enabled(self, enabled):
        self._video_enabled = enabled

    def set_audio_enabled(self, enabled):
        self._audio_enabled = enabled

    def set_screen_share_enabled(self, enabled):
        """Tells the network manager to start/stop screen sharing."""
        if not MSS_AVAILABLE and enabled:
            self.error.emit("Screen sharing requires mss library. Install with: pip install mss")
            return
            
        self._screen_share_enabled = enabled
        
        if enabled:
            self.set_video_enabled(False)  # Force camera off
            print("Screen sharing enabled - starting capture timer...")
            
            # Start a timer to capture screens in the main thread
            if self.screen_capture_timer is None:
                self.screen_capture_timer = QTimer()
                self.screen_capture_timer.timeout.connect(self._capture_screen)
                self.screen_capture_timer.start(33)  # ~30 FPS
                print("Screen capture timer started")
        else:
            # Stop the timer
            if self.screen_capture_timer:
                self.screen_capture_timer.stop()
                self.screen_capture_timer = None
                print("Screen capture timer stopped")
            
            # Clear the buffer
            with self.screen_frame_lock:
                self.latest_screen_frame = None

    def _capture_screen(self):
        """Captures screen in the main thread (called by timer)."""
        if not self._screen_share_enabled:
            return
        
        try:
            # Use mss with context manager (thread-safe approach)
            with mss.mss() as sct:
                # Capture primary monitor
                monitor = sct.monitors[1]
                img_rgba = np.array(sct.grab(monitor))
                img_bgr = cv2.cvtColor(img_rgba, cv2.COLOR_BGRA2BGR)
                
                # Resize to streaming size
                frame_resized = cv2.resize(img_bgr, (VIDEO_WIDTH, VIDEO_HEIGHT))
                
                # Store in buffer (thread-safe)
                with self.screen_frame_lock:
                    self.latest_screen_frame = frame_resized.copy()
                    
        except Exception as e:
            print(f"Screen capture error: {e}")
            with self.screen_frame_lock:
                self.latest_screen_frame = None
            # Disable screen share on error
            self._screen_share_enabled = False
            self.error.emit(f"Screen capture failed: {e}")

    def send_json_line(self, obj):
        """Helper to send JSON to the chat/file server."""
        if not self.chat_socket or not self._is_running:
            return
        try:
            s = json.dumps(obj) + "\n"
            self.chat_socket.sendall(s.encode())
        except Exception as e:
            print(f"Error sending JSON: {e}")
            self.disconnected.emit(f"Connection error: {e}")
            self.stop_all()

    def send_chat_message(self, target, message):
        obj = {"type": "chat", "to": target, "msg": message}
        self.send_json_line(obj)

    def request_user_list(self):
        obj = {"type": "command", "cmd": "/users"}
        self.send_json_line(obj)

    def request_file_list(self):
        """Sends a request to the server to get the full list of available files."""
        msg = {
            "type": "REQUEST_FILE_LIST",
            "from": self.username
        }
        self.send_json_line(msg)

    def upload_file(self, path):
        if not os.path.exists(path):
            self.error.emit(f"File not found: {path}")
            return
        
        filesize = os.path.getsize(path)
        filename = os.path.basename(path)
        
        meta = {"type": "upload_start", "filename": filename, "size": filesize}
        self.send_json_line(meta)
        
        self.file_sender_thread = threading.Thread(target=self.run_file_sender, 
                                                   args=(path, filesize, filename), daemon=True)
        self.file_sender_thread.start()

    def request_file_download(self, offer):
        try:
            filename = offer.get("filename")
            filesize = offer.get("size")
            
            save_dir = "Downloads"
            os.makedirs(save_dir, exist_ok=True)
            self.save_path = os.path.join(save_dir, filename)
            
            print(f"[Network] Requesting to download {filename}. Saving to {self.save_path}")

            self.file_save_handle = open(self.save_path, "wb")
            self.file_receiving_size = filesize
            self.file_received_bytes = 0
            self.current_download_filename = filename
            
            request_msg = {"type": "download_request", "filename": filename}
            self.send_json_line(request_msg)
            
        except Exception as e:
            self.error.emit(f"File download error: {e}")
            if self.file_save_handle:
                self.file_save_handle.close()
            self.file_save_handle = None

    # --- Background Threads ---
    
    def run_chat_listener(self):
        """Listens for all TCP packets (chat, file offers, etc.)"""
        while self._is_running:
            try:
                line = self.chat_fileobj.readline()
                if not line:
                    raise ConnectionError("Server disconnected")
                
                obj = json.loads(line.decode())
                obj_type = obj.get("type")
                
                if obj_type == "error":
                    self.error.emit(obj.get("msg", "Unknown server error"))
                elif obj_type == "user_list":
                    self.user_list_updated.emit(obj.get("users", []))
                elif obj_type == "chat_message":
                    self.chat_message_received.emit(obj.get("from"), obj.get("msg"), obj.get("private", False))
                
                elif obj_type == "new_file_available":
                    self.file_offer_received.emit(obj)
                
                elif obj_type == "FILE_LIST_UPDATE":
                    file_list = obj.get("files", [])
                    self.file_list_received.emit(file_list)
                
                elif obj_type == "file_chunk":
                    self.handle_file_chunk(obj)
                elif obj_type == "file_end":
                    self.handle_file_end(obj)
                
            except Exception as e:
                if self._is_running:
                    self.disconnected.emit(f"Chat connection lost: {e}")
                    self.stop_all()
                break
        print("Chat listener stopped.")

    def handle_file_chunk(self, obj):
        if self.file_save_handle:
            try:
                data = base64.b64decode(obj.get("data"))
                self.file_save_handle.write(data)
                
                self.file_received_bytes += len(data)
                self.file_recv_progress.emit(
                    self.current_download_filename,
                    self.file_received_bytes,
                    self.file_receiving_size
                )
            except Exception as e:
                self.error.emit(f"File chunk error: {e}")
                self.file_save_handle.close()
                self.file_save_handle = None

    def handle_file_end(self, obj):
        if self.file_save_handle:
            filename = obj.get("filename", self.current_download_filename)
            print(f"[Network] File transfer {filename} received successfully.")
            self.file_save_handle.close()
            self.file_save_handle = None
            
            self.file_recv_complete.emit(filename)
            
            self.file_received_bytes = 0
            self.file_receiving_size = 0

    def run_file_sender(self, path, filesize, filename):
        """Worker thread to send a single file."""
        
        print(f"Starting file upload for: {filename}")
        try:
            sent = 0
            with open(path, "rb") as f:
                while sent < filesize:
                    if not self._is_running:
                        break
                    
                    chunk = f.read(TCP_BUFFER_SIZE)
                    if not chunk:
                        break
                    
                    encoded_chunk = base64.b64encode(chunk).decode('utf-8')
                    obj = {"type": "file_chunk", "data": encoded_chunk}
                    self.send_json_line(obj)
                    
                    sent += len(chunk)
                    self.file_send_progress.emit(filename, sent, filesize)
            
            if self._is_running:
                self.send_json_line({"type": "file_end", "filename": filename})
                
            print(f"Finished uploading file: {filename}")
        except Exception as e:
            if self._is_running:
                self.error.emit(f"File upload error: {e}")
    def run_video_sender(self):
       while self._is_running:
        try:
            payload = {'username': self.username}

            # --- 1. SCREEN SHARE LOGIC ---
            if self._screen_share_enabled:
                payload['frame_type'] = 'screen'
                try:
                    with mss.mss() as sct:
                        monitor = sct.monitors[1]
                        img_rgba = np.array(sct.grab(monitor))
                        img_bgr = cv2.cvtColor(img_rgba, cv2.COLOR_BGRA2BGR)
                        frame_resized = cv2.resize(img_bgr, (VIDEO_WIDTH, VIDEO_HEIGHT))

                        with self.screen_frame_lock:
                            self.latest_screen_frame = frame_resized.copy()

                        self.local_frame_ready.emit(frame_resized)
                        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 75]
                        _, buffer = cv2.imencode('.jpg', frame_resized, encode_param)
                        b = buffer.tobytes()
                        # print(f"[video send] type={payload['frame_type']} encoded_bytes={len(b)}")
                        payload['frame'] = b
                        payload['frame'] = buffer.tobytes()
                        # jpeg_bytes = buffer.tobytes()

                        # # print("[screen share] captured and encoded screen frame", len(buffer))
                        #  # --- ✨ NEW: Split into UDP-safe chunks ---
                        # CHUNK_SIZE = 60000
                        # chunks = [jpeg_bytes[i:i + CHUNK_SIZE] for i in range(0, len(jpeg_bytes), CHUNK_SIZE)]

                        # for i, chunk in enumerate(chunks):
                        #    header = {
                        #      "frame_type": "screen",
                        #      "seq": i,
                        #      "total": len(chunks)
                        #     }
                        #    packet = json.dumps(header).encode() + b"::" + chunk
                        #    self.video_socket.sendto(packet, self.video_server_addr)

                        # # print(f"[screen share] Sent {len(chunks)} UDP chunks ({len(jpeg_bytes)} bytes total)")

                except Exception as e:
                    print("screen capture error in sender thread:", e)
                    payload['frame'] = None

            # --- 2. WEBCAM LOGIC ---
            elif self._video_enabled:
                payload['frame_type'] = 'webcam'

                if self.cap is None or not self.cap.isOpened():
                    self.cap = cv2.VideoCapture(0)
                    if not self.cap.isOpened():
                        self.error.emit("Cannot open webcam. Turning camera off.")
                        self._video_enabled = False
                        time.sleep(1)
                        continue

                ret, frame = self.cap.read()
                if not ret:
                    time.sleep(0.1)
                    continue

                frame_resized = cv2.resize(frame, (VIDEO_WIDTH, VIDEO_HEIGHT))
                self.local_frame_ready.emit(frame_resized)
                encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]
                _, buffer = cv2.imencode('.jpg', frame_resized, encode_param)
                b = buffer.tobytes()
                # print(f"[video send] type={payload['frame_type']} encoded_bytes={len(b)}")
                payload['frame'] = b               

                payload['frame'] = buffer.tobytes()

            # --- 3. NONE LOGIC ---
            else:
                if self.cap is not None and self.cap.isOpened():
                    self.cap.release()
                    self.cap = None
                    print("Webcam released.")

                payload['frame_type'] = 'none'
                payload['frame'] = None
                self.local_frame_ready.emit(None)

            # --- 4. SEND PACKET ---
            if self.video_socket and self._is_running:
                data_to_send = pickle.dumps(payload, protocol=4)
                self.video_socket.sendto(data_to_send, self.video_server_addr)

        except Exception as e:
            if self._is_running:
                print(f"Video sender error: {e}")
                if self._screen_share_enabled:
                    print("Disabling screen share due to error.")
                    self._screen_share_enabled = False

        # --- 5. SLEEP CONTROL ---
        if self._video_enabled or self._screen_share_enabled:
            time.sleep(0.033)  # ~30 FPS
        else:
            time.sleep(0.1)

    # print("Video sender stopped.")


    def run_video_receiver(self):
        while self._is_running:
            try:
                # --- FIX 1: Listen on the correct socket ---
                packet, _ = self.video_socket.recvfrom(UDP_BUFFER_SIZE) 
                # print(f"[video recv] got packet bytes={len(packet)}")
                
                if packet and self._is_running:
                    data = pickle.loads(packet)
                    recv_username = data.get('username', 'Unknown')
                    frame_data = data.get('frame')
                    
                    # --- FIX 2: Read and emit the frame_type ---
                    frame_type = data.get('frame_type', 'none') 
                    # print(f"[video recv] from={data.get('username')} frame_type={frame_type} frame_data_len={len(frame_data) if frame_data else 0}")
                    
                    frame = None
                    if frame_data is not None:
                        frame = cv2.imdecode(np.frombuffer(frame_data, dtype=np.uint8), cv2.IMREAD_COLOR)
                        
                    # Emit all 3 arguments
                    self.video_frame_received.emit(recv_username, frame, frame_type)
                        
            except socket.timeout: continue
            except Exception as e:
                if self._is_running: print(f"Video receiver error: {e}")
        print("Video receiver stopped.")

    def run_audio_sender(self):
        try:
            self.audio_stream_in = self.audio_instance.open(format=AUDIO_FORMAT, channels=AUDIO_CHANNELS, rate=AUDIO_RATE, input=True, frames_per_buffer=AUDIO_CHUNK)
        except Exception as e:
            self.error.emit(f"Microphone error: {e}")
            return
        print("Audio sender started.")
        while self._is_running:
            try:
                payload = {'username': self.username}
                if self._audio_enabled:
                    audio_data = self.audio_stream_in.read(AUDIO_CHUNK)
                    payload['audio'] = audio_data
                else:
                    payload['audio'] = None
                if self.audio_socket:
                    data_to_send = pickle.dumps(payload, protocol=4)
                    self.audio_socket.sendto(data_to_send, self.audio_server_addr)
            except IOError: pass
            except Exception as e:
                if self._is_running: print(f"Audio sender error: {e}")
            if not self._audio_enabled: time.sleep(0.1)
        print("Audio sender stopped.")
    
    def run_audio_receiver(self):
        try:
            self.audio_stream_out = self.audio_instance.open(format=AUDIO_FORMAT, channels=AUDIO_CHANNELS, rate=AUDIO_RATE, output=True, frames_per_buffer=AUDIO_CHUNK)
        except Exception as e:
            self.error.emit(f"Speaker error: {e}")
            return
        print("Audio receiver started.")
        while self._is_running:
            try:
                packet, _ = self.audio_socket.recvfrom(UDP_BUFFER_SIZE)
                if packet and self._is_running:
                    data = pickle.loads(packet)
                    audio_data = data.get('audio')
                    if audio_data:
                        self.audio_stream_out.write(audio_data)
            except socket.timeout: continue
            except Exception as e:
                if self._is_running: print(f"Audio receiver error: {e}")
        print("Audio receiver stopped.")



