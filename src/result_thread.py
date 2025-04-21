import time
import traceback
import numpy as np
import sounddevice as sd
import tempfile
import wave
import webrtcvad
from PyQt5.QtCore import QThread, QMutex, pyqtSignal, QTimer
from collections import deque
from threading import Event
import os

from transcription import transcribe
from utils import ConfigManager


class ResultThread(QThread):
    """
    A thread class for handling audio recording, transcription, and result processing.

    This class manages the entire process of:
    1. Recording audio from the microphone
    2. Detecting speech and silence
    3. Saving the recorded audio as numpy array
    4. Transcribing the audio
    5. Emitting the transcription result

    Signals:
        statusSignal: Emits the current status of the thread (e.g., 'recording', 'transcribing', 'idle')
        resultSignal: Emits the transcription result
        readySignal: Emits when the recording is ready to start after initialization
    """

    statusSignal = pyqtSignal(str)
    resultSignal = pyqtSignal(str)
    readySignal = pyqtSignal()  # Signal when ready to actually record

    def __init__(self, local_model=None):
        """
        Initialize the ResultThread.

        :param local_model: Local transcription model (if applicable)
        """
        super().__init__()
        self.local_model = local_model
        self.is_recording = False
        self.ready_to_record = False  # Flag for actual recording after countdown
        self.is_running = True
        self.sample_rate = None
        self.mutex = QMutex()
        # 仍然保留预录制缓冲区以兼容其他代码
        self.pre_recording_buffer = deque(maxlen=96000)  # 6秒的预录制缓冲区 (16000Hz * 6)

    def stop_recording(self):
        """Stop the current recording session."""
        self.mutex.lock()
        self.is_recording = False
        self.ready_to_record = False
        self.mutex.unlock()

    def stop(self):
        """Stop the entire thread execution."""
        self.mutex.lock()
        self.is_running = False
        self.mutex.unlock()
        self.statusSignal.emit('idle')
        self.wait()

    def run(self):
        """Main execution method for the thread."""
        try:
            if not self.is_running:
                return

            self.mutex.lock()
            self.is_recording = True
            self.ready_to_record = False  # Not ready until countdown completes
            self.mutex.unlock()
            
            # 简单模式：只在用户按下热键后开始录音
            ConfigManager.console_print('初始化录音...')
            
            # 直接启动UI
            self.statusSignal.emit('recording')  # This will start the countdown in the UI
            ConfigManager.console_print('等待用户准备好开始录音...')
            
            # 直接使用_record_audio录制
            audio_data = self._record_audio()

            if not self.is_running:
                return

            if audio_data is None:
                self.statusSignal.emit('idle')
                return

            self.statusSignal.emit('transcribing')
            ConfigManager.console_print('Transcribing...')

            # Time the transcription process
            start_time = time.time()
            result = transcribe(audio_data, self.local_model)
            end_time = time.time()

            transcription_time = end_time - start_time
            ConfigManager.console_print(f'Transcription completed in {transcription_time:.2f} seconds. Post-processed line: {result}')

            if not self.is_running:
                return

            self.statusSignal.emit('idle')
            self.resultSignal.emit(result)

        except Exception as e:
            traceback.print_exc()
            self.statusSignal.emit('error')
            self.resultSignal.emit('')
        finally:
            self.stop_recording()

    def _record_audio(self):
        """在用户按下热键后录制音频并返回音频数据"""
        # 获取配置
        recording_options = ConfigManager.get_config_section('recording_options')
        self.sample_rate = recording_options.get('sample_rate') or 16000
        sound_device = recording_options.get('sound_device')
        
        # 确定输入设备
        if sound_device is None:
            try:
                default_device = sd.query_devices(kind='input')
                sound_device = default_device['index']
                ConfigManager.console_print(f"使用默认麦克风: {default_device['name']}")
            except Exception as e:
                ConfigManager.console_print(f"获取默认设备出错: {e}")
                return None
        
        # 设置录音参数
        max_seconds = 60  # 最大录音时长
        frames = []  # 存储音频帧
        started_recording = False  # 标记是否开始录音
        
        # 定义回调函数
        def audio_callback(indata, frame_count, time_info, status):
            nonlocal started_recording
            if status:
                ConfigManager.console_print(f"音频状态: {status}")
                
            # 只有在用户按下热键后(ready_to_record为True)才录制
            if self.ready_to_record:
                frames.append(indata.copy())
                if not started_recording:
                    started_recording = True
                    record_start_time = time.time()
                    ConfigManager.console_print(f"开始录制音频于: {record_start_time:.6f}")
                    # 只有在实际开始录音后，才发出ready信号，提示UI更新状态
                    self.statusSignal.emit('ready')  # 通知UI更新为"录制中"状态
        
        # 开始录音
        ConfigManager.console_print("初始化音频设备...")
        try:
            # 创建并启动音频流，使用更小的blocksize以减少延迟
            with sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype='int16',
                callback=audio_callback,
                device=sound_device,
                blocksize=256,  # 使用非常小的块大小，减少延迟、加快初始化
                latency='low' 
            ):
                ConfigManager.console_print(f"音频设备初始化完成，等待用户信号...")
                
                # 等待用户开始录音
                ConfigManager.console_print("等待录音信号...")
                while not self.ready_to_record and self.is_recording and self.is_running:
                    sd.sleep(10)
                
                if not self.ready_to_record:
                    return None
                
                # 用户已按下热键，记录开始时间
                start_time = time.time()
                ConfigManager.console_print(f"用户已按下热键，开始录音 {start_time:.6f}")
                
                # 录音直到用户停止或达到最大时长
                ConfigManager.console_print("录音进行中...")
                
                # 等待录音结束
                while self.is_recording and self.is_running and (time.time() - start_time < max_seconds):
                    sd.sleep(100)
                
            # 处理录音
            if not frames:
                ConfigManager.console_print("未录到声音")
                return None
            
            # 打印信息
            ConfigManager.console_print(f"录音结束，收集了 {len(frames)} 帧数据")
            
            # 合并所有帧
            try:
                # 合并音频帧
                audio_data = np.concatenate([f.reshape(-1) for f in frames])
                duration = len(audio_data) / self.sample_rate
                ConfigManager.console_print(f"录音完成: {duration:.2f}秒, {len(frames)}帧")
                
                # 保存音频用于调试
                self._save_debug_recording(audio_data, "direct")
                
                # 检查是否太短
                min_duration_ms = recording_options.get('min_duration') or 100
                if (duration * 1000) < min_duration_ms:
                    return None
                
                return audio_data
            except Exception as e:
                ConfigManager.console_print(f"处理音频出错: {e}")
                traceback.print_exc()
                return None
                
        except Exception as e:
            ConfigManager.console_print(f"录音出错: {e}")
            traceback.print_exc()
            return None

    def _save_debug_recording(self, audio_data, prefix="recording"):
        """Helper method to save a recording for debugging"""
        try:
            # Create debug directory if it doesn't exist
            debug_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'debug')
            if not os.path.exists(debug_dir):
                os.makedirs(debug_dir)
            
            # Generate filename with timestamp
            timestamp = time.strftime("%Y%m%d-%H%M%S")
            debug_filename = os.path.join(debug_dir, f'{prefix}_{timestamp}.wav')
            
            with wave.open(debug_filename, 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)  # 16-bit audio
                wf.setframerate(self.sample_rate)
                wf.writeframes(audio_data.tobytes())
            
            ConfigManager.console_print(f'Saved debug recording to {debug_filename}')
        except Exception as e:
            ConfigManager.console_print(f'Error saving debug recording: {e}')

    def set_ready(self):
        """Set the ready flag to indicate that actual recording should start."""
        ready_time = time.time()
        self.mutex.lock()
        self.ready_to_record = True
        self.mutex.unlock()
        ConfigManager.console_print("### 准备开始录音 - 现在开始说话 ###")
        # 添加时间戳以精确跟踪何时设置ready状态
        ConfigManager.console_print(f"### READY状态设置于: {ready_time:.6f} ###")
        # 不再显示预缓冲区信息，改为记录当前系统状态
        ConfigManager.console_print("### 正在使用所有音频数据，包括设置ready状态前的500毫秒 ###")
