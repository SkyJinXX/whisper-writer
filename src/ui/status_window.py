import sys
import os
from PyQt5.QtCore import Qt, pyqtSignal, pyqtSlot, QTimer
from PyQt5.QtGui import QFont, QPixmap, QIcon, QColor
from PyQt5.QtWidgets import QApplication, QLabel, QHBoxLayout, QVBoxLayout, QProgressBar

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from ui.base_window import BaseWindow
from utils import ConfigManager

class StatusWindow(BaseWindow):
    statusSignal = pyqtSignal(str)
    closeSignal = pyqtSignal()
    readySignal = pyqtSignal()  # New signal to indicate ready state

    def __init__(self):
        """
        Initialize the status window.
        """
        super().__init__('WhisperWriter Status', 320, 150)
        self.initStatusUI()
        self.statusSignal.connect(self.updateStatus)
        self.countdown_timer = None
        self.countdown_value = 0

    def initStatusUI(self):
        """
        Initialize the status user interface.
        """
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        
        status_layout = QVBoxLayout()
        status_layout.setContentsMargins(10, 10, 10, 10)

        # Status header with icon
        header_layout = QHBoxLayout()
        
        self.icon_label = QLabel()
        self.icon_label.setFixedSize(32, 32)
        microphone_path = os.path.join('assets', 'microphone.png')
        pencil_path = os.path.join('assets', 'pencil.png')
        self.microphone_pixmap = QPixmap(microphone_path).scaled(32, 32, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.pencil_pixmap = QPixmap(pencil_path).scaled(32, 32, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.icon_label.setPixmap(self.microphone_pixmap)
        self.icon_label.setAlignment(Qt.AlignCenter)

        self.status_label = QLabel('Initializing...')
        self.status_label.setFont(QFont('Segoe UI', 12))

        header_layout.addStretch(1)
        header_layout.addWidget(self.icon_label)
        header_layout.addWidget(self.status_label)
        header_layout.addStretch(1)

        # Progress bar for countdown
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(10)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                background-color: #f0f0f0;
                border-radius: 5px;
            }
            QProgressBar::chunk {
                background-color: #4CAF50;
                border-radius: 5px;
            }
        """)
        
        # Ready indicator
        self.ready_label = QLabel('Get ready...')
        self.ready_label.setFont(QFont('Segoe UI', 10))
        self.ready_label.setAlignment(Qt.AlignCenter)
        self.ready_label.setStyleSheet("color: #666;")
        
        status_layout.addLayout(header_layout)
        status_layout.addWidget(self.progress_bar)
        status_layout.addWidget(self.ready_label)

        self.main_layout.addLayout(status_layout)
        
    def show(self):
        """
        Position the window in the bottom center of the screen and show it.
        """
        screen = QApplication.primaryScreen()
        screen_geometry = screen.geometry()
        screen_width = screen_geometry.width()
        screen_height = screen_geometry.height()
        window_width = self.width()
        window_height = self.height()

        x = (screen_width - window_width) // 2
        y = screen_height - window_height - 120

        self.move(x, y)
        super().show()
        
    def closeEvent(self, event):
        """
        Emit the close signal when the window is closed.
        """
        if self.countdown_timer and self.countdown_timer.isActive():
            self.countdown_timer.stop()
        self.closeSignal.emit()
        super().closeEvent(event)

    def startCountdown(self, duration_ms=1500):
        """
        Start a countdown before recording actually begins
        """
        if self.countdown_timer and self.countdown_timer.isActive():
            self.countdown_timer.stop()
            
        # Setup countdown
        self.countdown_value = 0
        self.progress_bar.setValue(0)
        self.ready_label.setText('Get ready...')
        self.ready_label.setStyleSheet("color: #666;")
        self.progress_bar.setVisible(True)
        
        # Calculate timer interval (refresh rate)
        steps = 20  # number of steps for smooth progress
        interval = duration_ms / steps
        increment = 100 / steps
        
        # Create and start timer
        self.countdown_timer = QTimer(self)
        self.countdown_timer.timeout.connect(
            lambda: self.updateCountdown(increment, duration_ms)
        )
        self.countdown_timer.start(int(interval))

    def updateCountdown(self, increment, total_duration):
        """Update the countdown progress bar"""
        self.countdown_value += increment
        self.progress_bar.setValue(int(self.countdown_value))
        
        # Change label as countdown progresses
        if self.countdown_value < 60:
            self.ready_label.setText('Get ready...')
        elif self.countdown_value < 90:
            self.ready_label.setText('Almost there...')
        else:
            self.ready_label.setText('Ready to record!')
            self.ready_label.setStyleSheet("color: #4CAF50; font-weight: bold;")
        
        # When countdown completes
        if self.countdown_value >= 100:
            self.countdown_timer.stop()
            self.readySignal.emit()
            
            # Flash the ready indicator briefly
            flash_timer = QTimer(self)
            flash_count = [0]  # Use list to allow modification in closure
            
            def flash():
                if flash_count[0] % 2 == 0:
                    self.ready_label.setStyleSheet("color: #4CAF50; font-weight: bold;")
                else:
                    self.ready_label.setStyleSheet("color: #FFF; font-weight: bold; background-color: #4CAF50; border-radius: 5px;")
                flash_count[0] += 1
                
                if flash_count[0] > 6:  # 3 flashes
                    flash_timer.stop()
                    self.ready_label.setStyleSheet("color: #4CAF50; font-weight: bold;")
                    
            flash_timer.timeout.connect(flash)
            flash_timer.start(200)  # Flash every 200ms

    @pyqtSlot(str)
    def updateStatus(self, status):
        """
        Update the status window based on the given status.
        """
        if status == 'recording':
            self.icon_label.setPixmap(self.microphone_pixmap)
            self.status_label.setText('Initializing...')
            self.show()
            # 倒计时非常短，几乎立即结束
            countdown_ms = 1  # 使用1ms的倒计时，实际上是立即完成
            ConfigManager.console_print(f"UI倒计时设为{countdown_ms}ms (立即完成)")
            self.startCountdown(countdown_ms)
        elif status == 'ready':
            self.icon_label.setPixmap(self.microphone_pixmap)
            self.status_label.setText('Recording...')
        elif status == 'transcribing':
            self.icon_label.setPixmap(self.pencil_pixmap)
            self.status_label.setText('Transcribing...')
            self.progress_bar.setVisible(False)
            self.ready_label.setVisible(False)

        if status in ('idle', 'error', 'cancel'):
            self.close()


if __name__ == '__main__':
    app = QApplication(sys.argv)
    
    status_window = StatusWindow()
    status_window.show()

    # Simulate status updates
    QTimer.singleShot(1000, lambda: status_window.statusSignal.emit('recording'))
    QTimer.singleShot(4000, lambda: status_window.statusSignal.emit('ready'))
    QTimer.singleShot(7000, lambda: status_window.statusSignal.emit('transcribing'))
    QTimer.singleShot(10000, lambda: status_window.statusSignal.emit('idle'))
    
    sys.exit(app.exec_())
