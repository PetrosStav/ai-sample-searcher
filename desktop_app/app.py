import os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
# Suppress ffmpeg/libav warnings for audio decoding (e.g., vorbis timestamp warnings)
os.environ["PYTHONWARNINGS"] = "ignore"
os.environ["AV_LOG_LEVEL"] = "error"  # Only show critical ffmpeg errors
import sys
import warnings
warnings.filterwarnings('ignore', category=UserWarning)
warnings.filterwarnings('ignore', category=FutureWarning)
from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QLineEdit, 
                             QListWidget, QMainWindow, QListWidgetItem, QPushButton, 
                             QHBoxLayout, QFileDialog, QMessageBox, QProgressBar, QLabel,
                             QSpinBox, QDoubleSpinBox, QComboBox, QGroupBox, QCheckBox, QSlider,
                             QGridLayout, QFrame)
from PyQt6.QtCore import Qt, QMimeData, QUrl, QThread, pyqtSignal, QSize, QTimer
from PyQt6.QtGui import QDrag, QShortcut, QKeySequence, QIcon, QPainter, QPen, QColor
from PyQt6.QtCore import QRect
import re
import os
import time
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from searcher import SampleSearcher
from indexer import IndexerBackend
import ctypes
import wave
import json
from mutagen import File as MutagenFile

myappid = 'mycompany.myproduct.subproduct.version'
try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
except (ImportError, AttributeError):
    # AttributeError occurs in WSL/Linux where windll doesn't exist
    pass

class IndexingWorker(QThread):
    finished = pyqtSignal(int)
    progress = pyqtSignal(int) 
    status_update = pyqtSignal(str)

    def __init__(self, folder_path, db_path=None):
        super().__init__()
        self.folder_path = folder_path
        self.db_path = db_path
    
    def run(self):
        self.status_update.emit("Loading Indexer models...")
        indexer = IndexerBackend(db_path=self.db_path) if self.db_path else IndexerBackend()
        
        # Inform user which engine is being used
        engine_name = indexer.get_audio_engine()
        self.status_update.emit(f"Using {engine_name} engine for BPM/Key detection")

        def callback_bridge(percentage):
            self.progress.emit(percentage)

        self.status_update.emit(f"Starting indexing on {self.folder_path}")
        try:
            count = indexer.run_indexing(self.folder_path, progress_callback=callback_bridge)
        except Exception as e:
            self.status_update.emit(f"FATAL INDEXING ERROR: {e}")
            count = 0
        self.finished.emit(count)

class BPMReanalysisWorker(QThread):
    finished = pyqtSignal(int)
    progress = pyqtSignal(int) 
    status_update = pyqtSignal(str)

    def __init__(self, force_reanalysis=False, db_path=None):
        super().__init__()
        self.force_reanalysis = force_reanalysis
        self.db_path = db_path
    
    def run(self):
        self.status_update.emit("Loading models for BPM analysis...")
        indexer = IndexerBackend(db_path=self.db_path) if self.db_path else IndexerBackend()
        
        # Inform user which engine is being used
        engine_name = indexer.get_audio_engine()
        self.status_update.emit(f"Using {engine_name} engine for BPM/Key detection")

        self.status_update.emit("Fetching samples from database...")
        try:
            all_samples = indexer.collection.get()
            sample_ids = all_samples.get('ids', [])
            metadatas = all_samples.get('metadatas', [])
            
            # Filter samples based on force reanalysis flag
            samples_to_analyze = []
            for i, metadata in enumerate(metadatas):
                if self.force_reanalysis or metadata.get('bpm', 0) == 0 or not metadata.get('key', ''):
                    samples_to_analyze.append((sample_ids[i], metadata))
            
            total = len(samples_to_analyze)
            if self.force_reanalysis:
                self.status_update.emit(f"Force reanalyzing BPM and Key for {total} samples...")
            else:
                self.status_update.emit(f"Found {total} samples without BPM/Key. Analyzing...")
            
            if total == 0:
                self.finished.emit(0)
                return
            
            updated = 0
            batch_updates = []  # Collect updates for batch processing
            
            for i, (file_path, metadata) in enumerate(samples_to_analyze):
                try:
                    # Get BPM and Key in one optimized call
                    bpm, key = indexer.get_bpm_and_key(file_path)
                    
                    updated_something = False
                    if bpm is not None and bpm > 0:
                        metadata['bpm'] = bpm
                        updated_something = True
                    if key is not None:
                        metadata['key'] = key
                        updated_something = True
                    
                    # Track which analysis engine was used
                    metadata['analysis_engine'] = indexer.get_audio_engine().lower()
                    
                    if updated_something:
                        batch_updates.append((file_path, metadata))
                        updated += 1
                        
                        # Batch update every 50 samples for efficiency
                        if len(batch_updates) >= 50:
                            ids = [item[0] for item in batch_updates]
                            metas = [item[1] for item in batch_updates]
                            indexer.collection.update(ids=ids, metadatas=metas)
                            batch_updates = []
                except Exception as e:
                    print(f"Error analyzing {file_path}: {e}")
                
                # Update progress
                progress_pct = int(((i + 1) / total) * 100)
                self.progress.emit(progress_pct)
            
            # Update any remaining samples in the batch
            if batch_updates:
                ids = [item[0] for item in batch_updates]
                metas = [item[1] for item in batch_updates]
                indexer.collection.update(ids=ids, metadatas=metas)
            
            self.status_update.emit(f"Analysis complete! Updated {updated} samples.")
            self.finished.emit(updated)
        except Exception as e:
            self.status_update.emit(f"FATAL ERROR: {e}")
            self.finished.emit(0)

class EssentiaWSLWorker(QThread):
    finished = pyqtSignal(str)
    status_update = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, db_path=None, force_reanalysis=False):
        super().__init__()
        self.db_path = db_path or "./sample_db"
        self.force_reanalysis = force_reanalysis
    
    def run(self):
        import subprocess
        
        self.status_update.emit("Starting Essentia analysis via WSL...")
        
        # Convert Windows path to WSL path
        wsl_db_path = self.db_path.replace("\\", "/")
        if len(wsl_db_path) >= 2 and wsl_db_path[1] == ':':
            drive_letter = wsl_db_path[0].lower()
            rest_path = wsl_db_path[2:]
            wsl_db_path = f"/mnt/{drive_letter}{rest_path}"
        
        # Get the script path in WSL format
        script_dir = os.path.dirname(os.path.abspath(__file__))
        script_path = os.path.join(script_dir, "analyze_essentia_wsl.py")
        wsl_script_path = script_path.replace("\\", "/")
        if len(wsl_script_path) >= 2 and wsl_script_path[1] == ':':
            drive_letter = wsl_script_path[0].lower()
            rest_path = wsl_script_path[2:]
            wsl_script_path = f"/mnt/{drive_letter}{rest_path}"
             
        # Convert env_wsl path to WSL format (it's in the parent directory)
        project_root = os.path.dirname(script_dir)
        env_path = os.path.join(project_root, "env_wsl")
        wsl_env_path = env_path.replace("\\", "/")
        if len(wsl_env_path) >= 2 and wsl_env_path[1] == ':':
            drive_letter = wsl_env_path[0].lower()
            rest_path = wsl_env_path[2:]
            wsl_env_path = f"/mnt/{drive_letter}{rest_path}"
        
        force_flag = " --force" if self.force_reanalysis else ""
        
        # Try both path-based and name-based activation
        # Use -ic for interactive shell to load conda initialization
        cmd = [
            "wsl",
            "bash", "-ic",
            f"conda activate {wsl_env_path} 2>/dev/null || conda activate env_wsl && "
            f"python {wsl_script_path} --db-path {wsl_db_path}{force_flag}"
        ]
        
        self.status_update.emit(f"Database path (WSL): {wsl_db_path}")
        self.status_update.emit(f"Script path (WSL): {wsl_script_path}")
        self.status_update.emit(f"Checking WSL and conda environment...")
        
        try:
            # First verify WSL is accessible
            test_wsl = subprocess.run(["wsl", "echo", "test"], 
                                     capture_output=True, text=True, encoding='utf-8', timeout=5)
            if test_wsl.returncode != 0:
                self.error.emit("WSL is not responding. Make sure WSL is installed and running.")
                return
            
            self.status_update.emit("✓ WSL is accessible")
            
            # Check if conda is available (use interactive shell)
            self.status_update.emit("Checking if conda is available in WSL...")
            check_conda = subprocess.run(
                ["wsl", "bash", "-ic", "which conda"],
                capture_output=True, text=True, encoding='utf-8', timeout=10
            )
            
            if check_conda.returncode != 0:
                self.error.emit(
                    "Conda is not available in WSL.\n\n"
                    "Make sure conda is installed in your WSL distribution.\n\n"
                    "If conda is installed but not found, you may need to:\n"
                    "1. Run 'conda init bash' in WSL\n"
                    "2. Restart WSL: wsl --shutdown"
                )
                return
            
            self.status_update.emit(f"✓ Conda found at: {check_conda.stdout.strip()}")
            
            # Check if conda env exists - try both path and name
            self.status_update.emit(f"Checking for conda environment at: {wsl_env_path}")
            check_env = subprocess.run(
                ["wsl", "bash", "-ic", 
                 f"conda activate {wsl_env_path} 2>/dev/null || conda activate env_wsl && python --version"],
                capture_output=True, text=True, encoding='utf-8', timeout=15
            )
            
            if check_env.returncode != 0:
                error_details = check_env.stdout + check_env.stderr
                self.error.emit(
                    f"Could not activate conda environment in WSL.\n\n"
                    f"Tried:\n"
                    f"  - Path: {wsl_env_path}\n"
                    f"  - Name: env_wsl\n\n"
                    f"Error: {error_details}\n\n"
                    f"Please create the environment:\n"
                    f"  wsl\n"
                    f"  cd {wsl_env_path.rsplit('/', 1)[0]}\n"
                    f"  conda create -p ./env_wsl python=3.10\n"
                    f"  conda activate ./env_wsl\n"
                    f"  pip install essentia-tensorflow chromadb numpy tqdm"
                )
                return
            
            self.status_update.emit(f"✓ Conda environment is working: {check_env.stdout.strip()}")
            self.status_update.emit("Starting analysis...")
            
            # Run the WSL command
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='replace',
                bufsize=1,
                universal_newlines=True
            )
            
            # Stream output
            output_lines = []
            for line in process.stdout:
                line = line.rstrip()
                if line:
                    output_lines.append(line)
                    self.status_update.emit(line)
                    print(f"[WSL] {line}")  # Also print to console
            
            process.wait()
            
            if process.returncode == 0:
                self.finished.emit("Essentia analysis completed successfully!")
            else:
                # Capture all output for error message
                error_output = "\n".join(output_lines[-20:]) if output_lines else "No output"
                full_cmd = cmd[-1]  # Get the bash command
                self.error.emit(
                    f"WSL process exited with code {process.returncode}\n\n"
                    f"Command:\n{full_cmd}\n\n"
                    f"Last output:\n{error_output}\n\n"
                    f"Check the Python terminal for full details."
                )
        
        except FileNotFoundError:
            self.error.emit("WSL not found! Make sure WSL is installed and accessible.")
        except Exception as e:
            self.error.emit(f"Error running WSL analysis: {e}")

