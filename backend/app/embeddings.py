import json
import threading

import numpy as np

MODEL_NAME = "BAAI/bge-small-en-v1.5"

_model = None
_lock = threading.Lock()


def _get_model():
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                from fastembed import TextEmbedding

                _model = TextEmbedding(model_name=MODEL_NAME)
    return _model


def embed_text(text: str) -> list[float]:
    text = (text or "").strip()
    if not text:
        return []
    model = _get_model()
    vec = next(model.embed([text]))
    return vec.tolist()


def embedding_model_name() -> str:
    return MODEL_NAME


def cosine_similarity(a: list[float], b: list[float]) -> float | None:
    if not a or not b:
        return None
    va, vb = np.array(a), np.array(b)
    denom = np.linalg.norm(va) * np.linalg.norm(vb)
    if denom == 0:
        return None
    return float(np.dot(va, vb) / denom)


def loads_vec(raw: str | None) -> list[float]:
    if not raw:
        return []
    return json.loads(raw)


def dumps_vec(vec: list[float]) -> str:
    return json.dumps(vec)
