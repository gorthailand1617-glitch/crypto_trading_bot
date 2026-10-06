import numpy as np
import logging

logger = logging.getLogger("TradingEngine")

class AdaptiveBrain:
    def __init__(self, learning_rate: float = 0.05):
        self.learning_rate = learning_rate
        # Features: cmo, vwap_dist, volume_delta, volatility
        self.weights = np.zeros(4)
        self.bias = 0.0
        # Online tracking of feature means and stds for scale normalization
        self.means = np.zeros(4)
        self.vars = np.ones(4)
        self.count = 0

    def _update_stats(self, x: np.ndarray):
        self.count += 1
        old_means = self.means.copy()
        self.means += (x - self.means) / self.count
        self.vars += (x - old_means) * (x - self.means)

    def _normalize(self, x: np.ndarray) -> np.ndarray:
        if self.count < 2:
            return x
        stds = np.sqrt(self.vars / (self.count - 1)) + 1e-9
        return (x - self.means) / stds

    def predict_proba_one(self, features: dict) -> float:
        # Features keys: cmo, vwap_dist, volume_delta, volatility
        x = np.array([
            features.get("cmo", 0.0),
            features.get("vwap_dist", 0.0),
            features.get("volume_delta", 0.0),
            features.get("volatility", 0.0)
        ], dtype=float)
        
        x_norm = self._normalize(x)
        z = np.dot(self.weights, x_norm) + self.bias
        # Sigmoid function
        prob = 1.0 / (1.0 + np.exp(-np.clip(z, -15.0, 15.0)))
        return float(prob)

    def learn_one(self, features: dict, y: int):
        x = np.array([
            features.get("cmo", 0.0),
            features.get("vwap_dist", 0.0),
            features.get("volume_delta", 0.0),
            features.get("volatility", 0.0)
        ], dtype=float)

        self._update_stats(x)
        x_norm = self._normalize(x)
        
        z = np.dot(self.weights, x_norm) + self.bias
        p = 1.0 / (1.0 + np.exp(-np.clip(z, -15.0, 15.0)))
        
        # SGD update rule
        error = p - float(y)
        self.weights -= self.learning_rate * error * x_norm
        self.bias -= self.learning_rate * error
        
        logger.info(f"🧠 [ADAPTIVE BRAIN] Learn Step: y={y}, p={p:.4f}, error={error:.4f}, weights={self.weights.tolist()}, bias={self.bias:.4f}")

    def get_state(self) -> dict:
        return {
            "weights": self.weights.tolist(),
            "bias": float(self.bias),
            "means": self.means.tolist(),
            "vars": self.vars.tolist(),
            "count": int(self.count),
            "learning_rate": float(self.learning_rate)
        }

    def set_state(self, state: dict):
        if not state:
            return
        self.weights = np.array(state.get("weights", [0.0]*4), dtype=float)
        self.bias = float(state.get("bias", 0.0))
        self.means = np.array(state.get("means", [0.0]*4), dtype=float)
        self.vars = np.array(state.get("vars", [1.0]*4), dtype=float)
        self.count = int(state.get("count", 0))
        self.learning_rate = float(state.get("learning_rate", 0.05))