def get_similarity_color(similarity_percent):
    """Returns a color based on similarity percentage using a pleasant gradient."""
    if similarity_percent >= 85:
        return "#00e676"  # Bright green
    elif similarity_percent >= 70:
        return "#69f0ae"  # Light green
    elif similarity_percent >= 55:
        return "#ffd740"  # Amber/yellow
    elif similarity_percent >= 40:
        return "#ffab40"  # Orange
    elif similarity_percent >= 25:
        return "#ff6e40"  # Deep orange
    else:
        return "#ff5252"  # Red

def get_gradient_style(similarity_percent):
    """Returns gradient CSS based on similarity percentage."""
    color = get_similarity_color(similarity_percent)
    # Create darker version for gradient end
    qcolor = QColor(color)
    darker = qcolor.darker(130).name()
    return f"""
        QProgressBar {{
            border: 1px solid #333;
            border-radius: 4px;
            background-color: #1e1e1e;
        }}
        QProgressBar::chunk {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                      stop:0 {color}, stop:1 {darker});
            border-radius: 3px;
        }}
    """

class ResultWidget(QWidget):
    """Custom widget to display filename, similarity score, and progress bar"""
    def __init__(self, filename, similarity_percent, bpm=None, key=None, analysis_engine=None, parent=None):
        super().__init__(parent)
        self.similarity_percent = similarity_percent
        
        layout = QVBoxLayout()
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(3)
        
        # Top row: Filename, Key, and BPM
        top_row = QHBoxLayout()
        top_row.setSpacing(10)
        
        # Filename label
        filename_label = QLabel(filename)
        filename_label.setStyleSheet("color: #dddddd; font-size: 13px; font-weight: bold;")
        top_row.addWidget(filename_label, 1)
        
        # Analysis engine badge (if available)
        if analysis_engine:
            engine_color = "#4a9eff" if analysis_engine == 'essentia' else "#ffaa44"
            engine_label = QLabel(f"🔬 {analysis_engine.title()}")
            engine_label.setStyleSheet(f"""
                color: {engine_color};
                font-size: 9px;
                font-weight: bold;
                background-color: #2a2a2a;
                padding: 2px 6px;
                border-radius: 3px;
                border: 1px solid {engine_color};
            """)
            engine_label.setMinimumWidth(55)
            top_row.addWidget(engine_label)
        
        # Key badge (if available)
        if key:
            key_label = QLabel(f"🎹 {key}")
            key_label.setStyleSheet("""
                color: #ffb347;
                font-size: 11px;
                font-weight: bold;
                background-color: #3a2a1a;
                padding: 3px 10px;
                border-radius: 4px;
                border: 1px solid #cc8833;
            """)
            key_label.setMinimumWidth(65)
            top_row.addWidget(key_label)
        
        # BPM badge (if available)
        if bpm and bpm > 0:
            bpm_label = QLabel(f"♪ {bpm:.0f} BPM")
            bpm_label.setStyleSheet("""
                color: #00e6ff;
                font-size: 11px;
                font-weight: bold;
                background-color: #0a3540;
                padding: 3px 10px;
                border-radius: 4px;
                border: 1px solid #00a8cc;
            """)
            bpm_label.setMinimumWidth(70)
            top_row.addWidget(bpm_label)
        
        layout.addLayout(top_row)
        
        # Similarity info row
        info_layout = QHBoxLayout()
        info_layout.setSpacing(10)
        
        # Similarity percentage label with color
        color = get_similarity_color(similarity_percent)
        score_label = QLabel(f"{similarity_percent:.1f}%")
        score_label.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: bold;")
        score_label.setMinimumWidth(50)
        info_layout.addWidget(score_label)
        
        # Progress bar with gradient
        progress = QProgressBar()
        progress.setRange(0, 100)
        progress.setValue(int(similarity_percent))
        progress.setTextVisible(False)
        progress.setMaximumHeight(8)
        progress.setStyleSheet(get_gradient_style(similarity_percent))
        info_layout.addWidget(progress, 1)
        
        layout.addLayout(info_layout)
        self.setLayout(layout)

