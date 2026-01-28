import os
from typing import List, Dict, Optional

import torch
import chromadb
from transformers import ClapModel, ClapProcessor

# Default model name from HuggingFace
# MODEL_NAME = "laion/clap-htsat-unfused"
MODEL_NAME = "laion/larger_clap_music_and_speech"

# Use local model cache to avoid downloading from HuggingFace
LOCAL_MODEL_PATH = os.path.join(os.path.dirname(__file__), '..', 'cloud_api', 'model_cache', MODEL_NAME.replace('/', '_'))

# Default database path
DEFAULT_DB_PATH = "./sample_db"


class SampleSearcher:
    """Lightweight searcher wrapper around CLAP + ChromaDB.

    Usage:
      from searcher import SampleSearcher
      s = SampleSearcher(db_path='./sample_db')
      results = s.search('kick drum short', top_k=5)

    Results format: List[dict] with keys: `filename`, `route`, `score`.
    """

    def __init__(
        self,
        db_path: str = None,
        model_name: str = None,
        device: Optional[str] = None,
    ) -> None:
        self.db_path = db_path or DEFAULT_DB_PATH
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")

        # Use local model if available, otherwise fallback to HuggingFace
        use_local = False
        if model_name is None:
            # Check if local model exists AND has required files
            if os.path.exists(LOCAL_MODEL_PATH) and os.path.exists(os.path.join(LOCAL_MODEL_PATH, 'config.json')):
                model_name = LOCAL_MODEL_PATH
                use_local = True
                print(f"Using local model cache: {model_name}")
            else:
                model_name = MODEL_NAME
                print(f"Using HuggingFace model: {model_name}")

        # Load model & processor
        print(f"Loading CLAP model on: {self.device}")
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

        # Connect to Chroma DB
        if not os.path.exists(self.db_path):
            raise FileNotFoundError(f"Error: DB folder not found at {self.db_path}")

        self.chroma_client = chromadb.PersistentClient(path=self.db_path)
        self.collection = self.chroma_client.get_collection(name="samples_library")

    def search(self, query_text: str, top_k: int = 10) -> List[Dict]:
        """Search for `query_text` and return structured results.

        Returns a list of dicts: { 'filename': ..., 'route': ..., 'score': ... }
        """
        text_inputs = self.processor(text=[query_text], return_tensors="pt")
        text_inputs = {k: v.to(self.device) for k, v in text_inputs.items()}

        with torch.no_grad():
            text_output = self.model.get_text_features(**text_inputs)
            # Extract the tensor from the output object
            text_embed = text_output.pooler_output if hasattr(text_output, 'pooler_output') else text_output

        query_vector = text_embed.cpu().numpy().tolist()[0]
        results = self.collection.query(query_embeddings=[query_vector], n_results=top_k)

        routes = results.get('ids', [[]])[0]
        metadatas = results.get('metadatas', [[]])[0]
        distances = results.get('distances', [[]])[0]

        out: List[Dict] = []
        for i in range(len(routes)):
            filename = metadatas[i].get('filename') if i < len(metadatas) else None
            metadata = metadatas[i] if i < len(metadatas) else {}
            complete_route = routes[i]
            score = distances[i]
            out.append({
                'filename': filename,
                'route': complete_route,
                'score': score,
                'metadata': metadata
            })

        return out

    def print_results(self, results: List[Dict], top_k: Optional[int] = None) -> None:
        k = top_k or len(results)
        print("-" * 50)
        print(f"Top {k} Results:")
        print("-" * 50)
        for i, r in enumerate(results[:k]):
            bpm = r.get('metadata', {}).get('bpm', None)
            bpm_str = f" | BPM: {bpm:.0f}" if bpm and bpm > 0 else ""
            print(f"#{i+1} | {r.get('filename')}{bpm_str}")
            print(f"    └ Score: {r.get('score'):.4f} | Route: {r.get('route')}")
            print("")


if __name__ == "__main__":
    # Backwards-compatible interactive CLI
    print(f"Using database: {DEFAULT_DB_PATH}")
    searcher = SampleSearcher()
    while True:
        user_input = input(">> Describe Sound: ")
        if len(user_input.strip()) > 0:
            results = searcher.search(user_input)
            searcher.print_results(results)