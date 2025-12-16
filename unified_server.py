import socket
import threading
import json
import os
import base64
import time
import logging # Import the logging module

# --- Consolidated Configuration ---
HOST = '0.0.0.0'
STORAGE_DIR = "server_files"

# Chat (TCP)
CHAT_PORT = 8002
TCP_BUFFER_SIZE = 4096

# Video (UDP)
VIDEO_PORT = 5052
VIDEO_TIMEOUT = 200  # Seconds

# Audio (UDP)
AUDIO_PORT = 5053
AUDIO_TIMEOUT = 200000 # Keeping your original value

# General UDP
UDP_BUFFER_SIZE = 65536 # Max UDP packet size


#
# --- 1. TCP Chat Server Class (From chat_server.py) ---
#
class ChatServer:
    def __init__(self,chat_logger):
        self.chat_logger=chat_logger
        self.clients = {}  # {conn: username}
        self.usernames = {} # {username: conn}
        self.receiving_files = {} # {username: file_handle}
        
        self.available_files = [] 
        
        # Create storage directory
        os.makedirs(STORAGE_DIR, exist_ok=True)
        
        self.load_existing_files()
        
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.bind((HOST, CHAT_PORT)) # Use CHAT_PORT
        self.server_socket.listen(10)
        logging.info(f"Chat server listening on {HOST}:{CHAT_PORT} (TCP)")

    def load_existing_files(self):
        """Scans the STORAGE_DIR and populates the available_files list."""
        logging.info(f"Loading existing files from '{STORAGE_DIR}'...")
        count = 0
        for filename in os.listdir(STORAGE_DIR):
            filepath = os.path.join(STORAGE_DIR, filename)
            if os.path.isfile(filepath):
                try:
                    filesize = os.path.getsize(filepath)
                    file_info = {
                        "type": "new_file_available",
                        "filename": filename,
                        "size": filesize,
                        "from": "Server (Cached)"
                    }
                    self.available_files.append(file_info)
                    count += 1
                except Exception as e:
                    logging.error(f"Error loading existing file {filename}", exc_info=True)
        logging.info(f"Loaded {count} existing files.")

    def start(self):
        try:
            while True:
                conn, addr = self.server_socket.accept()
                threading.Thread(target=self.handle_client, args=(conn, addr), daemon=True).start()
        except KeyboardInterrupt:
            logging.info("Server is shutting down.")
        finally:
            self.server_socket.close()
            logging.info("Chat server socket closed.")

    def handle_client(self, conn, addr):
        logging.info(f"New chat connection from {addr}")
        username = None
        try:
            conn.sendall(b"Enter username: ")
            username = conn.recv(1024).decode().strip()
            
            if not username or username in self.usernames:
                conn.sendall(b"Username is empty or already taken.\n")
                raise Exception("Invalid username")

            # Add client
            self.clients[conn] = username
            self.usernames[username] = conn
            conn.sendall(b"Username accepted. Welcome!\n")
            logging.info(f"{username} joined from {addr}")
            
            self.broadcast_message(f"--- {username} has joined the chat. ---", "System")
            self.broadcast_user_list()

            # Main loop for client messages
            fileobj = conn.makefile("rb")
            while True:
                line = fileobj.readline()
                if not line:
                    break
                
                try:
                    obj = json.loads(line.decode())
                    self.handle_json_message(obj, username, conn)
                except json.JSONDecodeError:
                    logging.warning(f"Error decoding JSON from {username}")
        
        except Exception as e:
            if "Invalid username" not in str(e) and "forcibly closed" not in str(e):
                logging.error(f"Error with {addr} ({username})", exc_info=True)
        
        finally:
            # Client disconnected
            conn.close()
            if conn in self.clients:
                username = self.clients[conn]
                del self.clients[conn]
                if username in self.usernames:
                    del self.usernames[username]
                
                # Clean up any partial file transfers
                if username in self.receiving_files:
                    self.receiving_files[username].close()
                    del self.receiving_files[username]
                    
                logging.info(f"{username} has disconnected.")
                self.broadcast_message(f"--- {username} has left the chat. ---", "System")
                self.broadcast_user_list()

    def handle_json_message(self, obj, username, conn):
        msg_type = obj.get("type")
        
        if msg_type == "chat":
            to = obj.get("to", "broadcast")
            logging.info(f"Chat from '{username}' to '{to}'")
            if to == "broadcast":
                self.broadcast_message(obj.get("msg"), username)
            else:
                self.send_private_message(obj.get("msg"), username, to)
        
        elif msg_type == "command" and obj.get("cmd") == "/users":
            logging.info(f"'{username}' requested user list")
            self.send_json(conn, {"type": "user_list", "users": list(self.usernames.keys())})
            
        # --- FILE LOGIC ---
        elif msg_type == "upload_start":
            self.handle_upload_start(obj, username)
        
        elif msg_type == "file_chunk":
            self.handle_file_chunk(obj, username)
            
        elif msg_type == "file_end":
            self.handle_file_end(obj, username)
            
        elif msg_type == "download_request":
            self.handle_download_request(obj, username, conn)
            
        elif msg_type == "REQUEST_FILE_LIST":
            logging.info(f"'{username}' requested file list")
            self.handle_file_list_request(conn)
            
    def handle_upload_start(self, obj, username):
        filename = obj.get("filename")
        if not filename:
            return
        
        # Sanitize filename
        filename = os.path.basename(filename)
        save_path = os.path.join(STORAGE_DIR, filename)
        
        if username in self.receiving_files:
            # User is already uploading, close old handle
            self.receiving_files[username].close()
            
        try:
            self.receiving_files[username] = open(save_path, "wb")
            logging.info(f"{username} is starting upload of {filename}")
        except Exception as e:
            logging.error(f"Error opening file {save_path}", exc_info=True)
            self.send_json(self.usernames[username], {"type": "error", "msg": "Server failed to open file."})

    def handle_file_chunk(self, obj, username):
        file_handle = self.receiving_files.get(username)
        if file_handle:
            try:
                data = base64.b64decode(obj.get("data"))
                file_handle.write(data)
            except Exception as e:
                logging.warning(f"File chunk error from {username}: {e}")

    def handle_file_end(self, obj, username):
        file_handle = self.receiving_files.pop(username, None)
        filename = obj.get("filename")
        if file_handle:
            file_handle.close()
            logging.info(f"{username} finished uploading {filename}")
            
            # Announce the new file to ALL OTHER clients
            save_path = os.path.join(STORAGE_DIR, filename)
            filesize = os.path.getsize(save_path)
            
            announcement = {
                "type": "new_file_available",
                "filename": filename,
                "size": filesize,
                "from": username
            }
            
            self.available_files = [f for f in self.available_files if f.get("filename") != filename]
            self.available_files.append(announcement)
            
            # Broadcast to all clients *except* the sender
            self.broadcast_json(announcement, sender_conn=self.usernames[username])
        
    def handle_download_request(self, obj, username, conn):
        filename = obj.get("filename")
        if not filename:
            return
            
        filepath = os.path.join(STORAGE_DIR, os.path.basename(filename))
        
        if os.path.exists(filepath):
            logging.info(f"{username} is downloading {filename}...")
            # Start a new thread to send the file
            threading.Thread(target=self.run_file_sender_to_client, 
                             args=(conn, filepath, filename), 
                             daemon=True).start()
        else:
            self.send_json(conn, {"type": "error", "msg": f"File '{filename}' not found on server."})

    def handle_file_list_request(self, conn):
        """Sends the complete list of available files to the requesting client."""
        logging.info(f"Sending file list to {self.clients.get(conn)}")
        response = {
            "type": "FILE_LIST_UPDATE",
            "files": self.available_files
        }
        self.send_json(conn, response)

    def run_file_sender_to_client(self, conn, filepath, filename):
        """Worker thread to send a single file to one client."""
        try:
            with open(filepath, "rb") as f:
                while True:
                    chunk = f.read(TCP_BUFFER_SIZE) # Use TCP_BUFFER_SIZE
                    if not chunk:
                        break # End of file
                    
                    encoded_chunk = base64.b64encode(chunk).decode('utf-8')
                    obj = {"type": "file_chunk", "data": encoded_chunk}
                    self.send_json(conn, obj)
            
            # Send an 'end' message
            self.send_json(conn, {"type": "file_end", "filename": filename})
            logging.info(f"Finished sending {filename} to {self.clients.get(conn)}")
            
        except Exception as e:
            logging.error(f"Error sending file {filename} to {self.clients.get(conn)}", exc_info=True)
            # Try to send an error to the client
            try:
                self.send_json(conn, {"type": "error", "msg": f"File transfer failed: {e}"})
            except:
                pass

    def send_json(self, conn, obj):
        try:
            s = json.dumps(obj) + "\n"
            conn.sendall(s.encode())
        except Exception as e:
            logging.warning(f"Error sending JSON: {e}")
            
    def broadcast_json(self, obj, sender_conn=None):
        for conn in self.clients:
            if conn != sender_conn:
                self.send_json(conn, obj)

    def broadcast_message(self, message, sender_username):
        self.chat_logger.info(f"[{sender_username}] (Broadcast): {message}")
        msg_obj = {"type": "chat_message", "from": sender_username, "msg": message, "private": False}
        self.broadcast_json(msg_obj)

    def send_private_message(self, message, sender_username, recipient_username):
        self.chat_logger.info(f"[{sender_username} -> {recipient_username}] (Private): {message}")
        recipient_conn = self.usernames.get(recipient_username)
        if recipient_conn:
            msg_obj = {"type": "chat_message", "from": sender_username, "msg": message, "private": True}
            self.send_json(recipient_conn, msg_obj)
            # Send copy to self
            self.send_json(self.usernames[sender_username], msg_obj)
        else:
            self.send_json(self.usernames[sender_username], 
                           {"type": "error", "msg": f"User '{recipient_username}' not found."})

    def broadcast_user_list(self):
        user_list_obj = {"type": "user_list", "users": list(self.usernames.keys())}
        self.broadcast_json(user_list_obj)