class SampleList(QListWidget):
    def __init__(self):
        super().__init__()
        self.setDragEnabled(True)

    def wsl_to_windows_path(self, wsl_path):
        if wsl_path.startswith("/mnt/"):
            parts = wsl_path.split('/')
            drive_letter = parts[2]    # 'c'
            rest_of_path = "\\".join(parts[3:])
            windows_path = f"{drive_letter.upper()}:\\{rest_of_path}"
            return windows_path
        return wsl_path
    
    def startDrag(self, supportedActions):
        item = self.currentItem()
        if not item:
            return   
        
        raw_path = item.data(Qt.ItemDataRole.UserRole)
        print(f"Ruta Linux: {raw_path}")
        win_path_backslashes = self.wsl_to_windows_path(raw_path)
        win_path_forward_slashes = win_path_backslashes.replace("\\", "/")
        final_url_string = f"file:///{win_path_forward_slashes}"
        url = QUrl(final_url_string)
        print(f"URL Final: {url.toString()}")

        mime_data  = QMimeData()
        mime_data.setUrls([url])

        drag = QDrag(self)
        drag.setMimeData(mime_data)
        drag.exec(Qt.DropAction.CopyAction)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        
        # Initial window title (will be updated after engine loads)
        self.base_title = "AI Sample Searcher"
        self.setWindowTitle(self.base_title)
        
        # Database path selection and config
        self.config_file = os.path.join(os.path.dirname(__file__), 'db_config.json')
        self.load_config()
        from indexer import DB_PATH as DEFAULT_DB_PATH
        self.current_db_path = self.config.get('last_used', DEFAULT_DB_PATH)
        
        basedir = os.path.dirname(__file__)
        icon_path = os.path.join(basedir, "icon.ico")
        self.setWindowIcon(QIcon(icon_path))
        self.resize(1100, 800)
        self.setMinimumSize(1100, 800)  # Minimum size to keep all elements visible
        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint)
        
        # Progress tracking for time estimation
        self.progress_start_time = None
        self.last_progress_value = 0
        self.last_progress_message = ""  # Cache the last progress message

        try: 
            self.engine = SampleSearcher(db_path=self.current_db_path)
            print("DB Loaded succesfuly")
            db_exists = True
            # Update window title with audio engine info
            self._update_window_title_with_engine()
        except FileNotFoundError:
            print("No DB found. Waiting for user to index.")
            self.engine = None
            db_exists = False

        #Audio Setup
        self.audio_ouput = QAudioOutput()
        self.player = QMediaPlayer()
        self.player.setAudioOutput(self.audio_ouput)
        self.audio_ouput.setVolume(0.8)
        self.current_playing_filename = ""
        self.slider_is_pressed = False
        
        # Connect player signals
        self.player.positionChanged.connect(self.update_playback_position)
        self.player.durationChanged.connect(self.update_playback_duration)
        self.player.playbackStateChanged.connect(self.handle_playback_state_changed)

        main_layout = QVBoxLayout()

        #Indexing Button and Configuration
        self.top_bar = QHBoxLayout()
        self.btn_index = QPushButton("📂 Add Samples Folder")
        self.btn_index.clicked.connect(self.open_folder_dialog)
        self.top_bar.addWidget(self.btn_index)
        
        self.btn_reanalyze = QPushButton("🔄 Librosa BPM/Key")
        self.btn_reanalyze.clicked.connect(self.start_bpm_reanalysis)
        self.btn_reanalyze.setToolTip("Analyze BPM and musical key using librosa (fast, Windows compatible)")
        self.btn_reanalyze.setStyleSheet("""
            QPushButton {
                background-color: #555;
                color: white;
                border: none;
                padding: 10px 15px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #666;
            }
            QPushButton:pressed {
                background-color: #444;
            }
            QPushButton:disabled {
                background-color: #333;
                color: #666;
            }
        """)
        if not db_exists:
            self.btn_reanalyze.setEnabled(False)
        self.top_bar.addWidget(self.btn_reanalyze)
        
        # Essentia WSL button
        self.btn_essentia_wsl = QPushButton("🎼 Essentia (WSL)")
        self.btn_essentia_wsl.clicked.connect(self.start_essentia_wsl_analysis)
        self.btn_essentia_wsl.setToolTip("Analyze BPM and Key using Essentia in WSL (more accurate, requires setup)")
        self.btn_essentia_wsl.setStyleSheet("""
            QPushButton {
                background-color: #665;
                color: white;
                border: none;
                padding: 10px 15px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #776;
            }
            QPushButton:pressed {
                background-color: #554;
            }
            QPushButton:disabled {
                background-color: #333;
                color: #666;
            }
        """)
        if not db_exists:
            self.btn_essentia_wsl.setEnabled(False)
        self.top_bar.addWidget(self.btn_essentia_wsl)
        
        # Single Force checkbox that applies to both analysis buttons
        self.force_reanalysis_checkbox = QCheckBox("Force Reanalyze All")
        self.force_reanalysis_checkbox.setToolTip("Force reanalysis of ALL samples (applies to either analysis button)")
        self.force_reanalysis_checkbox.setStyleSheet("""
            QCheckBox {
                color: #cccccc;
                font-size: 11px;
                spacing: 5px;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border: 1px solid #555;
                border-radius: 3px;
                background-color: #3c3c3c;
            }
            QCheckBox::indicator:checked {
                background-color: #ff6b35;
                border-color: #ff6b35;
            }
            QCheckBox::indicator:hover {
                border-color: #777;
            }
        """)
        if not db_exists:
            self.force_reanalysis_checkbox.setEnabled(False)
        self.top_bar.addWidget(self.force_reanalysis_checkbox)
        
        # Database selector
        self.top_bar.addWidget(QLabel("|"))  # Visual separator
        db_label = QLabel("Database:")
        db_label.setStyleSheet("color: #cccccc; font-size: 11px; padding-left: 10px; padding-right: 5px;")
        self.top_bar.addWidget(db_label)
        
        self.db_selector = QComboBox()
        self.populate_database_selector()
        self.db_selector.currentIndexChanged.connect(self.on_database_changed)
        self.db_selector.setToolTip("Select database to use for indexing and searching")
        self.db_selector.setStyleSheet("""
            QComboBox {
                background-color: #3c3c3c;
                border: 1px solid #555;
                border-radius: 3px;
                padding: 5px;
                color: #fff;
                min-width: 180px;
            }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView {
                background-color: #2c2c2c;
                color: #fff;
                selection-background-color: #006bb3;
            }
        """)
        self.top_bar.addWidget(self.db_selector)
        
        # Results count configuration
        self.top_bar.addStretch()
        results_label = QLabel("Results:")
        results_label.setStyleSheet("color: #cccccc; font-size: 11px; padding-right: 5px;")
        self.top_bar.addWidget(results_label)
        
        self.results_spinbox = QSpinBox()
        self.results_spinbox.setRange(1, 100)
        self.results_spinbox.setValue(15)  # Default value
        self.results_spinbox.setSuffix(" samples")
        self.results_spinbox.setToolTip("Number of results to display (type or use arrows)")
        self.results_spinbox.setKeyboardTracking(True)
        self.results_spinbox.setWrapping(False)
        self.results_spinbox.setButtonSymbols(QSpinBox.ButtonSymbols.UpDownArrows)
        self.results_spinbox.setStyleSheet("""
            QSpinBox {
                background-color: #3c3c3c;
                border: 1px solid #555;
                border-radius: 4px;
                color: #ffffff;
                padding: 5px;
                min-width: 100px;
            }
            QSpinBox::up-button, QSpinBox::down-button {
                background-color: #555;
                border: none;
                width: 16px;
            }
            QSpinBox::up-button:hover, QSpinBox::down-button:hover {
                background-color: #666;
            }
        """)
        self.top_bar.addWidget(self.results_spinbox)
        
        main_layout.addLayout(self.top_bar)

        #Progress Bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.hide()
        main_layout.addWidget(self.progress_bar)

        self.status_label = QLabel("Initializing....")
        self.status_label.setStyleSheet("font-size: 10pt; color: #555; padding: 8px 0px; margin-bottom: 15px;")
        main_layout.addWidget(self.status_label)

        # Filters Panel
        self.create_filters_panel(main_layout)

        #Search Bar
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Describe Sound: ")
        self.search_bar.returnPressed.connect(self.do_search)

        if not db_exists:
            self.search_bar.setPlaceholderText("Please index a folder first to search...")
            self.search_bar.setEnabled(False)
            self.status_label.setText("Ready to index. Please select a folder.")
        else:
            self.search_bar.setPlaceholderText("Describe Sound: ")
            self.status_label.setText("Engine Loaded. Ready.")
        main_layout.addWidget(self.search_bar)

        #Result List
        self.result_list = SampleList()
        self.result_list.itemClicked.connect(self.play_preview)
        main_layout.addWidget(self.result_list)
        
        # Playback Control Panel
        self.create_playback_panel(main_layout)

        self.stop_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        self.stop_shortcut.activated.connect(self.toggle_playback)

        container = QWidget()
        container.setLayout(main_layout)
        self.setCentralWidget(container)
    
    def _update_window_title_with_engine(self):
        """Update window title to show current database"""
        try:
            db_name = os.path.basename(self.current_db_path)
            self.setWindowTitle(f"{self.base_title} - [{db_name}]")
        except Exception as e:
            print(f"Could not update title: {e}")
            self.setWindowTitle(self.base_title)
    
    def load_config(self):
        """Load database configuration from JSON file"""
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r') as f:
                    self.config = json.load(f)
            else:
                # Create default config
                self.config = {
                    'databases': ['./sample_db'],
                    'last_used': './sample_db'
                }
                self.save_config()
        except Exception as e:
            print(f"Error loading config: {e}")
            self.config = {
                'databases': ['./sample_db'],
                'last_used': './sample_db'
            }
    
    def save_config(self):
        """Save database configuration to JSON file"""
        try:
            with open(self.config_file, 'w') as f:
                json.dump(self.config, f, indent=2)
        except Exception as e:
            print(f"Error saving config: {e}")
    
    def add_database_to_config(self, db_path):
        """Add a new database path to the config if not already there"""
        if db_path not in self.config['databases']:
            self.config['databases'].append(db_path)
            self.save_config()
    
    def populate_database_selector(self):
        """Populate database selector from config"""
        self.db_selector.clear()
        
        # Add all databases from config
        for db_path in self.config.get('databases', []):
            # Show relative path with db name
            display_name = os.path.basename(db_path) if db_path else db_path
            if not display_name:
                display_name = db_path
            self.db_selector.addItem(display_name, db_path)
        
        # Add "Browse..." option
        self.db_selector.addItem("Browse...", None)
        
        # Select current database
        for i in range(self.db_selector.count()):
            if self.db_selector.itemData(i) == self.current_db_path:
                self.db_selector.setCurrentIndex(i)
                break
    
    def on_database_changed(self, index):
        """Handle database selection change"""
        selected_data = self.db_selector.itemData(index)
        
        # If "Browse..." was selected
        if selected_data is None:
            custom_path = QFileDialog.getExistingDirectory(self, "Select Database Folder")
            if custom_path:
                new_db_path = custom_path
                # Try to load it first
                temp_current = self.current_db_path
                self.current_db_path = new_db_path
                
                try:
                    # Test if database is valid
                    test_engine = SampleSearcher(db_path=new_db_path)
                    # If successful, add to config and update selector
                    self.add_database_to_config(new_db_path)
                    self.config['last_used'] = new_db_path
                    self.save_config()
                    self.populate_database_selector()
                    self.reload_search_engine()
                except Exception as e:
                    # Failed to load, revert
                    self.current_db_path = temp_current
                    QMessageBox.warning(self, "Invalid Database", 
                                      f"Could not load database at:\n{new_db_path}\n\nError: {e}")
                    # Revert selector
                    for i in range(self.db_selector.count()):
                        if self.db_selector.itemData(i) == self.current_db_path:
                            self.db_selector.setCurrentIndex(i)
                            break
            else:
                # User cancelled, revert to previous selection
                for i in range(self.db_selector.count()):
                    if self.db_selector.itemData(i) == self.current_db_path:
                        self.db_selector.setCurrentIndex(i)
                        break
        else:
            # Regular database selected
            new_db_path = selected_data
            if new_db_path != self.current_db_path:
                self.current_db_path = new_db_path
                self.config['last_used'] = new_db_path
                self.save_config()
                self.reload_search_engine()
    
    def reload_search_engine(self):
        """Reload the search engine with the selected database"""
        self.status_label.setText(f"Loading database: {self.current_db_path}")
        try:
            self.engine = SampleSearcher(db_path=self.current_db_path)
            self.search_bar.setEnabled(True)
            self.search_bar.setPlaceholderText("Describe Sound: ")
            self.btn_reanalyze.setEnabled(True)
            self.btn_essentia_wsl.setEnabled(True)
            self.force_reanalysis_checkbox.setEnabled(True)
            self.status_label.setText(f"Database loaded: {os.path.basename(self.current_db_path)}")
            print(f"Successfully loaded database: {self.current_db_path}")
            # Clear current results
            self.result_list.clear()
        except FileNotFoundError:
            self.engine = None
            self.search_bar.setEnabled(False)
            self.search_bar.setPlaceholderText("Please index a folder first...")
            self.btn_reanalyze.setEnabled(False)
            self.btn_essentia_wsl.setEnabled(False)
            self.force_reanalysis_checkbox.setEnabled(False)
            self.status_label.setText(f"No database found at: {self.current_db_path}")
            print(f"No database found at: {self.current_db_path}")
            QMessageBox.warning(self, "Database Not Found", 
                              f"No database found at:\n{self.current_db_path}\n\nPlease index a folder first.")
        except Exception as e:
            self.status_label.setText(f"Error loading database: {e}")
            print(f"Error loading database {self.current_db_path}: {e}")
            QMessageBox.critical(self, "Error", f"Failed to load database:\n{e}")

    def create_filters_panel(self, parent_layout):
        """Create collapsible filter panel"""
        self.filter_group = QGroupBox("🔍 Advanced Filters")
        self.filter_group.setCheckable(True)
        self.filter_group.setChecked(False)  # Collapsed by default
        self.filter_group.setStyleSheet("""
            QGroupBox {
                color: #cccccc;
                border: 1px solid #555;
                border-radius: 5px;
                margin-top: 25px;
                padding-top: 20px;
                font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 10px;
                padding: 0 5px;
                margin-top: 5px;
            }
            QGroupBox::indicator {
                width: 16px;
                height: 16px;
                border: 1px solid #555;
                border-radius: 3px;
                background-color: #3c3c3c;
                margin-top: 5px;
            }
            QGroupBox::indicator:checked {
                background-color: #ff6b35;
                border-color: #ff6b35;
            }
            QGroupBox::indicator:hover {
                border-color: #777;
            }
        """)
        
        filter_layout = QVBoxLayout()
        filter_layout.setSpacing(12)
        
        # === TEXT FILTERS SECTION ===
        text_section = QHBoxLayout()
        text_section.setSpacing(15)
        
        # Include pattern
        include_box = QVBoxLayout()
        include_box.setSpacing(3)
        include_label = QLabel("Include Pattern")
        include_label.setStyleSheet("color: #88ccff; font-size: 10px; font-weight: bold;")
        include_box.addWidget(include_label)
        
        self.include_pattern = QLineEdit()
        self.include_pattern.setPlaceholderText("e.g., kick, snare (regex)")
        self.include_pattern.setStyleSheet("background-color: #3c3c3c; border: 1px solid #555; border-radius: 3px; padding: 6px; color: #fff;")
        self.include_pattern.returnPressed.connect(self.do_search)
        self.include_pattern.editingFinished.connect(self.do_search)
        include_box.addWidget(self.include_pattern)
        text_section.addLayout(include_box, 1)
        
        # Exclude pattern
        exclude_box = QVBoxLayout()
        exclude_box.setSpacing(3)
        exclude_label = QLabel("Exclude Pattern")
        exclude_label.setStyleSheet("color: #ff8888; font-size: 10px; font-weight: bold;")
        exclude_box.addWidget(exclude_label)
        
        self.exclude_pattern = QLineEdit()
        self.exclude_pattern.setPlaceholderText("e.g., loop, one-shot")
        self.exclude_pattern.setStyleSheet("background-color: #3c3c3c; border: 1px solid #555; border-radius: 3px; padding: 6px; color: #fff;")
        self.exclude_pattern.returnPressed.connect(self.do_search)
        self.exclude_pattern.editingFinished.connect(self.do_search)
        exclude_box.addWidget(self.exclude_pattern)
        text_section.addLayout(exclude_box, 1)
        
        filter_layout.addLayout(text_section)
        
        # Separator
        separator1 = QFrame()
        separator1.setFrameShape(QFrame.Shape.HLine)
        separator1.setStyleSheet("background-color: #444; margin: 5px 0px;")
        filter_layout.addWidget(separator1)
        
        # === RANGE FILTERS SECTION ===
        grid_layout = QGridLayout()
        grid_layout.setHorizontalSpacing(15)
        grid_layout.setVerticalSpacing(8)
        
        # Column 1: Similarity
        similarity_label = QLabel("Similarity %")
        similarity_label.setStyleSheet("color: #aaddaa; font-size: 10px; font-weight: bold;")
        grid_layout.addWidget(similarity_label, 0, 0)
        
        similarity_range = QHBoxLayout()
        similarity_range.setSpacing(5)
        self.min_similarity = QDoubleSpinBox()
        self.min_similarity.setRange(0, 100)
        self.min_similarity.setValue(0)
        self.min_similarity.setSuffix("% min")
        self.min_similarity.setDecimals(1)
        self.min_similarity.setKeyboardTracking(True)
        self.min_similarity.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.UpDownArrows)
        self.min_similarity.setStyleSheet("""
            QDoubleSpinBox {
                background-color: #3c3c3c;
                border: 1px solid #555;
                border-radius: 3px;
                padding: 5px;
                color: #fff;
                min-width: 90px;
            }
            QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
                width: 20px;
                height: 14px;
                background-color: #555;
                border: none;
            }
            QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {
                background-color: #666;
            }
        """)
        self.min_similarity.editingFinished.connect(self.do_search)
        similarity_range.addWidget(self.min_similarity)
        
        self.max_similarity = QDoubleSpinBox()
        self.max_similarity.setRange(0, 100)
        self.max_similarity.setValue(100)
        self.max_similarity.setSuffix("% max")
        self.max_similarity.setDecimals(1)
        self.max_similarity.setKeyboardTracking(True)
        self.max_similarity.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.UpDownArrows)
        self.max_similarity.setStyleSheet("""
            QDoubleSpinBox {
                background-color: #3c3c3c;
                border: 1px solid #555;
                border-radius: 3px;
                padding: 5px;
                color: #fff;
                min-width: 90px;
            }
            QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
                width: 20px;
                height: 14px;
                background-color: #555;
                border: none;
            }
            QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {
                background-color: #666;
            }
        """)
        self.max_similarity.editingFinished.connect(self.do_search)
        similarity_range.addWidget(self.max_similarity)
        grid_layout.addLayout(similarity_range, 1, 0)
        
        # Column 2: BPM
        bpm_label = QLabel("BPM Range")
        bpm_label.setStyleSheet("color: #ffddaa; font-size: 10px; font-weight: bold;")
        grid_layout.addWidget(bpm_label, 0, 1)
        
        bpm_range = QHBoxLayout()
        bpm_range.setSpacing(5)
        self.min_bpm = QDoubleSpinBox()
        self.min_bpm.setRange(0, 300)
        self.min_bpm.setValue(0)
        self.min_bpm.setSuffix(" min")
        self.min_bpm.setDecimals(1)
        self.min_bpm.setKeyboardTracking(True)
        self.min_bpm.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.UpDownArrows)
        self.min_bpm.setStyleSheet("""
            QDoubleSpinBox {
                background-color: #3c3c3c;
                border: 1px solid #555;
                border-radius: 3px;
                padding: 5px;
                color: #fff;
                min-width: 90px;
            }
            QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
                width: 20px;
                height: 14px;
                background-color: #555;
                border: none;
            }
            QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {
                background-color: #666;
            }
        """)
        self.min_bpm.editingFinished.connect(self.do_search)
        bpm_range.addWidget(self.min_bpm)
        
        self.max_bpm = QDoubleSpinBox()
        self.max_bpm.setRange(0, 300)
        self.max_bpm.setValue(300)
        self.max_bpm.setSuffix(" max")
        self.max_bpm.setDecimals(1)
        self.max_bpm.setKeyboardTracking(True)
        self.max_bpm.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.UpDownArrows)
        self.max_bpm.setStyleSheet("""
            QDoubleSpinBox {
                background-color: #3c3c3c;
                border: 1px solid #555;
                border-radius: 3px;
                padding: 5px;
                color: #fff;
                min-width: 90px;
            }
            QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
                width: 20px;
                height: 14px;
                background-color: #555;
                border: none;
            }
            QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {
                background-color: #666;
            }
        """)
        self.max_bpm.editingFinished.connect(self.do_search)
        bpm_range.addWidget(self.max_bpm)
        grid_layout.addLayout(bpm_range, 1, 1)
        
        # Column 3: Duration
        duration_label = QLabel("Duration (sec)")
        duration_label.setStyleSheet("color: #ddaaff; font-size: 10px; font-weight: bold;")
        grid_layout.addWidget(duration_label, 0, 2)
        
        duration_range = QHBoxLayout()
        duration_range.setSpacing(5)
        self.min_duration = QDoubleSpinBox()
        self.min_duration.setRange(0, 999)
        self.min_duration.setValue(0)
        self.min_duration.setSuffix(" min")
        self.min_duration.setDecimals(1)
        self.min_duration.setKeyboardTracking(True)
        self.min_duration.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.UpDownArrows)
        self.min_duration.setStyleSheet("""
            QDoubleSpinBox {
                background-color: #3c3c3c;
                border: 1px solid #555;
                border-radius: 3px;
                padding: 5px;
                color: #fff;
                min-width: 90px;
            }
            QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
                width: 20px;
                height: 14px;
                background-color: #555;
                border: none;
            }
            QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {
                background-color: #666;
            }
        """)
        self.min_duration.editingFinished.connect(self.do_search)
        duration_range.addWidget(self.min_duration)
        
        self.max_duration = QDoubleSpinBox()
        self.max_duration.setRange(0, 999)
        self.max_duration.setValue(999)
        self.max_duration.setSuffix(" max")
        self.max_duration.setDecimals(1)
        self.max_duration.setKeyboardTracking(True)
        self.max_duration.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.UpDownArrows)
        self.max_duration.setStyleSheet("""
            QDoubleSpinBox {
                background-color: #3c3c3c;
                border: 1px solid #555;
                border-radius: 3px;
                padding: 5px;
                color: #fff;
                min-width: 90px;
            }
            QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
                width: 20px;
                height: 14px;
                background-color: #555;
                border: none;
            }
            QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {
                background-color: #666;
            }
        """)
        self.max_duration.editingFinished.connect(self.do_search)
        duration_range.addWidget(self.max_duration)
        grid_layout.addLayout(duration_range, 1, 2)
        
        filter_layout.addLayout(grid_layout)
        
        # Separator
        separator2 = QFrame()
        separator2.setFrameShape(QFrame.Shape.HLine)
        separator2.setStyleSheet("background-color: #444; margin: 5px 0px;")
        filter_layout.addWidget(separator2)
        
        # === MUSICAL PROPERTIES SECTION ===
        musical_section = QHBoxLayout()
        musical_section.setSpacing(15)
        
        # Key filter
        key_box = QVBoxLayout()
        key_box.setSpacing(3)
        key_label = QLabel("Musical Key")
        key_label.setStyleSheet("color: #ffb347; font-size: 10px; font-weight: bold;")
        key_box.addWidget(key_label)
        
        self.key_filter = QComboBox()
        keys = ["All", "C maj", "C min", "C# maj", "C# min", "D maj", "D min", "D# maj", "D# min",
                "E maj", "E min", "F maj", "F min", "F# maj", "F# min", "G maj", "G min",
                "G# maj", "G# min", "A maj", "A min", "A# maj", "A# min", "B maj", "B min"]
        self.key_filter.addItems(keys)
        self.key_filter.currentIndexChanged.connect(self.do_search)
        self.key_filter.setStyleSheet("""
            QComboBox {
                background-color: #3c3c3c;
                border: 1px solid #555;
                border-radius: 3px;
                padding: 6px;
                color: #fff;
                min-width: 120px;
            }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView {
                background-color: #2c2c2c;
                color: #fff;
                selection-background-color: #006bb3;
                padding: 4px;
            }
        """)
        key_box.addWidget(self.key_filter)
        musical_section.addLayout(key_box, 1)
        
        # Format filter
        format_box = QVBoxLayout()
        format_box.setSpacing(3)
        format_label = QLabel("Audio Format")
        format_label.setStyleSheet("color: #aaddff; font-size: 10px; font-weight: bold;")
        format_box.addWidget(format_label)
        
        self.format_combo = QComboBox()
        self.format_combo.addItems(["All", "wav", "mp3", "aif", "aiff", "flac", "ogg", "opus", "m4a", "aac"])
        self.format_combo.currentIndexChanged.connect(self.do_search)
        self.format_combo.setStyleSheet("""
            QComboBox {
                background-color: #3c3c3c;
                border: 1px solid #555;
                border-radius: 3px;
                padding: 6px;
                color: #fff;
                min-width: 120px;
            }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView {
                background-color: #2c2c2c;
                color: #fff;
                selection-background-color: #006bb3;
                padding: 4px;
            }
        """)
        format_box.addWidget(self.format_combo)
        musical_section.addLayout(format_box, 1)
        
        # Reset button
        reset_box = QVBoxLayout()
        reset_box.setSpacing(3)
        reset_spacer = QLabel("")  # Empty label for alignment
        reset_spacer.setStyleSheet("font-size: 10px;")
        reset_box.addWidget(reset_spacer)
        
        reset_btn = QPushButton("⟲ Reset All")
        reset_btn.clicked.connect(self.reset_filters)
        reset_btn.setStyleSheet("""
            QPushButton {
                background-color: #555;
                color: white;
                border: none;
                padding: 6px 20px;
                border-radius: 3px;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #677;
            }
            QPushButton:pressed {
                background-color: #444;
            }
        """)
        reset_box.addWidget(reset_btn)
        musical_section.addLayout(reset_box, 1)
        
        filter_layout.addLayout(musical_section)
        
        self.filter_group.setLayout(filter_layout)
        parent_layout.addWidget(self.filter_group)
    
    def create_playback_panel(self, parent_layout):
        """Create playback control panel at bottom"""
        playback_group = QGroupBox("🎵 Now Playing")
        playback_group.setStyleSheet("""
            QGroupBox {
                color: #cccccc;
                border: 1px solid #555;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 15px;
                font-weight: bold;
                background-color: #2b2b2b;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }
        """)
        
        playback_layout = QVBoxLayout()
        playback_layout.setSpacing(5)
        
        # Filename display
        self.playing_label = QLabel("No sample playing")
        self.playing_label.setStyleSheet("color: #aaa; font-size: 11px; font-weight: normal; padding: 0;")
        self.playing_label.setWordWrap(False)
        playback_layout.addWidget(self.playing_label)
        
        # Progress bar and time
        progress_row = QHBoxLayout()
        progress_row.setSpacing(10)
        
        # Current time
        self.time_label = QLabel("0:00")
        self.time_label.setStyleSheet("color: #ccc; font-size: 10px; min-width: 35px;")
        progress_row.addWidget(self.time_label)
        
        # Playback progress slider (seekable)
        self.playback_slider = QSlider(Qt.Orientation.Horizontal)
        self.playback_slider.setRange(0, 1000)
        self.playback_slider.setValue(0)
        self.playback_slider.setMaximumHeight(10)
        self.playback_slider.sliderPressed.connect(self.on_slider_pressed)
        self.playback_slider.sliderReleased.connect(self.on_slider_released)
        self.playback_slider.setStyleSheet("""
            QSlider::groove:horizontal {
                border: 1px solid #444;
                height: 8px;
                background-color: #1e1e1e;
                border-radius: 4px;
            }
            QSlider::handle:horizontal {
                background-color: #0099ff;
                border: 1px solid #006bb3;
                width: 16px;
                margin: -4px 0;
                border-radius: 8px;
            }
            QSlider::handle:horizontal:hover {
                background-color: #00bbff;
            }
            QSlider::sub-page:horizontal {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                          stop:0 #006bb3, stop:1 #0099ff);
                border-radius: 4px;
            }
        """)
        progress_row.addWidget(self.playback_slider, 1)
        
        # Total time
        self.duration_label = QLabel("0:00")
        self.duration_label.setStyleSheet("color: #ccc; font-size: 10px; min-width: 35px;")
        progress_row.addWidget(self.duration_label)
        
        playback_layout.addLayout(progress_row)
        
        # Control buttons
        controls_row = QHBoxLayout()
        controls_row.setSpacing(5)
        
        self.play_pause_btn = QPushButton("⏸ Pause")
        self.play_pause_btn.setEnabled(False)
        self.play_pause_btn.clicked.connect(self.toggle_playback)
        self.play_pause_btn.setMaximumWidth(80)
        self.play_pause_btn.setStyleSheet("""
            QPushButton {
                background-color: #555;
                color: white;
                border: none;
                padding: 5px 10px;
                border-radius: 3px;
                font-size: 11px;
            }
            QPushButton:hover:enabled {
                background-color: #666;
            }
            QPushButton:disabled {
                background-color: #333;
                color: #666;
            }
        """)
        controls_row.addWidget(self.play_pause_btn)
        
        self.stop_btn = QPushButton("⏹ Stop")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_audio)
        self.stop_btn.setMaximumWidth(80)
        self.stop_btn.setStyleSheet("""
            QPushButton {
                background-color: #555;
                color: white;
                border: none;
                padding: 5px 10px;
                border-radius: 3px;
                font-size: 11px;
            }
            QPushButton:hover:enabled {
                background-color: #666;
            }
            QPushButton:disabled {
                background-color: #333;
                color: #666;
            }
        """)
        controls_row.addWidget(self.stop_btn)
        
        # Volume control
        volume_label = QLabel("🔊")
        volume_label.setStyleSheet("color: #ccc; font-size: 12px;")
        controls_row.addWidget(volume_label)
        
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(80)
        self.volume_slider.setMaximumWidth(100)
        self.volume_slider.valueChanged.connect(self.change_volume)
        self.volume_slider.setStyleSheet("""
            QSlider::groove:horizontal {
                border: 1px solid #444;
                height: 6px;
                background-color: #1e1e1e;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background-color: #006bb3;
                border: 1px solid #005999;
                width: 14px;
                margin: -4px 0;
                border-radius: 7px;
            }
            QSlider::handle:horizontal:hover {
                background-color: #0088dd;
            }
            QSlider::sub-page:horizontal {
                background-color: #006bb3;
                border-radius: 3px;
            }
        """)
        controls_row.addWidget(self.volume_slider)
        
        self.volume_label_pct = QLabel("80%")
        self.volume_label_pct.setStyleSheet("color: #ccc; font-size: 10px; min-width: 30px;")
        controls_row.addWidget(self.volume_label_pct)
        controls_row.addStretch()
        
        playback_layout.addLayout(controls_row)
        
        playback_group.setLayout(playback_layout)
        parent_layout.addWidget(playback_group)
    
    def reset_filters(self):
        """Reset all filters to default values"""
        self.include_pattern.clear()
        self.exclude_pattern.clear()
        self.min_duration.setValue(0)
        self.max_duration.setValue(999)
        self.format_combo.setCurrentIndex(0)
        self.min_similarity.setValue(0)
        self.max_similarity.setValue(100)
        self.min_bpm.setValue(0)
        self.max_bpm.setValue(300)
        self.key_filter.setCurrentIndex(0)
        # Refresh search results if a search has been performed
        if self.result_list.count() > 0:
            self.do_search()

    def update_status_label(self, message):
        # If we're actively showing progress with time estimation, don't overwrite it
        # Only update status when not in progress mode or if progress hasn't been displayed yet
        if self.progress_start_time is None or self.last_progress_value == 0:
            self.status_label.setText(message)

    def open_folder_dialog(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Sample Folder")
        if folder:
            reply = QMessageBox.question(self, 'Index Folder', 
                                         f"Do you want to index:\n{folder}\n\nThis may take a while.",
                                         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                self.start_indexing(folder)

    def start_indexing(self, folder):
        # Only disable operation buttons to prevent concurrent operations
        self.btn_index.setEnabled(False)
        self.btn_reanalyze.setEnabled(False)
        self.btn_essentia_wsl.setEnabled(False)
        self.force_reanalysis_checkbox.setEnabled(False)
        # Keep search and database selector enabled for background operation
        # self.search_bar.setEnabled(False)  # REMOVED - allow searching
        # self.db_selector.setEnabled(False)  # REMOVED - allow db switching
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self.status_label.setText("⚙️ Indexing in background...")
        
        # Reset progress timing
        self.progress_start_time = None
        self.last_progress_value = 0
        self.last_progress_message = ""

        self.worker = IndexingWorker(folder, db_path=self.current_db_path)
        self.worker.finished.connect(self.indexing_finished)
        self.worker.progress.connect(self.update_progress_bar)
        self.worker.status_update.connect(self.update_status_label)
        self.worker.start()
    
    def start_bpm_reanalysis(self):
        force = self.force_reanalysis_checkbox.isChecked()
        
        if force:
            msg = "This will reanalyze BPM and Key for ALL samples in your database.\n\nThis process may take a long time depending on your library size.\n\nContinue?"
        else:
            msg = "This will analyze BPM and musical key for all samples that don't have this data yet.\n\nThis process may take several minutes depending on your library size.\n\nContinue?"
        
        reply = QMessageBox.question(self, 'Analyze BPM', msg,
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.No:
            return
        
        # Only disable operation buttons to prevent concurrent operations
        self.btn_index.setEnabled(False)
        self.btn_reanalyze.setEnabled(False)
        self.btn_essentia_wsl.setEnabled(False)
        self.force_reanalysis_checkbox.setEnabled(False)
        # Keep search and database selector enabled for background operation
        # self.search_bar.setEnabled(False)  # REMOVED - allow searching
        # self.db_selector.setEnabled(False)  # REMOVED - allow db switching
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self.status_label.setText("⚙️ BPM/Key analysis running in background...")
        
        # Reset progress timing
        self.progress_start_time = None
        self.last_progress_value = 0
        self.last_progress_message = ""

        self.bpm_worker = BPMReanalysisWorker(force_reanalysis=force, db_path=self.current_db_path)
        self.bpm_worker.finished.connect(self.bpm_reanalysis_finished)
        self.bpm_worker.progress.connect(self.update_progress_bar)
        self.bpm_worker.status_update.connect(self.update_status_label)
        self.bpm_worker.start()
    
    def bpm_reanalysis_finished(self, count):
        self.progress_bar.hide()
        # Reset progress timing
        self.progress_start_time = None
        self.last_progress_value = 0
        self.last_progress_message = ""
        
        self.btn_index.setEnabled(True)
        self.btn_reanalyze.setEnabled(True)
        self.btn_essentia_wsl.setEnabled(True)
        self.force_reanalysis_checkbox.setEnabled(True)
        # search_bar and db_selector were never disabled, so no need to re-enable
        
        if count > 0:
            QMessageBox.information(self, "Done", f"Analysis complete!\nUpdated {count} samples with BPM and Key data.")
            self.status_label.setText(f"✓ Analysis complete! Updated {count} samples.")
            # Refresh current search if there are results
            if self.result_list.count() > 0:
                self.do_search()
        else:
            QMessageBox.information(self, "Done", "All samples already have BPM and Key data or analysis found no rhythm/pitch.")
            self.status_label.setText("✓ All samples already have BPM and Key data.")
    
    def start_essentia_wsl_analysis(self):
        force = self.force_reanalysis_checkbox.isChecked()
        
        if force:
            msg = "This will run Essentia analysis via WSL for ALL samples in your database.\n\nThis process may take a long time depending on your library size.\n\nMake sure:\n1. WSL is installed and working\n2. Conda environment 'env_wsl' exists with essentia installed\n3. All audio files are accessible from WSL\n\nContinue?"
        else:
            msg = "This will run Essentia analysis via WSL for samples that:\n- Don't have BPM/Key data yet, OR\n- Were analyzed with librosa\n\nThis may take several minutes.\n\nMake sure:\n1. WSL is installed and working\n2. Conda environment 'env_wsl' exists with essentia installed\n3. All audio files are accessible from WSL\n\nContinue?"
        
        reply = QMessageBox.question(self, 'Essentia Analysis (WSL)', msg,
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.No:
            return
        
        # Only disable operation buttons to prevent concurrent operations
        self.btn_index.setEnabled(False)
        self.btn_reanalyze.setEnabled(False)
        self.btn_essentia_wsl.setEnabled(False)
        self.force_reanalysis_checkbox.setEnabled(False)
        # Keep search and database selector enabled for background operation
        # self.search_bar.setEnabled(False)  # REMOVED - allow searching
        # self.db_selector.setEnabled(False)  # REMOVED - allow db switching
        self.progress_bar.setRange(0, 0)  # Indeterminate progress
        self.progress_bar.show()
        self.status_label.setText("⚙️ Essentia analysis running in background via WSL...")
        
        self.essentia_worker = EssentiaWSLWorker(db_path=self.current_db_path, force_reanalysis=force)
        self.essentia_worker.finished.connect(self.essentia_wsl_finished)
        self.essentia_worker.status_update.connect(self.update_status_label)
        self.essentia_worker.error.connect(self.essentia_wsl_error)
        self.essentia_worker.start()
    
    def essentia_wsl_finished(self, message):
        self.progress_bar.hide()
        
        self.btn_index.setEnabled(True)
        self.btn_reanalyze.setEnabled(True)
        self.btn_essentia_wsl.setEnabled(True)
        self.force_reanalysis_checkbox.setEnabled(True)
        # search_bar and db_selector were never disabled, so no need to re-enable
        
        QMessageBox.information(self, "Success", message)
        self.status_label.setText(f"✓ {message}")
        
        # Refresh current search if there are results
        if self.result_list.count() > 0:
            self.do_search()
    
    def essentia_wsl_error(self, error_msg):
        self.progress_bar.hide()
        
        self.btn_index.setEnabled(True)
        self.btn_reanalyze.setEnabled(True)
        self.btn_essentia_wsl.setEnabled(True)
        self.force_reanalysis_checkbox.setEnabled(True)
        # search_bar and db_selector were never disabled, so no need to re-enable
        
        QMessageBox.critical(self, "Error", f"Essentia WSL analysis failed:\n\n{error_msg}")
        self.status_label.setText(f"❌ Error: {error_msg}")

    def update_progress_bar(self, val):
        # Initialize timing on first progress update
        if self.progress_start_time is None:
            self.progress_start_time = time.time()
            self.last_progress_value = 0
        
        self.progress_bar.setValue(val)
        
        # Calculate estimated time remaining
        if val > 0:
            elapsed = time.time() - self.progress_start_time
            progress_rate = val / elapsed  # percentage per second
            remaining_progress = 100 - val
            
            if progress_rate > 0 and val > self.last_progress_value:
                estimated_seconds = remaining_progress / progress_rate
                
                # Format time nicely
                if estimated_seconds < 60:
                    time_str = f"{int(estimated_seconds)}s"
                elif estimated_seconds < 3600:
                    minutes = int(estimated_seconds / 60)
                    seconds = int(estimated_seconds % 60)
                    time_str = f"{minutes}m {seconds}s"
                else:
                    hours = int(estimated_seconds / 3600)
                    minutes = int((estimated_seconds % 3600) / 60)
                    time_str = f"{hours}h {minutes}m"
                
                self.last_progress_message = f"Progress: {val}% | Est. remaining: {time_str}"
                self.last_progress_value = val
            elif self.last_progress_message:  # Use cached message if available
                # Update just the percentage in the cached message
                if "Progress:" in self.last_progress_message:
                    parts = self.last_progress_message.split("|")
                    if len(parts) > 1:
                        self.last_progress_message = f"Progress: {val}% |{parts[1]}"
                    else:
                        self.last_progress_message = f"Progress: {val}%"
            else:
                self.last_progress_message = f"Progress: {val}%"
            
            self.status_label.setText(self.last_progress_message)
        else:
            self.last_progress_message = f"Progress: {val}%"
            self.status_label.setText(self.last_progress_message)

    def indexing_finished(self, count):
        self.progress_bar.hide()
        # Reset progress timing
        self.progress_start_time = None
        self.last_progress_value = 0
        self.last_progress_message = ""
        
        self.btn_index.setEnabled(True)
        self.btn_reanalyze.setEnabled(True)
        self.btn_essentia_wsl.setEnabled(True)
        self.force_reanalysis_checkbox.setEnabled(True)
        # search_bar and db_selector were never disabled, so no need to re-enable
        
        self.status_label.setText("Reloading Engine...")
        try: 
            self.engine = SampleSearcher(db_path=self.current_db_path)
            self.search_bar.setPlaceholderText("Describe Sound: ")
            self.search_bar.setEnabled(True)
            self.search_bar.setFocus()
            self.status_label.setText(f"✓ Indexing complete! Processed {count} new files.")
            # Update window title with audio engine info
            self._update_window_title_with_engine()
            QMessageBox.information(self, "Done", f"Indexing complete!\nProcessed {count} new files.")
        except Exception as e:
            self.status_label.setText(f"FATAL ERROR: Could not load DB. {e}")
            QMessageBox.critical(self, "Error", f"Could not load database: {e}")

    def play_preview(self, item):
        file_path = item.data(Qt.ItemDataRole.UserRole)
        if file_path.startswith("/mnt/"):
            file_path = self.result_list.wsl_to_windows_path(file_path)

        # Get filename and BPM from the custom widget
        widget = self.result_list.itemWidget(item)
        if widget:
            # Get filename from the top label
            top_layout = widget.layout().itemAt(0)
            if top_layout and hasattr(top_layout, 'layout'):
                filename_label = top_layout.layout().itemAt(0).widget()
                if filename_label:
                    self.current_playing_filename = filename_label.text()
                # Try to get BPM label
                if top_layout.layout().count() > 1:
                    bpm_label = top_layout.layout().itemAt(1).widget()
                    if bpm_label and isinstance(bpm_label, QLabel):
                        bpm_text = bpm_label.text()
                        self.playing_label.setText(f"▶ {self.current_playing_filename} | {bpm_text}")
                    else:
                        self.playing_label.setText(f"▶ {self.current_playing_filename}")
                else:
                    self.playing_label.setText(f"▶ {self.current_playing_filename}")
            else:
                self.current_playing_filename = os.path.basename(file_path)
                self.playing_label.setText(f"▶ {self.current_playing_filename}")
        else:
            self.current_playing_filename = os.path.basename(file_path)
            self.playing_label.setText(f"▶ {self.current_playing_filename}")
        
        self.player.setSource(QUrl.fromLocalFile(file_path))
        self.player.play()
        self.play_pause_btn.setEnabled(True)
        self.stop_btn.setEnabled(True)
        self.play_pause_btn.setText("⏸ Pause")

    def toggle_playback(self):
        """Toggle play/pause"""
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.play_pause_btn.setText("▶ Play")
        elif self.player.playbackState() == QMediaPlayer.PlaybackState.PausedState:
            self.player.play()
            self.play_pause_btn.setText("⏸ Pause")
    
    def stop_audio(self):
        """Stop playback completely"""
        self.player.stop()
        self.playback_slider.setValue(0)
        self.time_label.setText("0:00")
        self.playing_label.setText("No sample playing")
        self.play_pause_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self.play_pause_btn.setText("⏸ Pause")
    
    def update_playback_position(self, position):
        """Update playback progress bar and time"""
        if not self.slider_is_pressed and self.player.duration() > 0:
            progress = int((position / self.player.duration()) * 1000)
            self.playback_slider.setValue(progress)
        self.time_label.setText(self.format_time(position))
    
    def on_slider_pressed(self):
        """Called when user starts dragging the slider"""
        self.slider_is_pressed = True
    
    def on_slider_released(self):
        """Called when user releases the slider - seek to new position"""
        self.slider_is_pressed = False
        if self.player.duration() > 0:
            new_position = int((self.playback_slider.value() / 1000) * self.player.duration())
            self.player.setPosition(new_position)
    
    def update_playback_duration(self, duration):
        """Update total duration display"""
        self.duration_label.setText(self.format_time(duration))
    
    def change_volume(self, value):
        """Change audio output volume"""
        self.audio_ouput.setVolume(value / 100)
        self.volume_label_pct.setText(f"{value}%")
    
    def handle_playback_state_changed(self, state):
        """Handle playback state changes"""
        if state == QMediaPlayer.PlaybackState.StoppedState:
            self.playback_slider.setValue(0)
            if self.current_playing_filename:
                self.playing_label.setText(f"■ {self.current_playing_filename}")
    
    def format_time(self, ms):
        """Convert milliseconds to MM:SS format"""
        seconds = ms // 1000
        minutes = seconds // 60
        seconds = seconds % 60
        return f"{minutes}:{seconds:02d}"

    def apply_filters(self, results):
        """Apply user-defined filters to search results"""
        filtered = []
        
        # Get filter values
        include_pattern = self.include_pattern.text().strip()
        exclude_pattern = self.exclude_pattern.text().strip()
        min_dur = self.min_duration.value()
        max_dur = self.max_duration.value()
        format_filter = self.format_combo.currentText()
        min_sim = self.min_similarity.value()
        max_sim = self.max_similarity.value()
        
        for item in results:
            filename = item['filename']
            full_path = item['route']
            distance = item['score']
            metadata = item.get('metadata', {})
            similarity_percent = max(0, min(100, (1 - distance / 2) * 100))
            
            # Build item with metadata for later use
            item_with_meta = {
                'filename': filename,
                'route': full_path,
                'score': distance,
                'metadata': metadata
            }
            
            # Filter by similarity
            if similarity_percent < min_sim or similarity_percent > max_sim:
                continue
            
            # Filter by include pattern
            if include_pattern:
                try:
                    if not re.search(include_pattern, filename, re.IGNORECASE):
                        continue
                except re.error:
                    pass  # Invalid regex, skip pattern
            
            # Filter by exclude pattern
            if exclude_pattern:
                try:
                    if re.search(exclude_pattern, filename, re.IGNORECASE):
                        continue
                except re.error:
                    pass  # Invalid regex, skip pattern
            
            # Filter by format
            if format_filter != "All":
                file_ext = os.path.splitext(filename)[1].lower().strip('.')
                if file_ext != format_filter:
                    continue
            
            # Filter by BPM
            min_bpm_val = self.min_bpm.value()
            max_bpm_val = self.max_bpm.value()
            if min_bpm_val > 0 or max_bpm_val < 300:
                bpm = item.get('metadata', {}).get('bpm', 0)
                if bpm is None:
                    bpm = 0
                if bpm > 0:  # Only filter samples that have BPM detected
                    if bpm < min_bpm_val or bpm > max_bpm_val:
                        continue
            
            # Filter by Key
            key_filter = self.key_filter.currentText()
            if key_filter != "All":
                sample_key = item.get('metadata', {}).get('key', '')
                if sample_key != key_filter:
                    continue
            
            # Filter by duration (if file exists and we can check)
            if min_dur > 0 or max_dur < 999:
                try:
                    # Convert WSL path to Windows if needed
                    check_path = full_path
                    if check_path.startswith("/mnt/"):
                        check_path = self.result_list.wsl_to_windows_path(check_path)
                    
                    duration = None
                    ext = os.path.splitext(check_path)[1].lower()
                    
                    if ext == '.wav' and os.path.exists(check_path):
                        try:
                            with wave.open(check_path, 'rb') as wf:
                                duration = wf.getnframes() / float(wf.getframerate())
                        except:
                            pass
                    elif ext in ['.mp3', '.aif', '.aiff', '.flac', '.ogg', '.opus', '.m4a', '.aac'] and os.path.exists(check_path):
                        try:
                            info = MutagenFile(check_path)
                            if info is not None and info.info:
                                duration = info.info.length
                        except:
                            pass
                    
                    if duration is not None:
                        if duration < min_dur or duration > max_dur:
                            continue
                except:
                    pass  # If we can't check duration, include the file
            
            filtered.append((item_with_meta, similarity_percent))
        
        return filtered
    
    def do_search(self):
        if self.engine is None:
            return
        query = self.search_bar.text().strip()
        
        # Don't search if query is empty
        if not query:
            return
        
        # Get more results than requested to account for filtering
        top_k = self.results_spinbox.value()
        fetch_k = min(100, top_k * 3)  # Fetch 3x to ensure enough after filtering
        
        results = self.engine.search(query, top_k=fetch_k)
        
        # Apply filters
        filtered_results = self.apply_filters(results)
        
        # Limit to requested count
        filtered_results = filtered_results[:top_k]
        
        self.result_list.clear()
        
        if not filtered_results:
            # Show message if no results
            empty_item = QListWidgetItem("No results match your filters. Try adjusting them.")
            empty_item.setForeground(QColor("#888"))
            self.result_list.addItem(empty_item)
            return

        for item, similarity_percent in filtered_results:
            filename = item['filename']
            full_path = item['route']
            bpm = item.get('metadata', {}).get('bpm', None)
            key = item.get('metadata', {}).get('key', None)
            analysis_engine = item.get('metadata', {}).get('analysis_engine', None)
            
            # Create custom widget with BPM, Key, and analysis engine
            widget = ResultWidget(filename, similarity_percent, bpm, key, analysis_engine)
            
            # Create list item
            list_item = QListWidgetItem(self.result_list)
            list_item.setData(Qt.ItemDataRole.UserRole, full_path)
            list_item.setSizeHint(widget.sizeHint())
            
            # Add to list
            self.result_list.addItem(list_item)
            self.result_list.setItemWidget(list_item, widget)

# Dark Theme
STYLESHEET = """
QMainWindow {
    background-color: #2b2b2b;
}

QLabel {
    color: #cccccc;
    font-family: 'Segoe UI', sans-serif;
}

QLineEdit {
    background-color: #3c3c3c;
    border: 1px solid #555;
    border-radius: 5px;
    color: #ffffff;
    padding: 8px;
    font-size: 14px;
}

QLineEdit:focus {
    border: 1px solid #006bb3;
}

QListWidget {
    background-color: #1e1e1e;
    border: none;
    color: #dddddd;
    font-size: 13px;
    outline: none;
}

QListWidget::item {
    padding: 2px;
    border-bottom: 1px solid #2a2a2a;
    background-color: transparent;
}

QListWidget::item:selected {
    background-color: #007acc;
    color: white;
}

QListWidget::item:hover {
    background-color: #333333;
}

QPushButton {
    background-color: #006bb3;
    color: white;
    border: none;
    padding: 10px 20px;
    border-radius: 4px;
    font-weight: bold;
}

QPushButton:hover {
    background-color: #005c99;
}

QPushButton:pressed {
    background-color: #004c80;
}

QProgressBar {
    border: 1px solid #444;
    border-radius: 3px;
    text-align: center;
    background-color: #2e2e2e;
    color: white;
}

QProgressBar::chunk {
    background-color: #006bb3;
    width: 20px;
}

QSpinBox, QDoubleSpinBox, QComboBox, QLineEdit {
    font-size: 12px;
}

QGroupBox {
    background-color: #2b2b2b;
}
"""

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLESHEET)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())