import os
# Suppress ffmpeg/libav warnings (e.g., vorbis timestamp warnings)
os.environ["AV_LOG_LEVEL"] = "error"
os.environ["AUDIOREAD_BACKENDS"] = "ffmpeg"  # Ensure audioread uses ffmpeg
import sys
import chromadb
import librosa
import torch
import numpy as np
import wave
import warnings
from mutagen import File as MutagenFile
from transformers import ClapModel, ClapProcessor
from tqdm import tqdm

# Windows desktop app uses librosa only
# Essentia analysis is done separately via WSL script (analyze_essentia_wsl.py)
print("Using librosa for BPM and Key detection")
print("  Use 'Analyze with Essentia (WSL)' button for more accurate essentia analysis")

# Suppress librosa warnings for short samples
warnings.filterwarnings('ignore', category=UserWarning, module='librosa')
warnings.filterwarnings('ignore')

# Redirect stderr to suppress ffmpeg warnings that slip through
import contextlib

@contextlib.contextmanager
def suppress_stderr():
    """Context manager to suppress stderr output"""
    with open(os.devnull, 'w') as devnull:
        old_stderr = sys.stderr
        sys.stderr = devnull
        try:
            yield
        finally:
            sys.stderr = old_stderr

# Default database path
DB_PATH = "./sample_db"
MAX_DURATION = 10.0

# Default model name from HuggingFace
# MODEL_NAME = "laion/clap-htsat-unfused"
MODEL_NAME = "laion/larger_clap_music_and_speech"

# Use local model cache to avoid downloading from HuggingFace
LOCAL_MODEL_PATH = os.path.join(os.path.dirname(__file__), '..', 'cloud_api', 'model_cache', MODEL_NAME.replace('/', '_'))

