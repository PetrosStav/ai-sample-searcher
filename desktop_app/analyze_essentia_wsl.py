#!/usr/bin/env python3
"""
WSL Essentia Analysis Script
This script runs on WSL to analyze audio samples using Essentia.
It connects to the ChromaDB database and updates BPM/Key metadata.

Usage:
    python analyze_essentia_wsl.py [--db-path PATH] [--force]
    
Options:
    --db-path PATH    Path to ChromaDB database (default: ./sample_db)
    --force           Force reanalysis of all samples (even those already analyzed)
"""

import os
import sys
import argparse
import chromadb
import numpy as np
from tqdm import tqdm

# Import essentia
try:
    import essentia
    import essentia.standard as es
    print("✓ Essentia loaded successfully")
except ImportError as e:
    print("ERROR: Essentia not available! Please install it in your WSL environment:")
    print("  conda activate env_wsl")
    print("  pip install essentia-tensorflow")
    sys.exit(1)


def wsl_path_to_windows(wsl_path):
    """Convert WSL path to Windows path if needed"""
    if wsl_path.startswith("/mnt/"):
        parts = wsl_path.split('/')
        drive_letter = parts[2]
        rest_of_path = "/".join(parts[3:])
        windows_path = f"{drive_letter.upper()}:/{rest_of_path}"
        return windows_path
    return wsl_path


def windows_path_to_wsl(windows_path):
    """Convert Windows path to WSL path if needed"""
    if len(windows_path) >= 2 and windows_path[1] == ':':
        drive_letter = windows_path[0].lower()
        rest_of_path = windows_path[2:].replace('\\', '/')
        wsl_path = f"/mnt/{drive_letter}{rest_of_path}"
        return wsl_path
    return windows_path


def get_bpm_and_key_essentia(file_path):
    """Extract BPM and Key using Essentia"""
    try:
        # Convert Windows path to WSL path if needed for file access
        file_to_load = wsl_path_to_windows(file_path) if file_path.startswith("/mnt/") else file_path
        
        # Try both path formats
        if not os.path.exists(file_to_load):
            file_to_load = windows_path_to_wsl(file_path)
        
        if not os.path.exists(file_to_load):
            print(f"  Warning: File not found: {file_path}")
            return None, None
        
        # Load audio with Essentia (up to 30 seconds for analysis)
        loader = es.MonoLoader(filename=file_to_load, sampleRate=44100)
        audio = loader()
        
        # Limit to 30 seconds to speed up analysis
        max_samples = 44100 * 30
        if len(audio) > max_samples:
            audio = audio[:max_samples]
        
        # Skip very short samples
        if len(audio) < 44100 * 0.5:
            return None, None
        
        bpm = None
        key = None
        
        # === BPM Detection using RhythmExtractor2013 ===
        try:
            rhythm_extractor = es.RhythmExtractor2013(method="multifeature")
            bpm_value, beats, beats_confidence, _, beats_intervals = rhythm_extractor(audio)
            
            if bpm_value > 0:
                # Apply similar heuristics as librosa for consistency
                base_bpm = float(bpm_value)
                candidates = [base_bpm]
                
                if base_bpm < 80:
                    candidates.append(base_bpm * 2)
                if base_bpm > 160:
                    candidates.append(base_bpm / 2)
                
                valid_candidates = [b for b in candidates if 40 <= b <= 200]
                
                if valid_candidates:
                    final_bpm = min(valid_candidates, key=lambda x: abs(x - 120))
                    bpm = round(final_bpm, 1)
        except Exception as e:
            print(f"  BPM Error: {e}")
        
        # === Key Detection using HPCP and Key algorithm ===
        try:
            # First, compute HPCP (Harmonic Pitch Class Profile) from audio
            windowing = es.Windowing(type='blackmanharris62')
            spectrum = es.Spectrum()
            spectral_peaks = es.SpectralPeaks(orderBy='magnitude',
                                               magnitudeThreshold=0.00001,
                                               minFrequency=20,
                                               maxFrequency=3500,
                                               maxPeaks=60)
            
            # Compute HPCP with size that's multiple of 12
            hpcp_extractor = es.HPCP(size=36,
                                    referenceFrequency=440,
                                    bandPreset=False,
                                    minFrequency=20,
                                    maxFrequency=3500,
                                    weightType='cosine',
                                    nonLinear=False,
                                    windowSize=1.)
            
            # Process audio frames to get HPCP
            frame_size = 4096
            hop_size = 2048
            hpcp_values = []
            
            for frame_start in range(0, len(audio) - frame_size, hop_size):
                frame = audio[frame_start:frame_start + frame_size]
                windowed_frame = windowing(frame)
                spec = spectrum(windowed_frame)
                frequencies, magnitudes = spectral_peaks(spec)
                
                if len(frequencies) > 0:
                    hpcp = hpcp_extractor(frequencies, magnitudes)
                    hpcp_values.append(hpcp)
            
            if hpcp_values:
                # Average HPCP across all frames
                avg_hpcp = np.mean(hpcp_values, axis=0)
                
                # Now use Key algorithm with the averaged HPCP
                key_extractor = es.Key(profileType='edma', pcpSize=36)
                key_result = key_extractor(avg_hpcp)
                
                # Handle different return formats
                if isinstance(key_result, tuple):
                    if len(key_result) >= 3:
                        detected_key = key_result[0]
                        detected_scale = key_result[1]
                        strength = key_result[2]
                        
                        # Only accept if confidence is reasonable
                        if strength > 0.5:
                            scale_abbr = "maj" if detected_scale == "major" else "min"
                            key = f"{detected_key} {scale_abbr}"
        except Exception as e:
            # Silently skip key detection errors
            pass
        
        return bpm, key
        
    except Exception as e:
        print(f"  Analysis error for {file_path}: {e}")
        return None, None


