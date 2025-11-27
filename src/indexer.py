import os
import chromadb
import librosa
import torch
import numpy as np
import wave
from mutagen import File as MutagenFile
from transformers import ClapModel, ClapProcessor
from tqdm import tqdm

SAMPLE_FOLDER = "/mnt/c/Users/gdiaz/OneDrive/Documentos/Ableton/DrumKits"
DB_PATH = "./sample_db"
MAX_DURATION = 10.0

def get_audio_embedding(file_path):
    try:
        audio, sr = librosa.load(file_path, sr=48000, duration=MAX_DURATION)
        inputs = processor(audio=audio, return_tensors="pt", sampling_rate=sr)
        inputs = {k: v.to(device) for k, v in inputs.items()} #Move tensors from the dict to the GPU

        with torch.no_grad():
            embedding = model.get_audio_features(**inputs)
        
        return embedding.cpu().numpy().tolist()[0]
    except Exception as e:
        print(f"\nError processing {file_path}: {e}")
        return None
    
def get_duration(file_path):
    ext = os.path.splitext(file_path)[1].lower()
    try:
        if ext == '.wav':
            with wave.open(file_path, 'rb') as wf:
                return wf.getnframes() / float(wf.getframerate())
        elif ext == '.mp3':
            info = MutagenFile(file_path)
            if info is not None and info.info:
                return info.info.length
    except Exception:
        return None
    return None

# Initialize Model
device = "cuda" if torch.cuda.is_available() else "cpu"
print(f"Cargando modelo en: {device}")
model = ClapModel.from_pretrained("laion/clap-htsat-unfused", use_safetensors=True).to(device)
processor = ClapProcessor.from_pretrained("laion/clap-htsat-unfused")

#Initialize Database
chroma_client = chromadb.PersistentClient(path=DB_PATH)
collection = chroma_client.get_or_create_collection(name="samples_library")
existing_ids = set(collection.get()["ids"])

#Browse Folders
print(f"Scanning {SAMPLE_FOLDER}")
files_to_process = []

for root, dirs, files in os.walk(SAMPLE_FOLDER):
    for file in files:
        if file.lower().endswith(('.wav', '.mp3')):
            full_path = os.path.join(root, file)
            if full_path in existing_ids: #Filter existing ids in DB
                continue
            duration = get_duration(full_path)
            if duration is not None and duration <= MAX_DURATION: #Filter by max duration
                files_to_process.append(full_path)

print(f"Found {len(files_to_process)} files. Processing...")

for filepath in tqdm(files_to_process):
    vector  = get_audio_embedding(filepath)
    if vector:
        collection.add(
            embeddings=[vector],
            documents=[filepath],
            metadatas=[{"filename": os.path.basename(filepath)}],
            ids=[filepath]
        )

print("Processed Succesfuly")
