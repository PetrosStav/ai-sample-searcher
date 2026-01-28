# Essentia Analysis via WSL - Setup Guide

## Overview

This project now supports a hybrid approach for audio analysis:

- **Windows App**: Uses **librosa** for BPM and Key detection (good compatibility, no setup required)
- **WSL Script**: Uses **Essentia** for more accurate BPM and Key analysis (optional, requires setup)

The ChromaDB database tracks which analysis engine was used for each sample via the `analysis_engine` metadata field.

## Why This Approach?

- **Essentia doesn't work on Windows** - It requires Linux
- **WSL has issues with audio playback and drag & drop** - The full UI doesn't work well
- **Solution**: Run the main app on Windows, but use WSL for accurate audio analysis when needed

## Requirements

### For Windows App (Always Required)
- Python with librosa installed
- PyQt6 for the UI
- ChromaDB for vector storage

### For Essentia Analysis via WSL (Optional)
1. **WSL Installed**
   ```bash
   # Check if WSL is installed
   wsl --version
   ```

2. **Conda Environment in WSL** (`./env_wsl`)
   - Python with essentia-tensorflow
   - ChromaDB (same version as Windows)
   - numpy

3. **Accessible Audio Files**
   - Your audio files must be accessible from WSL
   - Typically stored on a Windows drive mounted at `/mnt/d/` or `/mnt/c/`

## Setup Instructions

### 1. Install WSL (if not already installed)

```powershell
# In PowerShell (Administrator)
wsl --install
```

### 2. Create Conda Environment in WSL

```bash
# In WSL terminal
conda create -p ./env_wsl python=3.10
conda activate ./env_wsl

# Install required packages
pip install essentia essentia-tensorflow
pip install chromadb numpy tqdm

# Install cuda cudnn 8.9.*
conda install -c conda-forge cudnn=8.9.*
```

### 3. Verify Setup

```bash
# In WSL terminal
conda activate ./env_wsl
python -c "import essentia; print('Essentia installed successfully!')"
```

### 4. Test the Script

```bash
# In WSL terminal
cd /mnt/<drive>/path/to/ai-sample-searcher/desktop_app
conda activate ../env_wsl
python analyze_essentia_wsl.py --db-path ../sample_db --help
```

## Usage

### Option 1: Windows UI Button (Recommended)

1. Open the Windows app (`app.py`)
2. Make sure you have a database loaded
3. Click the **"🎼 Essentia (WSL)"** button
4. Choose whether to force reanalysis:
   - **Unchecked**: Only analyze samples without BPM/Key or analyzed with librosa
   - **Checked**: Reanalyze ALL samples (use "Force" checkbox)
5. Wait for the analysis to complete
6. Results will show which engine was used with a small badge

### Option 2: Manual Command Line

```bash
# In WSL terminal
cd /mnt/<drive>/path/to/ai-sample-searcher/desktop_app
conda activate ../env_wsl

# Analyze samples without BPM/Key or analyzed with librosa
python analyze_essentia_wsl.py --db-path ../sample_db

# Force reanalysis of ALL samples
python analyze_essentia_wsl.py --db-path ../sample_db --force
```

## What Gets Analyzed?

### Regular Mode (without --force)
- Samples with **no BPM** (bpm = 0)
- Samples with **no Key** (key = '')
- Samples analyzed with **librosa** (analysis_engine = 'librosa')

### Force Mode (with --force)
- **ALL samples** in the database

## Database Metadata

Each sample now has an `analysis_engine` field:
- `"librosa"` - Analyzed with librosa (Windows)
- `"essentia"` - Analyzed with Essentia (WSL)

You can see this in the search results as a small badge next to the filename:
- 🔬 **Librosa** (orange badge)
- 🔬 **Essentia** (blue badge)

## Workflow Recommendations

### Initial Setup
1. Index your sample library using the Windows app (uses librosa)
2. Use the app normally for searching and preview
3. When you're ready, run Essentia analysis via WSL for more accurate BPM/Key

### Regular Workflow
1. Add new samples using Windows app (quick, uses librosa)
2. Search and use samples normally
3. Periodically run Essentia analysis (button or CLI) to improve accuracy

### Best Accuracy
1. Index with Windows app first (creates embeddings)
2. Immediately run Essentia analysis via WSL
3. All samples will have accurate BPM/Key data from the start

## Troubleshooting

### "WSL not found" Error
- Make sure WSL is installed: `wsl --version`
- Try running `wsl` in PowerShell to verify it works

### "Conda environment not found" Error
- Check the script uses the correct environment name
- Edit `analyze_essentia_wsl.py` if your environment is named differently
- Default is `env_wsl`, but you can modify the script

### "Database not found" Error
- Make sure the database path is correct
- Windows path example: `<drive>:\path\to\ai-sample-searcher\sample_db`
- WSL converts it to: `/mnt/<drive>/path/to/ai-sample-searcher/sample_db`

### "File not found" Error During Analysis
- Make sure your audio files are on a Windows drive accessible from WSL
- Files should be at `/mnt/c/` or `/mnt/d/` etc.
- Don't use network drives or unmounted locations

### Slow Analysis
- Essentia analysis is slower than librosa (more accurate)
- Expect ~0.5-2 seconds per sample depending on CPU
- Use without `--force` to only analyze new/unanalyzed samples

## Script Location

The Essentia analysis script is located at:
```
desktop_app/analyze_essentia_wsl.py
```

It can be run independently of the Windows app if needed.

## Technical Details

### Path Conversion
The script automatically converts between Windows and WSL paths:
- Windows: `C:\folder\file.wav`
- WSL: `/mnt/c/folder/file.wav`

### Conda Activation
The Windows app runs this command in WSL:
```bash
wsl bash -ic "conda activate ../env_wsl && python ..."
```

This uses bash's interactive mode which automatically sources conda initialization. If your conda is installed elsewhere or you encounter activation issues, you may need to modify the script.

### Batch Updates
The script updates the database in batches of 50 samples for efficiency.

## Future Improvements

Possible enhancements:
- Progress bar for WSL analysis
- Automatic conda environment detection
- Support for other audio analysis engines
- Parallel processing for faster analysis