def main():
    parser = argparse.ArgumentParser(description='Analyze audio samples with Essentia (WSL)')
    parser.add_argument('--db-path', type=str, default='./sample_db',
                      help='Path to ChromaDB database (default: ./sample_db)')
    parser.add_argument('--force', action='store_true',
                      help='Force reanalysis of all samples')
    
    args = parser.parse_args()
    
    print("=" * 60)
    print("Essentia Audio Analysis (WSL)")
    print("=" * 60)
    print(f"Database: {args.db_path}")
    print(f"Force reanalysis: {args.force}")
    print()
    
    # Convert Windows path to WSL path for database
    db_path = args.db_path
    if db_path.startswith("D:") or db_path.startswith("d:"):
        db_path = windows_path_to_wsl(db_path)
        print(f"Converted database path to WSL: {db_path}")
    
    # Check if database exists
    if not os.path.exists(db_path):
        print(f"ERROR: Database not found at: {db_path}")
        print("Make sure to provide the correct path using --db-path")
        sys.exit(1)
    
    # Connect to ChromaDB
    try:
        chroma_client = chromadb.PersistentClient(path=db_path)
        collection = chroma_client.get_collection(name="samples_library")
        print(f"✓ Connected to database")
    except Exception as e:
        print(f"ERROR: Could not connect to database: {e}")
        sys.exit(1)
    
    # Get all samples
    print("Fetching samples from database...")
    try:
        all_samples = collection.get()
        sample_ids = all_samples.get('ids', [])
        metadatas = all_samples.get('metadatas', [])
        print(f"Found {len(sample_ids)} samples in database")
    except Exception as e:
        print(f"ERROR: Could not fetch samples: {e}")
        sys.exit(1)
    
    # Filter samples based on force flag
    samples_to_analyze = []
    for i, metadata in enumerate(metadatas):
        if args.force:
            # Analyze all samples
            samples_to_analyze.append((sample_ids[i], metadata))
        else:
            # Only analyze samples without BPM/Key or analyzed with librosa
            analysis_engine = metadata.get('analysis_engine', '')
            bpm = metadata.get('bpm', 0)
            key = metadata.get('key', '')
            
            if analysis_engine == 'librosa' or bpm == 0 or not key:
                samples_to_analyze.append((sample_ids[i], metadata))
    
    total = len(samples_to_analyze)
    print(f"Samples to analyze: {total}")
    
    if total == 0:
        print("No samples need analysis. Use --force to reanalyze all samples.")
        return
    
    print()
    print("Starting analysis...")
    print("-" * 60)
    
    # Analyze samples
    updated = 0
    batch_updates = []
    
    for file_path, metadata in tqdm(samples_to_analyze, desc="Analyzing", unit="sample"):
        try:
            # Analyze BPM and Key
            bpm, key = get_bpm_and_key_essentia(file_path)
            
            updated_something = False
            if bpm is not None and bpm > 0:
                metadata['bpm'] = bpm
                updated_something = True
            if key is not None:
                metadata['key'] = key
                updated_something = True
            
            # Always mark as analyzed with essentia
            metadata['analysis_engine'] = 'essentia'
            
            if updated_something or args.force:
                batch_updates.append((file_path, metadata))
                updated += 1
                
                # Batch update every 50 samples for efficiency
                if len(batch_updates) >= 50:
                    ids = [item[0] for item in batch_updates]
                    metas = [item[1] for item in batch_updates]
                    collection.update(ids=ids, metadatas=metas)
                    batch_updates = []
        
        except Exception as e:
            print(f"\nError analyzing {file_path}: {e}")
    
    # Update any remaining samples in the batch
    if batch_updates:
        ids = [item[0] for item in batch_updates]
        metas = [item[1] for item in batch_updates]
        collection.update(ids=ids, metadatas=metas)
    
    print()
    print("=" * 60)
    print(f"Analysis complete!")
    print(f"Updated {updated} samples with Essentia analysis")
    print("=" * 60)


if __name__ == "__main__":
    main()
