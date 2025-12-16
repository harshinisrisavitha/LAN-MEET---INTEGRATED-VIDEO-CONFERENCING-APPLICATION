import sys
import os
import cv2
import numpy as np
import time
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QPushButton, QHBoxLayout, QVBoxLayout,
    QLabel, QTextEdit, QListWidget, QListWidgetItem, QLineEdit, QFrame, QStackedWidget,
    QGridLayout, QInputDialog, QMessageBox, QFileDialog, QProgressBar
)
from PyQt5.QtCore import Qt, QSize, QPropertyAnimation, QEasingCurve, QTimer, QThread, pyqtSignal, QObject
from PyQt5.QtGui import QImage, QPixmap

# Import the networking logic
from networking_module import NetworkManager

# --- *** MODIFIED: Removed hard-coded IPs *** ---
# (Server config is the same)
VIDEO_SERVER_PORT = 5052
CHAT_SERVER_PORT = 8002
# These sizes are for the grid/placeholders, NOT the stream
VIDEO_WIDTH = 320
VIDEO_HEIGHT = 240
# --- *** END MODIFIED *** ---


# --- NEW: Centralized Theme Colors (CORRECTED) ---
THEMES = {
    'dark': {
        "root_bg": "#121212",
        "video_bg": "#181818",
        "panel_bg": "#1f1f1f",
        "pip_bg": "#2c2c2c",
        "input_bg": "#2a2a2a",
        "border_dark": "#333333",
        "border_light": "#444444",
        "text_primary": "#ffffff",  # <-- Was "white"
        "text_secondary": "#aaaaaa",
        "text_placeholder": "#555555",
        "video_placeholder_bg": "#000000",
        "img_placeholder_bg": "#202020",
        "btn_bg": "#3c4043",
        "btn_hover": "#4a4e51",
        "btn_accent": "#8ab4f8",
        "btn_accent_hover": "#9ac1f9",
        "btn_accent_text": "#121212",
        "btn_danger": "#e53935",
        "btn_danger_hover": "#f44336",
        "chat_system": "#8ab4f8",
        "chat_success": "#00e676",
        "chat_error": "#f44336",
        "chat_sender": "#ffa500",  # <-- Was "orange"
        "chat_private": "#800080",  # <-- Was "purple"
    },
    'light': {
        "root_bg": "#ffffff",
        "video_bg": "#f0f0f0",
        "panel_bg": "#f5f5f5",
        "pip_bg": "#e0e0e0",
        "input_bg": "#ffffff",
        "border_dark": "#cccccc",
        "border_light": "#bbbbbb",
        "text_primary": "#000000",  # <-- Was "black"
        "text_secondary": "#333333",
        "text_placeholder": "#999999",
        "video_placeholder_bg": "#e0e0e0",
        "img_placeholder_bg": "#dddddd",  # <-- Was "#ddd"
        "btn_bg": "#e0e0e0",
        "btn_hover": "#d5d5d5",
        "btn_accent": "#1a73e8",
        "btn_accent_hover": "#2980e9",
        "btn_accent_text": "#ffffff",
        "btn_danger": "#d93025",
        "btn_danger_hover": "#e74c3c",
        "chat_system": "#1a73e8",
        "chat_success": "#1e8e3e",
        "chat_error": "#d93025",
        "chat_sender": "#d95500",
        "chat_private": "#800080",  # <-- Was "purple"
    }
}


# (Utility functions are the same)
def create_placeholder_image(name, width, height, colors):
    img = np.zeros((height, width, 3), dtype=np.uint8)
    # Convert hex to BGR
    bg_color_hex = colors.get('img_placeholder_bg', '#202020')
    fg_color_hex = colors.get('text_primary', '#ffffff')
    
    bg_color = tuple(int(bg_color_hex.lstrip('#')[i:i+2], 16) for i in (4, 2, 0)) # BGR
    fg_color = tuple(int(fg_color_hex.lstrip('#')[i:i+2], 16) for i in (4, 2, 0)) # BGR
    img[:] = bg_color
    
    font = cv2.FONT_HERSHEY_SIMPLEX; font_scale = 0.8; thickness = 2
    text_size = cv2.getTextSize(name, font, font_scale, thickness)[0]
    text_x = (width - text_size[0]) // 2
    text_y = (height + text_size[1]) // 2
    cv2.putText(img, name, (text_x, text_y), font, font_scale, fg_color, thickness)
    return img

def convert_cv_to_qt(cv_img):
    if cv_img is None: return QPixmap()
    rgb_image = cv2.cvtColor(cv_img, cv2.COLOR_BGR2RGB)
    h, w, ch = rgb_image.shape
    bytes_per_line = ch * w
    qt_image = QImage(rgb_image.data, w, h, bytes_per_line, QImage.Format_RGB888)
    return QPixmap.fromImage(qt_image)