class IndexerBackend:
    def __init__(self, db_path=DB_PATH):
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"Indexer using device: {self.device}")
        
        # Store which audio analysis engine is being used (always librosa for Windows app)
        self.audio_engine = "Librosa"
        
        # Use local model if available, otherwise fallback to HuggingFace
        use_local = False
        if os.path.exists(LOCAL_MODEL_PATH) and os.path.exists(os.path.join(LOCAL_MODEL_PATH, 'config.json')):
            model_name = LOCAL_MODEL_PATH
            use_local = True
            print(f"Using local model cache: {model_name}")
        else:
            model_name = MODEL_NAME
            print(f"Using HuggingFace model: {model_name}")
        
        #Load Models
        if use_local:
            self.model = ClapModel.from_pretrained(model_name, use_safetensors=True, local_files_only=True).to(self.device)
            self.processor = ClapProcessor.from_pretrained(model_name, local_files_only=True)
        else:
            # Download to custom cache directory
            cache_base = os.path.join(os.path.dirname(__file__), '..', 'cloud_api', 'model_cache')
            os.makedirs(cache_base, exist_ok=True)
            print(f"Downloading model to: {LOCAL_MODEL_PATH}")
            self.model = ClapModel.from_pretrained(model_name, use_safetensors=True, cache_dir=LOCAL_MODEL_PATH).to(self.device)
            self.processor = ClapProcessor.from_pretrained(model_name, cache_dir=LOCAL_MODEL_PATH)
        #Create and connect DB
        self.chroma_client = chromadb.PersistentClient(path=db_path)
        self.collection = self.chroma_client.get_or_create_collection(name="samples_library")
    
    def get_audio_engine(self):
        """Returns the name of the audio analysis engine being used"""
        return self.audio_engine

    def get_audio_embedding(self, file_path):
        try:
            with suppress_stderr():
                audio, sr = librosa.load(file_path, sr=48000, duration=MAX_DURATION)
            inputs = self.processor(audio=audio, return_tensors="pt", sampling_rate=sr)
            inputs = {k: v.to(self.device) for k, v in inputs.items()} #Move tensors from the dict to the GPU
            with torch.no_grad():
                output = self.model.get_audio_features(**inputs)      
                # Extract the tensor from the output object
                embedding = output.pooler_output if hasattr(output, 'pooler_output') else output
            return embedding.cpu().numpy().tolist()[0]
        except Exception as e:
            print(f"\nError processing {file_path}: {e}")
            return None
        
    def get_duration(self, file_path):
        ext = os.path.splitext(file_path)[1].lower()
        try:
            if ext == '.wav':
                with wave.open(file_path, 'rb') as wf:
                    return wf.getnframes() / float(wf.getframerate())
            elif ext in ['.mp3', '.aif', '.aiff', '.flac', '.ogg', '.opus', '.m4a', '.aac']:
                info = MutagenFile(file_path)
                if info is not None and info.info:
                    return info.info.length
        except Exception:
            return None
        return None
    
    def get_bpm_and_key(self, file_path):
        """Extract BPM and Key using librosa"""
        try:
            # Load audio once (first 30 seconds is enough for analysis)
            with suppress_stderr():
                y, sr = librosa.load(file_path, sr=22050, duration=30.0)
            
            # Skip very short samples
            if len(y) < sr * 0.5:
                return None, None
            
            # Separate harmonic (for Key) and percussive (for BPM)
            y_harmonic, y_percussive = librosa.effects.hpss(y)
            
            # === BPM Detection ===
            bpm = None
            try:
                # onset_strength is generally more reliable for "feeling" the beat
                onset_env = librosa.onset.onset_strength(y=y_percussive, sr=sr)
                
                # estimate tempo
                tempo = librosa.beat.tempo(onset_envelope=onset_env, sr=sr)
                
                # Handle different librosa return types (scalar vs array)
                if isinstance(tempo, np.ndarray):
                    tempo = tempo[0]
                
                base_bpm = float(tempo)

                # Heuristic: Prioritize 80-160 BPM range (standard dance/pop range)
                # If detected BPM is < 80, try doubling it. If > 160, try halving it.
                candidates = [base_bpm]
                if base_bpm < 80:
                    candidates.append(base_bpm * 2)
                if base_bpm > 160:
                    candidates.append(base_bpm / 2)
                
                # Filter candidates within strictly reasonable bounds
                valid_candidates = [b for b in candidates if 40 <= b <= 200]
                
                # Pick the one closest to the 100-130 "sweet spot" if multiple exist
                if valid_candidates:
                    # Sort by distance to 120 BPM
                    final_bpm = min(valid_candidates, key=lambda x: abs(x - 120))
                    bpm = round(final_bpm, 1)
            except Exception as e:
                print(f"Librosa BPM Error: {e}")
                pass
            
            # === Key Detection ===
            key = None
            try:
                # Chroma CQT is robust for musical pitch
                chroma = librosa.feature.chroma_cqt(y=y_harmonic, sr=sr, hop_length=512)
                chroma_mean = np.mean(chroma, axis=1)
                
                if np.std(chroma_mean) >= 1e-6:
                    pitch_classes = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
                    
                    # Krumhansl-Schmuckler profiles
                    major_profile = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
                    minor_profile = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
                    
                    # Normalize profiles
                    major_profile /= np.linalg.norm(major_profile)
                    minor_profile /= np.linalg.norm(minor_profile)
                    
                    # Normalize chroma
                    chroma_mean /= (np.linalg.norm(chroma_mean) + 1e-8)
                    
                    correlations = []
                    for i in range(12):
                        # Roll the chroma vector to match the template
                        rolled_chroma = np.roll(chroma_mean, -i)
                        
                        corr_maj = np.dot(rolled_chroma, major_profile)
                        corr_min = np.dot(rolled_chroma, minor_profile)
                        
                        correlations.append((pitch_classes[i], 'Maj', corr_maj))
                        correlations.append((pitch_classes[i], 'Min', corr_min))
                    
                    # Get best match
                    best_match = max(correlations, key=lambda x: x[2])
                    
                    # Confidence threshold (arbitrary, but 0.5 is usually safe)
                    if best_match[2] > 0.5:
                        key = f"{best_match[0]} {best_match[1]}"
            except Exception as e:
                print(f"Librosa Key Error: {e}")
                pass
            
            return bpm, key
            
        except Exception:
            return None, None
    
    def run_indexing(self, folder_path, progress_callback=None): 
        print(f"Scanning {folder_path}...")
        existing_ids = set(self.collection.get()["ids"])
        files_to_process = []

        for root, dirs, files in os.walk(folder_path):
            for file in files:
                if file.lower().endswith(('.wav', '.mp3', '.aif', '.aiff', '.flac', '.ogg', '.opus', '.m4a', '.aac')):
                    full_path = os.path.join(root, file)
                    full_path = os.path.normpath(full_path)
                    if full_path in existing_ids: #Filter existing ids in DB
                        continue
                    duration = self.get_duration(full_path)
                    if duration is not None and duration <= MAX_DURATION: #Filter by max duration
                        files_to_process.append(full_path)

        print(f"Found {len(files_to_process)} files. Indexing...")
        count = 0
        for i, filepath in enumerate(tqdm(files_to_process)):
            # 1. Get Embedding (requires 48kHz usually)
            vector = self.get_audio_embedding(filepath)
            
            if vector:
                # 2. Get BPM and Key in ONE pass (uses 22kHz)
                # This replaces the separate self.get_bpm() and self.get_key() calls
                bpm, key = self.get_bpm_and_key(filepath)
                
                metadata = {
                    "filename": os.path.basename(filepath),
                    "bpm": bpm if bpm is not None else 0.0,
                    "key": key if key is not None else "",
                    "analysis_engine": self.audio_engine.lower()  # Track which engine was used
                }
                
                self.collection.add(
                    embeddings=[vector],
                    documents=[filepath],
                    metadatas=[metadata],
                    ids=[filepath]
                )
                count += 1
            if progress_callback:
                percent = int(((i+1)/len(files_to_process))*100)
                progress_callback(percent)

        return count
