#  AI Sample Searcher

**Find your samples by describing them, not by filename.**

![Demo](docs/demo.gif)

A local desktop application for music producers that allows you to search through your sample library using natural language (e.g., *"Heavy distorted industrial kick"* or *"Atmospheric sci-fi texture"*).

Powered by **LAION-CLAP**, **ChromaDB**, and **PyQt6**.

---

## Features

### Core Search Features
- **Semantic Search:** Uses Multimodal Embeddings (LAION-CLAP) to understand the "vibe" and texture of audio, bridging the gap between text and sound.
- **100% Local Privacy:** No cloud uploads. Your sample library is indexed and stored locally on your machine using ChromaDB.
- **DAW Integration:** Drag & Drop results directly from the app into **Ableton Live**, **FL Studio**, or **Bitwig**.
- **GPU Accelerated:** Optimized for CUDA to index thousands of samples in minutes.
- **Format Support:** Supports `.wav`, `.mp3`, `.aif`, `.aiff`, `.flac`, `.ogg`, `.opus`, `.m4a`, and `.aac` files.
- **Sample Length:** Automatically indexes samples up to 10 seconds in length (longer samples are skipped during indexing).
- **Model Caching:** CLAP model is downloaded once and cached locally to avoid repeated downloads.

### Audio Analysis
- **BPM & Key Detection:** Automatic BPM and musical key detection for all samples
  - **Librosa Engine:** Fast, built-in analysis (default on Windows)
    - Uses onset strength analysis for tempo detection
    - Intelligent BPM range adjustment (prioritizes 80-160 BPM range)
    - Krumhansl-Schmuckler profiles for musical key detection
  - **Essentia Engine:** More accurate analysis via WSL (optional, see [ESSENTIA_WSL_SETUP.md](ESSENTIA_WSL_SETUP.md))
- **Analysis Engine Tracking:** Each sample shows which analysis engine was used with visual badges
- **Batch Reanalysis:** Reanalyze BPM/Key for samples that need it or force reanalysis for all samples

### Advanced Filtering
- **Text Pattern Filters:** Include/exclude samples by filename patterns (regex supported)
- **Similarity Range:** Filter results by similarity percentage (0-100%)
- **BPM Range Filter:** Find samples within a specific tempo range
- **Duration Filter:** Filter by sample length (seconds)
- **Musical Key Filter:** Filter by detected musical key (e.g., "C maj", "A min")
- **Audio Format Filter:** Filter by file extension

### User Interface
- **Multiple Database Support:** Switch between different sample databases/libraries
- **Collapsible Filter Panel:** Clean interface with advanced filters available when needed
- **Audio Playback with Progress:** Preview samples with visual waveform and seekable playback
- **Configurable Results:** Adjust number of search results (default 20)
- **Visual Badges:** See which analysis engine was used for each sample (Librosa 🔬 / Essentia 🔬)

## Tech Stack

- **Model:** [LAION-CLAP](https://huggingface.co/laion/larger_clap_music_and_speech) - Larger CLAP model for music and speech (Contrastive Language-Audio Pretraining).
- **Database:** [ChromaDB](https://www.trychroma.com/) (Local Vector Database with HNSW indexing).
- **GUI:** PyQt6 (Native desktop interface with Drag & Drop support).
- **Backend:** PyTorch & Transformers.

## Prerequisites

- **OS:** Windows 10/11 (Recommended for Drag & Drop compatibility), Linux, or macOS.
- **Python:** 3.10 or higher.
- **Hardware:** NVIDIA GPU recommended for faster indexing (CPU works but is slower).

## Instalation

Prerequisite: Python 3.10 is required.

1. **Clone the respository**
```bash
   git clone https://github.com/gdiaz82/ai-sample-searcher
   cd ai-sample-searcher
```
2. **Create a virtual environment (Recommended)**
```bash
    conda create -p ./env python=3.10
    conda activate ./env
```
3. **Install dependencies**
```bash
    pip install -r requirements.txt
```
*Note: If you have an NVIDIA GPU, ensure you have the correct PyTorch version with CUDA support installed.*
*pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121*

## Usage

1. **Launch the Application**
```bash
python desktop_app/app.py
```
2. **Index your Library**
When you open the app for the first time, the database will be empty.

- Click the "📂 Add Samples Folder" button at the top.

- Select the root directory of your sample library via the file explorer window.

- The AI will scan and generate embeddings for your files. This is GPU-accelerated but may take a few minutes depending on your library size.

- A popup will confirm when indexing is finished.

3. **Search & Use Samples**

- Type a description in the search bar (e.g., "Crunchy hip hop snare" or "Atmospheric sci-fi texture").

- Press Enter or adjust the number of results to return.

- View results with similarity scores, BPM, key, duration, and analysis engine badge.

- Click on a result to preview the sound with waveform visualization.

- Drag and Drop the result directly from the list into your DAW (Ableton, FL Studio, etc.).

4. **Advanced Filtering (Optional)**

- Click on "🔍 Advanced Filters" to expand the filter panel.

- Apply filters:
  - **Text Patterns:** Include/exclude by filename (e.g., include "kick", exclude "loop")
  - **Similarity:** Set minimum/maximum similarity percentage
  - **BPM Range:** Filter by tempo (e.g., 120-140 BPM)
  - **Duration:** Filter by sample length
  - **Musical Key:** Filter by detected key
  - **Format:** Filter by audio file type

- Filters update search results automatically.

- Click "⟲ Reset All" to clear all filters.

5. **Improve BPM/Key Accuracy (Optional)**

- Click the "🎼 Essentia (WSL)" button to run more accurate BPM/Key analysis.

- Choose whether to reanalyze all samples or only those without accurate data.

- See [ESSENTIA_WSL_SETUP.md](ESSENTIA_WSL_SETUP.md) for setup instructions.

6. **Multiple Databases**

- Use the database dropdown in the top toolbar to switch between different sample libraries.

- Each database maintains its own embeddings, metadata, and analysis data.


## Additional Features

### Database Management
- **Persistent Storage:** All embeddings and metadata stored locally in ChromaDB
- **Metadata Tracking:** Each sample includes filename, path, BPM, key, duration, format, and analysis engine
- **Multiple Databases:** Create and switch between different sample library databases
- **Efficient Updates:** Batch processing for BPM/Key reanalysis

### Audio Analysis Engines
- **Librosa (Default):** Fast, cross-platform BPM and key detection
- **Essentia (WSL):** More accurate analysis using Essentia's advanced algorithms
- **Hybrid Workflow:** Index quickly with Librosa, then improve accuracy with Essentia when needed

## Advanced Setup

### Essentia Analysis via WSL (Windows Only)

For more accurate BPM and key detection, you can set up Essentia analysis via Windows Subsystem for Linux (WSL).

See the detailed guide: **[ESSENTIA_WSL_SETUP.md](ESSENTIA_WSL_SETUP.md)**

Key benefits:
- More accurate BPM detection
- Better musical key analysis
- Seamless integration with Windows UI
- Optional - use only when you need higher accuracy

## License
This project uses the LAION-CLAP model. Please refer to their repository for model licensing details.