class VideoConferenceUI(QMainWindow):
    def __init__(self):
        super().__init__()
        
        # --- Theme setup ---
        self.current_theme = 'dark'
        self.colors = THEMES[self.current_theme]
        
        self.setWindowTitle("LAN Meet - Integrated")
        self.setGeometry(100, 100, 1400, 800)
        
        self.username = "User"
        self.is_chat_open = False
        self.is_participants_open = False
        self.is_files_open = False
        self.side_panel_width = 350
        self.video_widgets = {} 
        self.grid_cols = 3
        self.is_leaving=False
        
        # --- NEW FOR PRESENTATION MODE ---
        self.presenter_username = None # Tracks who is currently presenting
        
        self.pip_label = QLabel("Self View", self) # <-- Parent is self
        self.pip_label.setObjectName("PipLabel")
        self.pip_label.setFixedSize(200, 120)
        self.pip_label.setAlignment(Qt.AlignCenter)
        # --- END NEW ---
        
        # --- NEW: Chat history for theme refreshing ---
        self.chat_history = []
        
        # --- *** MODIFIED: Get IP and Username *** ---
        self.server_ip = "" # Will be set by get_server_ip
        if not self.get_server_ip():
            sys.exit()
            
        if not self.get_username():
            sys.exit()
        # --- *** END MODIFIED *** ---
        
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.root_layout = QVBoxLayout(self.central_widget)
        self.root_layout.setContentsMargins(0, 0, 0, 0); self.root_layout.setSpacing(0)
        self.content_layout = QHBoxLayout()
        self.content_layout.setContentsMargins(0, 0, 0, 0); self.content_layout.setSpacing(0)
        
        # --- Create network manager FIRST ---
        self.setup_networking()
        
        self.video_area = self.create_video_area() # <-- This is now a QStackedWidget
        self.content_layout.addWidget(self.video_area, stretch=1)
        
        self.side_panel = self.create_side_panel()
        self.side_panel.setMaximumWidth(0); self.side_panel.hide()
        self.content_layout.addWidget(self.side_panel, stretch=0)
        
        self.root_layout.addLayout(self.content_layout, stretch=1)
        
        self.control_bar = self.create_control_bar()
        self.root_layout.addWidget(self.control_bar, alignment=Qt.AlignBottom, stretch=0)
        
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setFixedHeight(8)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setValue(0)
        self.root_layout.addWidget(self.progress_bar)
        
        # --- Apply theme AFTER all widgets are created ---
        self.set_theme(self.current_theme)
        
        
        QTimer.singleShot(0, self.update_pip_position)

    # --- NEW: Stylesheet Generation ---
    def get_stylesheet(self):
        """Generates the entire app stylesheet from the theme colors."""
        c = self.colors # Shorthand
        return f"""
            VideoConferenceUI {{
                background-color: {c['root_bg']};
                color: {c['text_primary']};
                font-size: 14px;
            }}
            QStackedWidget#VideoArea {{
                background-color: {c['video_bg']};
                border: none;
            }}
            QLabel#InitialPlaceholder {{
                font-size: 24px;
                color: {c['text_placeholder']};
            }}
            QLabel#PipLabel {{
                background-color: {c['pip_bg']};
                border: 1px solid {c['border_light']};
                border-radius: 10px;
                font-size: 16px;
            }}
            QStackedWidget#SidePanel {{
                background-color: {c['panel_bg']};
                border-left: 1px solid {c['border_dark']};
            }}
            QFrame#ControlBar {{
                background-color: {c['root_bg']};
                border-top: 1px solid {c['border_dark']};
            }}
            QTextEdit {{
                background-color: {c['panel_bg']};
                border: 1px solid {c['border_dark']};
                border-radius: 5px;
                color: {c['text_primary']};
            }}
            QLineEdit {{
                background-color: {c['input_bg']};
                border: 1px solid {c['border_light']};
                border-radius: 5px;
                padding: 8px;
                color: {c['text_primary']};
            }}
            QListWidget {{
                border: 1px solid {c['border_dark']};
                border-radius: 5px;
                padding: 5px;
                background-color: {c['panel_bg']};
                color: {c['text_primary']};
            }}
            QLabel {{
                color: {c['text_primary']};
            }}
            QLabel#PanelHeader {{
                font-size: 16px;
                font-weight: bold;
                padding-bottom: 5px;
            }}
            QLabel#PresentationLabel {{
                background-color: {c['video_placeholder_bg']}; 
                border-radius: 8px;
            }}
            QProgressBar {{
                border: none;
                background-color: {c['root_bg']};
            }}
            QProgressBar::chunk {{
                background-color: {c['btn_accent']};
            }}
            
            /* Button Styles */
            QPushButton#AttachButton, QPushButton#ThemeButton {{
                background-color: {c['btn_bg']};
                border: none;
                border-radius: 5px;
                padding: 8px 12px;
                color: {c['text_primary']};
            }}
            QPushButton#AttachButton:hover, QPushButton#ThemeButton:hover {{
                background-color: {c['btn_hover']};
            }}
            
            QPushButton#SendButton, QPushButton#DownloadButton {{
                background-color: {c['btn_accent']};
                color: {c['btn_accent_text']};
                font-weight: bold;
                border: none;
                border-radius: 5px;
                padding: 8px 18px;
            }}
            QPushButton#SendButton:hover, QPushButton#DownloadButton:hover {{
                background-color: {c['btn_accent_hover']};
            }}
            
            QPushButton#LeaveButton {{
                background-color: {c['btn_danger']};
                border: none;
                border-radius: 20px;
                font-size: 14px;
                font-weight: bold;
                color: white;
                height: 40px;
                padding: 0 20px;
            }}
            QPushButton#LeaveButton:hover {{
                background-color: {c['btn_danger_hover']};
            }}
            QPushButton#ClosePanelButton {{
                background-color: transparent;
                border: none;
                font-size: 18px;
                font-weight: bold;
                color: {c['text_secondary']};
            }}
            QPushButton#ClosePanelButton:hover {{
                color: {c['text_primary']};
            }}
        """

    # --- NEW: Theme Switching ---
    def set_theme(self, mode):
        """Applies the specified theme ('light' or 'dark') to the app."""
        self.current_theme = mode
        self.colors = THEMES[mode]
        
        # Apply the master stylesheet
        self.setStyleSheet(self.get_stylesheet())
        
        # Refresh dynamic button styles
        self.toggle_mic(self.mic_button.isChecked())
        self.toggle_camera(self.cam_button.isChecked())
        
        # --- ADDED FOR SCREEN SHARE ---
        if hasattr(self, 'screen_button'):
            self.on_screen_share_toggled(self.screen_button.isChecked(), from_theme_change=True)
        # --- END ADD ---
        
        # --- ADDED FOR PRESENTATION MODE ---
        if hasattr(self, 'participant_grid_widget'):
            self.participant_grid_widget.setStyleSheet(f"background-color: {self.colors['video_placeholder_bg']};")
        # --- END ADD ---
        
        # Refresh placeholder images
        self.update_local_feed(None) # Will show placeholder
        for username, widget in self.video_widgets.items():
            # Create a new placeholder with the right colors
            placeholder = create_placeholder_image(username, VIDEO_WIDTH, VIDEO_HEIGHT, self.colors)
            pixmap = convert_cv_to_qt(placeholder)
            pixmap_scaled = pixmap.scaled(VIDEO_WIDTH, VIDEO_HEIGHT, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            widget.setPixmap(pixmap_scaled)
            
        # Refresh chat history
        self.redraw_chat()
        
        # Refresh PIP position
        self.update_pip_position()
    
    def on_leave_meeting(self):
        """Sets the leaving flag and then calls the close event."""
        self.is_leaving = True
        self.close()
        
    def toggle_theme(self):
        """Swaps between light and dark mode."""
        if self.current_theme == 'dark':
            self.set_theme('light')
        else:
            self.set_theme('dark')

    # --- *** NEW: Get Server IP *** ---
    def get_server_ip(self):
        """Shows a dialog to get the server's IP address."""
        ip, ok = QInputDialog.getText(self, 'Server IP', 'Enter the server IP address:', text="127.0.0.1")
        if ok and ip:
            self.server_ip = ip.strip()
            return True
        return False
    # --- *** END NEW *** ---

    def get_username(self):
        text, ok = QInputDialog.getText(self, 'Username', 'Enter your name:')
        if ok and text:
            self.username = text
            self.setWindowTitle(f"LAN Meet - {self.username}")
            return True
        return False

    def setup_networking(self):
        """Creates and starts the network manager in a separate thread."""
        self.network_thread = QThread()
        self.network_manager = NetworkManager()
        self.network_manager.moveToThread(self.network_thread)
        
        # Connect Signals to GUI Slots
        self.network_manager.connected.connect(self.on_connected)
        self.network_manager.disconnected.connect(self.on_disconnected)
        self.network_manager.error.connect(self.on_network_error)
        
        # Video
        self.network_manager.local_frame_ready.connect(self.update_local_feed)
        # This now connects a 3-arg signal to a 3-arg slot, which is correct
        self.network_manager.video_frame_received.connect(self.update_remote_feed)
        
        # Chat/File
        self.network_manager.chat_message_received.connect(self.on_chat_message)
        self.network_manager.user_list_updated.connect(self.on_user_list_update)
        
        self.network_manager.file_offer_received.connect(self.on_file_offer)
        self.network_manager.file_recv_complete.connect(self.on_file_received)
        self.network_manager.file_recv_progress.connect(self.on_file_progress)
        self.network_manager.file_send_progress.connect(self.on_file_progress)
        
        self.network_manager.file_list_received.connect(self.on_file_list_received)
        
        # Connect thread management
        # --- *** MODIFIED: Pass the input IP *** ---
        self.network_thread.started.connect(
            lambda: self.network_manager.connect_to_servers(
                self.username,
                self.server_ip, # <-- Use the variable
                VIDEO_SERVER_PORT,
                CHAT_SERVER_PORT
            )
        )
        # --- *** END MODIFIED *** ---
        self.network_thread.start()

    # --- REPLACED: create_video_area (Presentation Mode) ---
    def create_video_area(self):
        # The main video area is now a stack that can switch layouts
        self.video_area = QStackedWidget()
        self.video_area.setObjectName("VideoArea")
        
        # --- Layout 1: Grid View (Default) ---
        self.grid_view = QFrame()
        self.video_grid_layout = QGridLayout(self.grid_view)
        self.video_grid_layout.setContentsMargins(20, 20, 20, 20)
        self.video_grid_layout.setSpacing(10)
        
        self.initial_placeholder = QLabel("Connecting to servers...")
        self.initial_placeholder.setObjectName("InitialPlaceholder")
        self.initial_placeholder.setAlignment(Qt.AlignCenter)
        self.video_grid_layout.addWidget(self.initial_placeholder, 0, 0)
        
        # --- Layout 2: Presentation View ---
        self.presentation_view = QWidget()
        self.presentation_layout = QHBoxLayout(self.presentation_view)
        self.presentation_layout.setContentsMargins(10, 10, 10, 10)
        self.presentation_layout.setSpacing(10)
        
        # This is the main label for the screen share
        self.presentation_label = QLabel()
        self.presentation_label.setObjectName("PresentationLabel")
        self.presentation_label.setAlignment(Qt.AlignCenter)
        
        # This is the small grid for other participants
        self.participant_grid_widget = QWidget()
        self.participant_grid_widget.setFixedWidth(240)
        
        self.participant_grid_layout = QGridLayout(self.participant_grid_widget)
        self.participant_grid_layout.setContentsMargins(5, 5, 5, 5)
        self.participant_grid_layout.setSpacing(5)
        self.participant_grid_layout.setAlignment(Qt.AlignTop)
        
        self.presentation_layout.addWidget(self.presentation_label, stretch=1)
        self.presentation_layout.addWidget(self.participant_grid_widget)

        # --- Add both layouts to the stack ---
        self.video_area.addWidget(self.grid_view)
        self.video_area.addWidget(self.presentation_view)
        
        self.video_area.setCurrentWidget(self.grid_view) # Start in grid view
        
        # Move PiP label to be on top of the main window
        self.pip_label.raise_()
        
        return self.video_area
    # --- END REPLACED ---

    def create_side_panel(self):
        side_panel = QStackedWidget()
        side_panel.setObjectName("SidePanel") # <-- Set object name
        side_panel.setFixedWidth(self.side_panel_width)
        
        # --- Chat Panel ---
        self.chat_widget = QWidget()
        chat_layout = QVBoxLayout(self.chat_widget)
        chat_layout.setContentsMargins(15, 15, 15, 15); chat_layout.setSpacing(10)
        # --- Chat Header ---
        chat_header_layout = QHBoxLayout()
        chat_header = QLabel("In-call messages")
        chat_header.setObjectName("PanelHeader")
        chat_header_layout.addWidget(chat_header, stretch=1)

        chat_close_btn = QPushButton("X")
        chat_close_btn.setObjectName("ClosePanelButton")
        chat_close_btn.setFixedSize(24, 24)
        chat_close_btn.clicked.connect(lambda: self.animate_side_panel(show=False))
        chat_header_layout.addWidget(chat_close_btn)
        chat_layout.addLayout(chat_header_layout)
        
        self.chat_display = QTextEdit()
        self.chat_display.setReadOnly(True)
        chat_layout.addWidget(self.chat_display)
        
        self.message_input = QLineEdit()
        self.message_input.setPlaceholderText("Type a message... (use /w <user> for private)")
        self.message_input.returnPressed.connect(self.on_send_chat)
        chat_layout.addWidget(self.message_input)
        
        chat_button_layout = QHBoxLayout()
        chat_button_layout.addStretch()
        self.attach_button = QPushButton("Upload File")
        self.attach_button.setObjectName("AttachButton") # <-- Set object name
        self.attach_button.clicked.connect(self.on_attach_file)
        chat_button_layout.addWidget(self.attach_button)
        
        self.send_button = QPushButton("Send")
        self.send_button.setObjectName("SendButton") # <-- Set object name
        self.send_button.clicked.connect(self.on_send_chat)
        chat_button_layout.addWidget(self.send_button)
        
        chat_layout.addLayout(chat_button_layout)
        side_panel.addWidget(self.chat_widget)
        
        # --- Participants Panel ---
        self.participants_widget = QWidget()
        participants_layout = QVBoxLayout(self.participants_widget)
        participants_layout.setContentsMargins(15, 15, 15, 15); participants_layout.setSpacing(10)
        # --- Participants Header ---
        part_header_layout = QHBoxLayout()
        self.part_header = QLabel("Participants (1)")
        self.part_header.setObjectName("PanelHeader")
        part_header_layout.addWidget(self.part_header, stretch=1)

        part_close_btn = QPushButton("X")
        part_close_btn.setObjectName("ClosePanelButton")
        part_close_btn.setFixedSize(24, 24)
        part_close_btn.clicked.connect(lambda: self.animate_side_panel(show=False))
        part_header_layout.addWidget(part_close_btn)
        participants_layout.addLayout(part_header_layout)
        
        self.participant_list = QListWidget()
        participants_layout.addWidget(self.participant_list)
        side_panel.addWidget(self.participants_widget)
        
        # --- Files Panel ---
        self.files_widget = QWidget()
        files_layout = QVBoxLayout(self.files_widget)
        files_layout.setContentsMargins(15, 15, 15, 15); files_layout.setSpacing(10)
        
        # --- Files Header ---
        files_header_layout = QHBoxLayout()
        files_header = QLabel("Available Files")
        files_header.setObjectName("PanelHeader")
        files_header_layout.addWidget(files_header, stretch=1)

        files_close_btn = QPushButton("X")
        files_close_btn.setObjectName("ClosePanelButton")
        files_close_btn.setFixedSize(24, 24)
        files_close_btn.clicked.connect(lambda: self.animate_side_panel(show=False))
        files_header_layout.addWidget(files_close_btn)
        files_layout.addLayout(files_header_layout)
        
        self.file_list_widget = QListWidget()
        files_layout.addWidget(self.file_list_widget, stretch=1)
        
        self.download_button = QPushButton("Download Selected")
        self.download_button.setObjectName("DownloadButton")
        self.download_button.clicked.connect(self.on_download_selected_file)
        files_layout.addWidget(self.download_button)
        
        side_panel.addWidget(self.files_widget)
        
        return side_panel

    def create_control_bar(self):
        frame = QFrame(); frame.setFixedHeight(80)
        frame.setObjectName("ControlBar")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(20, 0, 20, 0); layout.setSpacing(12)
        
        self.mic_button = QPushButton("Mic Off")
        self.mic_button.setCheckable(True)
        self.mic_button.setChecked(False)
        self.mic_button.toggled.connect(self.toggle_mic)
        self.toggle_mic(False) # Call once to set style
        layout.addWidget(self.mic_button)
        
        self.cam_button = QPushButton("Cam Off")
        self.cam_button.setCheckable(True)
        self.cam_button.setChecked(False)
        self.cam_button.toggled.connect(self.toggle_camera)
        self.toggle_camera(False) # Call once to set style
        layout.addWidget(self.cam_button)

        # --- NEW: Screen Share Button ---
        self.screen_button = QPushButton("Present")
        self.screen_button.setCheckable(True)
        self.screen_button.setChecked(False)
        self.screen_button.toggled.connect(self.on_screen_share_toggled)
        self.on_screen_share_toggled(False, from_theme_change=True) # Call once to set style
        layout.addWidget(self.screen_button)
        # --- END NEW ---
        
        layout.addStretch()
        
        self.chat_button = QPushButton("Chat")
        self.chat_button.setStyleSheet(self.get_button_style(on=True))
        self.chat_button.clicked.connect(self.toggle_chat_panel)
        layout.addWidget(self.chat_button)
        
        self.participants_button = QPushButton("Participants")
        self.participants_button.setStyleSheet(self.get_button_style(on=True))
        self.participants_button.clicked.connect(self.toggle_participants_panel)
        layout.addWidget(self.participants_button)
        
        self.files_button = QPushButton("Files")
        self.files_button.setStyleSheet(self.get_button_style(on=True))
        self.files_button.clicked.connect(self.toggle_files_panel)
        layout.addWidget(self.files_button)
        
        layout.addStretch()

        # --- NEW: Theme Toggle Button ---
        self.theme_button = QPushButton("Change Theme")
        self.theme_button.setObjectName("ThemeButton")
        self.theme_button.clicked.connect(self.toggle_theme)
        layout.addWidget(self.theme_button)
        
        self.leave_button = QPushButton("Leave Meeting")
        self.leave_button.setObjectName("LeaveButton")
        self.leave_button.clicked.connect(self.on_leave_meeting)
        layout.addWidget(self.leave_button)
        
        return frame

    # --- MODIFIED: get_button_style now uses theme colors ---
    def get_button_style(self, on):
        """Gets the dynamic style for toggle buttons. (Not part of QSS)"""
        if on:
            return (
                f"QPushButton {{ background-color: {self.colors['btn_bg']}; border: none; border-radius: 20px; font-size: 14px; font-weight: bold; color: {self.colors['text_primary']}; height: 40px; padding: 0 18px; }}"
                f"QPushButton:hover {{ background-color: {self.colors['btn_hover']}; }}"
            )
        else:
            return (
                f"QPushButton {{ background-color: {self.colors['btn_danger']}; border: none; border-radius: 20px; font-size: 14px; font-weight: bold; color: white; height: 40px; padding: 0 18px; }}"
                f"QPushButton:hover {{ background-color: {self.colors['btn_danger_hover']}; }}"
            )
    # -----------------------------------
    # GUI Interaction Slots
    # -----------------------------------
    
    def on_send_chat(self):
        msg = self.message_input.text()
        if not msg: return
        
        target = "broadcast"
        if msg.startswith("/w "):
            try:
                _, target, msg = msg.split(" ", 2)
            except:
                self.on_chat_message("System", "Invalid private message format. Use: /w <user> <message>", False, "error")
                return
        
        self.network_manager.send_chat_message(target, msg)
        self.message_input.clear()

    def on_attach_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select file to upload")
        if file_path:
            self.network_manager.upload_file(file_path)
            self.on_chat_message("System", f"Starting upload for {os.path.basename(file_path)}...", False, "info")
            self.progress_bar.setValue(0)
    
    def toggle_mic(self, checked):
        if checked:
            self.mic_button.setText("Mic On")
            self.mic_button.setStyleSheet(self.get_button_style(on=True))
            self.network_manager.set_audio_enabled(True)
        else:
            self.mic_button.setText("Mic Off")
            self.mic_button.setStyleSheet(self.get_button_style(on=False))
            self.network_manager.set_audio_enabled(False)
            
    def toggle_camera(self, checked):
        if checked:
            self.cam_button.setText("Cam On")
            self.cam_button.setStyleSheet(self.get_button_style(on=True))
            
            # --- MODIFIED: Disable cam if screen sharing ---
            if self.screen_button.isChecked():
                QMessageBox.warning(self, "Camera Blocked", "You cannot turn on your camera while presenting. Stop presenting first.")
                self.cam_button.setChecked(False)
                return
            # --- END MODIFIED ---
            
            self.network_manager.set_video_enabled(True)
        else:
            self.cam_button.setText("Cam Off")
            self.cam_button.setStyleSheet(self.get_button_style(on=False))
            self.network_manager.set_video_enabled(False)
            
    # --- NEW: Screen Share Toggle Slot ---
    def on_screen_share_toggled(self, checked, from_theme_change=False):
        # This handles the logic for the "Present" button
        
        # Don't send network messages if just changing theme
        if not from_theme_change:
            # If turning on
            if checked:
                # Disable camera first
                if self.cam_button.isChecked():
                    self.cam_button.setChecked(False)
                
                self.network_manager.set_screen_share_enabled(True)
            
            # If turning off
            else:
                self.network_manager.set_screen_share_enabled(False)

        # Update style
        if checked:
            self.screen_button.setText("Stop Presenting")
            self.screen_button.setStyleSheet(self.get_button_style(on=False)) # Use 'off' (red) style
        else:
            self.screen_button.setText("Present")
            self.screen_button.setStyleSheet(self.get_button_style(on=True)) # Use 'on' style
    # --- END NEW ---

    def animate_side_panel(self, show, panel_type=None):
        if panel_type:
            if panel_type == 'chat':
                self.side_panel.setCurrentWidget(self.chat_widget)
            elif panel_type == 'participants':
                self.side_panel.setCurrentWidget(self.participants_widget)
            elif panel_type == 'files':
                self.side_panel.setCurrentWidget(self.files_widget)

        self.animation = QPropertyAnimation(self.side_panel, b"maximumWidth")
        self.animation.setDuration(300)
        self.animation.setEasingCurve(QEasingCurve.InOutCubic)
        
        if show:
            self.side_panel.show()
            self.animation.setStartValue(0)
            self.animation.setEndValue(self.side_panel_width)
        else:
            self.animation.setStartValue(self.side_panel_width)
            self.animation.setEndValue(0)
            self.animation.finished.connect(self.side_panel.hide)
            
        self.animation.start()
        
        self.is_chat_open = (show and panel_type == 'chat')
        self.is_participants_open = (show and panel_type == 'participants')
        self.is_files_open = (show and panel_type == 'files')

    def toggle_chat_panel(self):
        # If it's already open and is the chat panel, close it. Otherwise, open it.
        if self.side_panel.isVisible() and self.is_chat_open:
            self.animate_side_panel(show=False)
        else:
            self.animate_side_panel(show=True, panel_type='chat')

    def toggle_participants_panel(self):
        if self.side_panel.isVisible() and self.is_participants_open:
            self.animate_side_panel(show=False)
        else:
            self.animate_side_panel(show=True, panel_type='participants')

    def toggle_files_panel(self):
        if self.side_panel.isVisible() and self.is_files_open:
            self.animate_side_panel(show=False)
        else:
            self.animate_side_panel(show=True, panel_type='files')
            # Request the file list
            self.network_manager.request_file_list()

    # -----------------------------------
    # Network Signal Slots
    # -----------------------------------

    def on_connected(self):
        self.on_chat_message("System", "Connected! Requesting user list...", False, "success")
        self.network_manager.request_user_list()
        
        # Enable media buttons
        self.mic_button.setChecked(True)
        self.cam_button.setChecked(True)

    def on_disconnected(self, reason):
        self.on_chat_message("System", f"Disconnected: {reason}", False, "error")
        QMessageBox.critical(self, "Disconnected", f"Lost connection to server: {reason}")
        self.cam_button.setEnabled(False); self.mic_button.setEnabled(False)
        self.screen_button.setEnabled(False); self.send_button.setEnabled(False)
        self.attach_button.setEnabled(False); self.message_input.setEnabled(False)

    def on_network_error(self, message):
        self.on_chat_message("System", message, False, "error")

    # --- NEW: Redraw chat helper ---
    def redraw_chat(self):
        """Clears and redraws the chat from history using current theme."""
        self.chat_display.clear()
        for item in self.chat_history:
            self.append_chat_message(
                item["from"], item["msg"], item["is_private"], item["type"]
            )
    
    # --- NEW: Chat append helper ---
    def append_chat_message(self, sender, msg, is_private, msg_type="chat"):
        """Formats and appends a single chat message to the display."""
        c = self.colors # Shorthand
        if msg_type == "error":
            self.chat_display.append(f"<b style='color:{c['chat_error']}'>ERROR: {msg}</b>")
        elif msg_type == "info":
            self.chat_display.append(f"<i style='color:{c['chat_system']}'>{msg}</i>")
        elif msg_type == "success":
            self.chat_display.append(f"<i style='color:{c['chat_success']}'>{msg}</i>")
        else: # Regular chat
            color = c['chat_private'] if is_private else c['chat_sender']
            prefix = f"(Private) {sender}" if is_private else sender
            self.chat_display.append(f"<b style='color:{color}'>{prefix}:</b> {msg}")
    
    # --- MODIFIED: on_chat_message now saves history ---
    def on_chat_message(self, sender, msg, is_private, msg_type="chat"):
        # Save to history
        self.chat_history.append({
            "from": sender,
            "msg": msg,
            "is_private": is_private,
            "type": msg_type
        })
        # Append to display
        self.append_chat_message(sender, msg, is_private, msg_type)
    # --- REPLACED: This is the new, superior version ---
    def on_user_list_update(self, user_list):
        # user_list is now a list of strings
        self.participant_list.clear()
        rebuild_needed = False
        
        usernames_in_call = set()
        
        # 1. Add/Update all users from the list
        for username in user_list:
            usernames_in_call.add(username)
            
            # --- Add to participant list ---
            label = f"{username}"
            if username == self.username:
                label += " (You)"
            self.participant_list.addItem(label)
            # --- End ---
            
            # Skip adding self to the main video grid
            if username == self.username:
                continue
            
            # If user is new to the video grid, create a placeholder for them
            if username not in self.video_widgets:
                print(f"User {username} has joined (chat), adding to video grid.")
                new_label = QLabel()
                new_label.setAlignment(Qt.AlignCenter)
                new_label.setStyleSheet(f"background-color: {self.colors['video_placeholder_bg']}; border-radius: 8px;")
                new_label.setScaledContents(False)
                
                # Create and set placeholder
                placeholder = create_placeholder_image(username, VIDEO_WIDTH, VIDEO_HEIGHT, self.colors)
                pixmap = convert_cv_to_qt(placeholder)
                pixmap_scaled = pixmap.scaled(VIDEO_WIDTH, VIDEO_HEIGHT, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                
                new_label.setPixmap(pixmap_scaled)
                new_label.setFixedSize(VIDEO_WIDTH, VIDEO_HEIGHT)
                
                self.video_widgets[username] = new_label
                rebuild_needed = True
        
        # 2. Remove any users who are in the grid but NOT in the new list
        current_users_in_grid = set(self.video_widgets.keys())
        users_to_remove = current_users_in_grid - usernames_in_call
        
        for username in users_to_remove:
            print(f"User {username} has left, removing from video grid.")
            widget = self.video_widgets.pop(username)
            widget.deleteLater()
            rebuild_needed = True
        
        # 3. Update header
        self.part_header.setText(f"Participants ({len(user_list)})")
        
        # 4. Rebuild the visual grid if needed
        if rebuild_needed:
            self.rebuild_video_grid()
    # --- END REPLACED ---

    def on_file_offer(self, offer_data):
        self.on_chat_message("System", f"New file available from {offer_data['from']}: {offer_data['filename']} ({offer_data['size']} bytes)", False, "info")
        # Add to the file list panel
        self.add_file_to_list(offer_data)

    def on_file_progress(self, filename, sent, total):
        if total > 0:
            progress = int((sent / total) * 100)
            self.progress_bar.setValue(progress)

    def on_file_received(self, filename):
        self.progress_bar.setValue(100)
        self.on_chat_message("System", f"File '{filename}' received successfully! Saved in Downloads.", False, "success")
        QMessageBox.information(self, "Download Complete", f"File '{filename}' has been saved to your 'Downloads' folder.")
        QTimer.singleShot(2000, lambda: self.progress_bar.setValue(0))

    def on_file_list_received(self, file_list):
        self.file_list_widget.clear()
        if not file_list:
            item = QListWidgetItem("No files available on server.")
            item.setFlags(item.flags() & ~Qt.ItemIsSelectable)
            self.file_list_widget.addItem(item)
        else:
            for offer_data in file_list:
                self.add_file_to_list(offer_data)
                
    def on_download_selected_file(self):
        selected_item = self.file_list_widget.currentItem()
        if not selected_item or not selected_item.data(Qt.UserRole):
            QMessageBox.warning(self, "No File Selected", "Please select a file from the list to download.")
            return
        
        offer_data = selected_item.data(Qt.UserRole)
        filename = offer_data.get("filename")
        
        self.on_chat_message("System", f"Starting download for {filename}...", False, "info")
        self.network_manager.request_file_download(offer_data)
        self.progress_bar.setValue(0)

    def add_file_to_list(self, offer_data):
        filename = offer_data.get("filename", "Unknown")
        sender = offer_data.get("from", "Unknown")
        size = offer_data.get("size", 0)
        
        # If the first item is the "no files" placeholder, remove it
        if self.file_list_widget.count() == 1:
            first_item = self.file_list_widget.item(0)
            if not first_item.data(Qt.UserRole):
                self.file_list_widget.clear()
        
        # Check for duplicates
        for i in range(self.file_list_widget.count()):
            item = self.file_list_widget.item(i)
            if item.data(Qt.UserRole).get("filename") == filename:
                return # Already in list
        
        display_text = f"{filename}\n(From: {sender} - {size/1024:.1f} KB)"
        item = QListWidgetItem(display_text)
        item.setData(Qt.UserRole, offer_data) # Store all data
        self.file_list_widget.addItem(item)
        
    def update_local_feed(self, frame):
        if frame is None:
            # Show placeholder
            placeholder = create_placeholder_image("Cam Off", self.pip_label.width(), self.pip_label.height(), self.colors)
            pixmap = convert_cv_to_qt(placeholder)
        else:
            pixmap = convert_cv_to_qt(frame)
            
        pixmap = pixmap.scaled(self.pip_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.pip_label.setPixmap(pixmap)

    # --- REPLACED: This is the new, superior version ---
    def update_remote_feed(self, username, frame, frame_type):
        if username == self.username:
            return # Don't process our own remote feed

        # Get or create the video widget for this user
        if username not in self.video_widgets:
            # This should ideally be created by on_user_list_update,
            # but this is a good failsafe.
            print(f"WARNING: User {username} sent video before chat, adding to grid.")
            new_label = QLabel()
            new_label.setAlignment(Qt.AlignCenter)
            new_label.setStyleSheet(f"background-color: {self.colors['video_placeholder_bg']}; border-radius: 8px;")
            new_label.setScaledContents(False)
            self.video_widgets[username] = new_label
            # on_user_list_update will call rebuild_video_grid when it runs
            
        widget = self.video_widgets[username]
        
        # --- FIX: Check if this frame triggers a layout change ---
        old_frame_type = getattr(widget, 'frame_type', 'none')
        needs_rebuild = False
        
        # Check if presentation status is STARTING
        if frame_type == 'screen' and old_frame_type != 'screen':
            needs_rebuild = True
        
        # Check if presentation status is ENDING
        if frame_type != 'screen' and old_frame_type == 'screen':
            needs_rebuild = True
            
        # Store the new frame type *regardless*
        setattr(widget, 'frame_type', frame_type)
        
        if needs_rebuild:
            print(f"Rebuilding grid due to frame_type change for {username} (from {old_frame_type} to {frame_type})")
            self.rebuild_video_grid()
        # --- END FIX ---
            
        # Create a pixmap (either from frame or placeholder)
        if frame is None:
            placeholder = create_placeholder_image(username, VIDEO_WIDTH, VIDEO_HEIGHT, self.colors)
            pixmap = convert_cv_to_qt(placeholder)
        else:
            pixmap = convert_cv_to_qt(frame)
            
        # Apply the pixmap (either video or placeholder)
        if self.presenter_username == username:
            # If this is the presenter, scale to fit the big presentation label
            self.presentation_label.setPixmap(pixmap.scaled(self.presentation_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            # Scale to fit the grid
            widget.setPixmap(pixmap.scaled(VIDEO_WIDTH, VIDEO_HEIGHT, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            widget.setFixedSize(VIDEO_WIDTH, VIDEO_HEIGHT)
    # --- END REPLACED ---


    # --- REPLACED: This is the new, superior version ---
    def rebuild_video_grid(self):
        
        # 1. Determine if anyone is presenting
        presenter_username = None
        for uname, widget in self.video_widgets.items():
            if getattr(widget, 'frame_type', 'none') == 'screen':
                presenter_username = uname
                break
        
        # Detach all widgets from their old layouts
        for widget in self.video_widgets.values():
            widget.setParent(None)
        
        # Also clear the main presentation label
        self.presentation_label.setParent(None)
        self.presentation_label.setPixmap(QPixmap()) # Clear pixmap
            
        # Clear old placeholder if it exists
        if self.initial_placeholder and self.initial_placeholder.parent():
            self.video_grid_layout.removeWidget(self.initial_placeholder)
            self.initial_placeholder.deleteLater()
            self.initial_placeholder = None

        
        if presenter_username:
            # --- 2. BUILD PRESENTATION LAYOUT ---
            if self.presenter_username != presenter_username:
                print(f"Switching to presentation mode for {presenter_username}")
                
            self.presenter_username = presenter_username
            
            # Add main presentation widget
            presenter_widget = self.video_widgets[presenter_username]
            # We don't add the widget itself, we just set its pixmap on presentation_label
            # This is handled in update_remote_feed.
            self.presentation_layout.insertWidget(0, self.presentation_label, stretch=1)
            
            # Add all OTHER widgets to the participant grid
            # Clear old participant grid widgets
            for i in reversed(range(self.participant_grid_layout.count())):
                item = self.participant_grid_layout.itemAt(i)
                if item and item.widget():
                    item.widget().setParent(None)
            
            row, col = 0, 0
            for uname, widget in self.video_widgets.items():
                if uname != presenter_username:
                    self.participant_grid_layout.addWidget(widget, row, col)
                    row += 1 # Simple vertical list
            
            self.video_area.setCurrentWidget(self.presentation_view)

        else:
            # --- 3. BUILD GRID LAYOUT ---
            if self.presenter_username is not None:
                print("Switching back to grid mode.")
                
            self.presenter_username = None
            
            # Add all widgets to the main grid
            usernames = list(self.video_widgets.keys())
            
            for idx, username in enumerate(usernames):
                row = idx // self.grid_cols
                col = idx % self.grid_cols
                widget = self.video_widgets[username]
                self.video_grid_layout.addWidget(widget, row, col)
                
            # If no one is here, show placeholder
            if not self.video_widgets:
                self.initial_placeholder = QLabel("Waiting for other participants...")
                self.initial_placeholder.setObjectName("InitialPlaceholder")
                self.initial_placeholder.setAlignment(Qt.AlignCenter)
                self.video_grid_layout.addWidget(self.initial_placeholder, 0, 0)
                
            self.video_area.setCurrentWidget(self.grid_view)
        
        self.update()
        QTimer.singleShot(0, self.update_pip_position)
    # --- END REPLACED ---

    def update_pip_position(self):
        """Moves the PiP to the bottom-left of the video area."""
        # We must use self.video_area as the reference
        if not self.video_area: return
        
        pip_margin = 15
        
        # Calculate X
        x = self.video_area.pos().x() + pip_margin
        
        # Calculate Y
        # We use self.control_bar.pos().y() as the "floor"
        floor_y = self.control_bar.pos().y()
        y = floor_y - self.pip_label.height() - pip_margin
        
        self.pip_label.move(x, y)
        self.pip_label.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self.update_pip_position)

    def closeEvent(self, event):
        if not self.is_leaving:
            reply = QMessageBox.question(self, 'Quit', 
                                         "Are you sure you want to leave the meeting?", 
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.No:
                event.ignore()
                return
        
        self.is_leaving = True
        print("Closing application...")
        
        # Disconnect network
        if hasattr(self, 'network_manager'):
            self.network_manager.stop_all()
        if hasattr(self, 'network_thread'):
            self.network_thread.quit()
            self.network_thread.wait()
            
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = VideoConferenceUI()
    window.show()
    sys.exit(app.exec_())