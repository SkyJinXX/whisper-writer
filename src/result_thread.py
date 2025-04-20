import time
import traceback
import numpy as np
import sounddevice as sd
import tempfile
import wave
import webrtcvad
from PyQt5.QtCore import QThread, QMutex, pyqtSignal
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

            self.statusSignal.emit('recording')  # This will start the countdown in the UI
            ConfigManager.console_print('Initializing recording...')
            
            # The actual recording will start after the UI countdown completes
            # and the set_ready() method is called via the signal connection
            
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
        """
        Record audio from the microphone.
        Now with immediate recording regardless of mode, and proper buffering.

        :return: numpy array of audio data, or None if the recording is too short
        """
        # Test available sound devices
        try:
            devices = sd.query_devices()
            ConfigManager.console_print(f"Available audio devices:")
            for i, device in enumerate(devices):
                ConfigManager.console_print(f"  [{i}] {device['name']} (in: {device['max_input_channels']}, out: {device['max_output_channels']})")
            
            default_device = sd.query_devices(kind='input')
            ConfigManager.console_print(f"Default input device: {default_device['name']}")
        except Exception as e:
            ConfigManager.console_print(f"Error querying audio devices: {e}")
        
        recording_options = ConfigManager.get_config_section('recording_options')
        self.sample_rate = recording_options.get('sample_rate') or 16000
        sound_device = recording_options.get('sound_device')
        
        # If no specific device is selected, use the default input device
        if sound_device is None:
            try:
                default_device = sd.query_devices(kind='input')
                default_device_index = default_device['index']
                sound_device = default_device_index
                ConfigManager.console_print(f"Using default input device: {default_device['name']} (index: {default_device_index})")
            except Exception as e:
                ConfigManager.console_print(f"Error getting default device: {e}")
        else:
            ConfigManager.console_print(f"Using configured input device: {sound_device}")
        
        # Get recording mode
        recording_mode = recording_options.get('recording_mode') or 'continuous'
        ConfigManager.console_print(f"Recording mode: {recording_mode}")
        
        # For testing or simpler modes, use direct synchronous recording
        if recording_mode in ('press_to_toggle', 'hold_to_record'):
            # Instead of a fixed time, use a dynamic approach with a buffer
            record_seconds = recording_options.get('max_recording_seconds') or 60.0
            ConfigManager.console_print(f"Using direct recording (max {record_seconds} seconds)...")
            
            # Create a buffer to hold audio data - we'll expand it if needed
            buffer_size = int(self.sample_rate * record_seconds)
            recording_data = np.zeros((buffer_size,), dtype=np.int16)
            frames_recorded = 0
            
            # Set up the callback to continuously record audio
            def callback(indata, frames, time, status):
                nonlocal frames_recorded, recording_data, buffer_size
                if status:
                    ConfigManager.console_print(f"Audio callback status: {status}")
                
                # Only record if we're in ready state
                if not self.ready_to_record:
                    return
                    
                # If we're about to exceed the buffer, expand it
                if frames_recorded + frames > buffer_size:
                    ConfigManager.console_print(f"Expanding recording buffer (current: {buffer_size} frames)")
                    # Double the buffer size
                    new_buffer = np.zeros((buffer_size * 2,), dtype=np.int16)
                    new_buffer[:buffer_size] = recording_data
                    recording_data = new_buffer
                    buffer_size *= 2
                
                # Add the new audio data
                recording_data[frames_recorded:frames_recorded+frames] = indata[:, 0]
                frames_recorded += frames
            
            # Start the input stream
            ConfigManager.console_print(f"Starting audio recording with device: {sound_device}")
            try:
                with sd.InputStream(
                    callback=callback,
                    samplerate=self.sample_rate,
                    channels=1,
                    dtype='int16',
                    device=sound_device
                ):
                    while self.is_recording and self.is_running and frames_recorded < buffer_size:
                        sd.sleep(100)  # Sleep and process audio in chunks
                
                # Trim the recording to the actual length
                audio_data = recording_data[:frames_recorded]
                
                # Report recording stats
                duration = len(audio_data) / self.sample_rate
                ConfigManager.console_print(f"Recording finished. Duration: {duration:.2f} seconds, Frames: {frames_recorded}")
                
                # Save the recording for debugging
                # self._save_debug_recording(audio_data, "direct")
                
                # Check if the recording is long enough
                min_duration_ms = recording_options.get('min_duration') or 100
                if (duration * 1000) < min_duration_ms:
                    ConfigManager.console_print(f"Recording too short (less than {min_duration_ms}ms), discarding")
                    return None
                    
                return audio_data
                
            except Exception as e:
                ConfigManager.console_print(f"Error during direct recording: {str(e)}")
                return None
                
        # For modes with voice activity detection, use a more complex approach
        # Always record everything but use VAD to decide when to stop
        ConfigManager.console_print("Using VAD recording mode with continuous buffering")
        
        # Initialize VAD parameters
        frame_duration_ms = 30  # WebRTC VAD frame duration in ms
        frame_size = int(self.sample_rate * (frame_duration_ms / 1000.0))
        silence_duration_ms = recording_options.get('silence_duration') or 900
        silence_frames = int(silence_duration_ms / frame_duration_ms)
        vad = webrtcvad.Vad(2)  # Aggressiveness level 2 (scale 0-3)
        
        # Initialize recording state
        speech_detected = False
        recording_data = []  # Will store all audio frames
        silent_frame_count = 0
        frames_processed = 0
        recording_start_time = time.time()
        
        # Use a threading event to synchronize with the callback
        data_ready = Event()
        
        # Initialize audio buffer with a reasonable size
        audio_buffer = deque(maxlen=frame_size)
        
        # The callback that receives audio data
        def audio_callback(indata, frames, time, status):
            if status:
                ConfigManager.console_print(f"Audio callback status: {status}")
            # Add the incoming audio data to our buffer
            audio_buffer.extend(indata[:, 0])
            data_ready.set()  # Signal that data is ready
        
        ConfigManager.console_print(f"Starting VAD recording with device: {sound_device}")
        try:
            with sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype='int16',
                blocksize=frame_size,
                device=sound_device,
                callback=audio_callback
            ):
                # Main recording loop
                while self.is_running and self.is_recording:
                    # Wait for data from the callback
                    data_ready.wait()
                    data_ready.clear()
                    
                    # Skip if we don't have enough data
                    if len(audio_buffer) < frame_size:
                        continue
                    
                    # Get a frame of audio data
                    frame = np.array(list(audio_buffer), dtype=np.int16)
                    audio_buffer.clear()
                    
                    # Always add the frame to our recording
                    recording_data.extend(frame)
                    frames_processed += 1
                    
                    # Check if this frame contains speech
                    try:
                        is_speech = vad.is_speech(frame.tobytes(), self.sample_rate)
                        
                        # If we detect speech, mark it
                        if is_speech:
                            silent_frame_count = 0
                            if not speech_detected:
                                speech_detected = True
                                speech_time = time.time()
                                ConfigManager.console_print(f"Speech detected after {speech_time - recording_start_time:.3f}s (frame {frames_processed})")
                        else:
                            # Count silent frames after speech is detected
                            if speech_detected:
                                silent_frame_count += 1
                                
                        # If we've had enough silence after speech, stop recording
                        if speech_detected and silent_frame_count >= silence_frames:
                            ConfigManager.console_print(f"Stopping after {silent_frame_count} silent frames")
                            break
                            
                    except Exception as e:
                        ConfigManager.console_print(f"Error in VAD processing: {e}")
                        # Continue recording even if VAD fails
                    
                    # Safety timeout - stop after 30 seconds if no speech detected
                    elapsed_time = time.time() - recording_start_time
                    if elapsed_time > 30 and not speech_detected:
                        ConfigManager.console_print("Recording timeout (30s) with no speech")
                        break
            
            # Convert the collected frames to a numpy array
            audio_data = np.array(recording_data, dtype=np.int16)
            duration = len(audio_data) / self.sample_rate
            
            # Log recording statistics
            ConfigManager.console_print(f"VAD recording finished. Size: {audio_data.size} samples, Duration: {duration:.2f}s, Frames: {frames_processed}")
            
            # Save for debugging
            # self._save_debug_recording(audio_data, "vad")
            
            # Check if recording meets minimum duration
            min_duration_ms = recording_options.get('min_duration') or 100
            if (duration * 1000) < min_duration_ms:
                ConfigManager.console_print(f"Recording too short (less than {min_duration_ms}ms), discarding")
                return None
            
            # For continuous/VAD modes, only return if speech was detected
            if recording_mode == 'voice_activity_detection' and not speech_detected:
                ConfigManager.console_print("No speech detected, discarding recording")
                return None
                
            return audio_data
            
        except Exception as e:
            ConfigManager.console_print(f"Error during VAD recording: {str(e)}")
            import traceback
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
        self.mutex.lock()
        self.ready_to_record = True
        self.mutex.unlock()
        ConfigManager.console_print("### READY TO RECORD - START SPEAKING NOW ###")
