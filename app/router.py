import numpy as np
import yaml
import hashlib
from sentence_transformers import SentenceTransformer


class SemanticRouter:
    def __init__(self, model_name: str, config_path: str):
        self.model = SentenceTransformer(model_name, device="cpu")
        with open(config_path, "rb") as f:
            raw = f.read()
        cfg = yaml.safe_load(raw)
        self.version = hashlib.sha256(raw).hexdigest()[:12]
        # with open(config_path) as f:
        #     cfg = yaml.safe_load(f)
        self.threshold = cfg["threshold"]
        self.default = cfg["default"]["target"]

        self.names: list[str] = []
        self.targets: dict[str, dict] = {}
        self.utt_emb: list[np.ndarray] = []   # mỗi route: ma trận (n_utterances, 384)
        centroids = []

        for r in cfg["routes"]:
            emb = self.model.encode(r["utterances"], normalize_embeddings=True)
            self.utt_emb.append(emb)

            c = emb.mean(axis=0)
            centroids.append(c / np.linalg.norm(c))   # normalize lại sau khi lấy trung bình

            self.names.append(r["name"])
            self.targets[r["name"]] = r["target"]

        self.centroids = np.stack(centroids)          # (R, 384)

    def route(self, prompt: str, strategy: str = "centroid") -> dict:
        q = self.model.encode(prompt, normalize_embeddings=True)   # (384,)

        if strategy == "max":
            # Với mỗi route: lấy similarity cao nhất giữa prompt và từng utterance
            scores = np.array([float((E @ q).max()) for E in self.utt_emb])
        else:
            # So với centroid của mỗi route
            scores = self.centroids @ q

        i = int(scores.argmax())
        score = float(scores[i])

        if score < self.threshold:
            return {"route": "default", "score": score, "target": self.default}
        return {"route": self.names[i], "score": score, "target": self.targets[self.names[i]]}