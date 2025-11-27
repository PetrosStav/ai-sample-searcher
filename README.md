#  AI Sample Searcher

**Find your samples by describing them, not by filename.**

A local desktop application for music producers that allows you to search through your sample library using natural language (e.g., *"Heavy distorted industrial kick"* or *"Atmospheric sci-fi texture"*).

Powered by **LAION-CLAP**, **ChromaDB**, and **PyQt6**.

---

## 🚀 Features

- **Semantic Search:** Uses Multimodal Embeddings to understand the "vibe" and texture of audio, bridging the gap between text and sound.
- **100% Local Privacy:** No cloud uploads. Your sample library is indexed and stored locally on your machine using ChromaDB.
- **DAW Integration:** Drag & Drop results directly from the app into **Ableton Live**, **FL Studio**, or **Bitwig**.
- **GPU Accelerated:** Optimized for CUDA to index thousands of samples in minutes.
- **Format Support:** Supports `.wav` and `.mp3` files.

## 🛠️ Tech Stack

- **Model:** [LAION-CLAP](https://huggingface.co/laion/clap-htsat-unfused) (Contrastive Language-Audio Pretraining).
- **Database:** [ChromaDB](https://www.trychroma.com/) (Local Vector Database with HNSW indexing).
- **GUI:** PyQt6 (Native desktop interface with Drag & Drop support).
- **Backend:** PyTorch & Transformers.

## 📋 Prerequisites

- **OS:** Windows 10/11 (Recommended for Drag & Drop compatibility), Linux, or macOS.
- **Python:** 3.10 or higher.
- **Hardware:** NVIDIA GPU recommended for faster indexing (CPU works but is slower).