#
# --- 2. Generic UDP Broadcast Server (From video/audio_server.py) ---
#
def run_udp_broadcast_server(port, server_name, client_timeout):
    """
    A generic UDP broadcast server function.
    Listens on 'port', logs as 'server_name', and uses 'client_timeout'.
    """
    clients = {} # { (ip, port): last_seen_time }
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    
    try:
        server_socket.bind((HOST, port))
        logging.info(f"[*] {server_name} Server listening on {HOST}:{port} (UDP)")
    except socket.error as e:
        logging.error(f"Error binding {server_name} server: {e}", exc_info=True)
        return

    try:
        while True:
            try:
                data, addr = server_socket.recvfrom(UDP_BUFFER_SIZE)
            except Exception as e:
                logging.warning(f"Error receiving data on {server_name} server: {e}")
                continue

            if addr not in clients:
                logging.info(f"[+] {server_name} client connected: {addr}")
            clients[addr] = time.time()

            current_time = time.time()
            for client_addr in list(clients.keys()):
                
                if current_time - clients[client_addr] > client_timeout:
                    logging.info(f"[-] {server_name} client {client_addr} timed out. Removing.")
                    del clients[client_addr]
                    continue

                if client_addr != addr:
                    try:
                        server_socket.sendto(data, client_addr)
                    except Exception as e:
                        logging.warning(f"Error sending {server_name} data to {client_addr}: {e}")
                        
    except KeyboardInterrupt:
        # This will likely be triggered by the main thread's shutdown
        logging.info(f"[*] {server_name} Server is shutting down.")
    except Exception as e:
        logging.critical(f"{server_name} Server crashed: {e}", exc_info=True)
    finally:
        server_socket.close()
        logging.info(f"{server_name} Server socket closed.")

#
# --- 3. Main execution ---
#
if __name__ == "__main__":
    # --- Configure logging to FILE ONLY ---
    log_format = '%(asctime)s - %(levelname)s - %(message)s'
    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        handlers=[
            logging.FileHandler("unified_server.txt")  # Log to file
        ]
    )

    # --- 2. Configure dedicated CHAT logging (chat_log.txt) ---
    chat_log_formatter = logging.Formatter('%(asctime)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    chat_logger = logging.getLogger('ChatLogger')
    chat_logger.setLevel(logging.INFO)

    chat_handler = logging.FileHandler("chat_log.txt", encoding='utf-8')
    chat_handler.setFormatter(chat_log_formatter)

    chat_logger.addHandler(chat_handler)
    chat_logger.propagate = False # Prevent chat logs from going to unified_server.txt

    # --- Start UDP Servers in Daemon Threads ---
    logging.info("Starting Video server thread...")
    video_thread = threading.Thread(
        target=run_udp_broadcast_server, 
        args=(VIDEO_PORT, "Video", VIDEO_TIMEOUT), 
        daemon=True
    )
    video_thread.start()
    
    logging.info("Starting Audio server thread...")
    audio_thread = threading.Thread(
        target=run_udp_broadcast_server, 
        args=(AUDIO_PORT, "Audio", AUDIO_TIMEOUT), 
        daemon=True
    )
    audio_thread.start()

    # --- Start TCP Chat Server (in the main thread) ---
    logging.info("Starting Chat server (main thread)...")
    # The ChatServer class __init__ handles creating the storage dir
    chat_server = ChatServer(chat_logger)
    chat_server.start() # This is a blocking call that will run until exit

    logging.info("Main server application has finished.")
