"""Optional CPU 1D CNN. Importing this module never imports PyTorch."""

from __future__ import annotations

import importlib.util
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from spectralab_ml.models.validation import finite_matrix, training_arrays

CHECKPOINT_FORMAT = "cnn-state-npz-v1"
CNN_ARCHITECTURE = "conv8-pool4-conv16-adapt64-linear-v1"


def validate_model_configuration(value: dict, *, expected_seed: int) -> None:
    required = {"architecture", "epochs", "learning_rate", "batch_size", "device", "seed"}
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("CNN artifact requires explicit versioned model_configuration; retrain")
    if value["architecture"] != CNN_ARCHITECTURE or value["device"] != "cpu":
        raise ValueError("CNN architecture or device is unsupported")
    if type(value["batch_size"]) is not int or value["batch_size"] != 32:
        raise ValueError("CNN batch size contract is unsupported")
    if type(value["epochs"]) is not int or not 1 <= value["epochs"] <= 1000:
        raise ValueError("CNN epochs must be an integer in [1,1000]")
    rate = value["learning_rate"]
    if type(rate) not in (int, float):
        raise ValueError("CNN learning rate must be a positive finite number")
    try:
        rate = float(rate)
    except OverflowError as exc:
        raise ValueError("CNN learning rate exceeds the finite numeric range") from exc
    if not math.isfinite(rate) or rate <= 0:
        raise ValueError("CNN learning rate must be a positive finite number")
    if (
        type(expected_seed) is not int
        or type(value["seed"]) is not int
        or value["seed"] != expected_seed
        or expected_seed < 0
    ):
        raise ValueError("CNN configuration seed does not match artifact seed")


def cnn_available() -> bool:
    return importlib.util.find_spec("torch") is not None


def _torch():
    try:
        import torch
    except ImportError as exc:
        raise ImportError("CNN requires the optional 'cnn' extra") from exc
    return torch


@dataclass
class CNN1D:
    seed: int = 42
    epochs: int = 30
    learning_rate: float = 0.003
    score_kind: str = "cnn_multilabel_logits"
    calibrated: bool = False

    def configuration(self) -> dict:
        result = {
            "architecture": CNN_ARCHITECTURE,
            "epochs": self.epochs,
            "learning_rate": self.learning_rate,
            "batch_size": 32,
            "device": "cpu",
            "seed": self.seed,
        }
        validate_model_configuration(result, expected_seed=self.seed)
        return result

    def _build(self, classes: int) -> None:
        torch = _torch()
        self.class_count = classes
        self.network = torch.nn.Sequential(
            torch.nn.Conv1d(1, 8, kernel_size=9, padding=4),
            torch.nn.ReLU(),
            torch.nn.MaxPool1d(4),
            torch.nn.Conv1d(8, 16, kernel_size=5, padding=2),
            torch.nn.ReLU(),
            torch.nn.AdaptiveMaxPool1d(64),
            torch.nn.Flatten(),
            torch.nn.Linear(16 * 64, classes),
        ).cpu()

    def fit(self, X: np.ndarray, y: np.ndarray) -> CNN1D:
        X, y = training_arrays(X, y)
        if X.shape[1] < 4:
            raise ValueError("CNN requires aligned N×L spectra and N×C labels")
        if np.any(np.abs(X) > np.finfo(np.float32).max):
            raise ValueError("CNN features exceed the finite float32 range")
        self.configuration()
        torch = _torch()
        torch.set_num_threads(1)
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(self.seed)
            self.input_points = X.shape[1]
            self._build(y.shape[1])
            features = torch.from_numpy(np.asarray(X, dtype=np.float32)).unsqueeze(1)
            labels = torch.from_numpy(np.asarray(y, dtype=np.float32))
            optimizer = torch.optim.Adam(self.network.parameters(), lr=self.learning_rate)
            loss_fn = torch.nn.BCEWithLogitsLoss()
            self.network.train()
            for _ in range(self.epochs):
                for index in range(0, len(X), 32):
                    optimizer.zero_grad()
                    loss = loss_fn(
                        self.network(features[index : index + 32]), labels[index : index + 32]
                    )
                    loss.backward()
                    optimizer.step()
        self.network.eval()
        return self

    def scores(self, X: np.ndarray) -> np.ndarray:
        X = finite_matrix(X, "CNN features")
        if X.shape[1] != self.input_points:
            raise ValueError("CNN input grid mismatch")
        if np.any(np.abs(X) > np.finfo(np.float32).max):
            raise ValueError("CNN features exceed the finite float32 range")
        torch = _torch()
        self.network.eval()
        with torch.no_grad():
            features = torch.from_numpy(np.asarray(X, dtype=np.float32)).unsqueeze(1)
            return self.network(features).numpy().astype(float)

    def save_checkpoint(self, path: Path) -> None:
        # Pure numeric NPZ avoids Python object deserialization, even for the optional runtime.
        with path.open("wb") as stream:
            np.savez_compressed(
                stream,
                **{
                    name: tensor.detach().cpu().numpy()
                    for name, tensor in self.network.state_dict().items()
                },
            )

    @classmethod
    def load_checkpoint(
        cls,
        path: Path,
        *,
        seed: int,
        input_points: int,
        class_count: int,
        epochs: int = 30,
        learning_rate: float = 0.003,
        architecture: str = CNN_ARCHITECTURE,
    ) -> CNN1D:
        torch = _torch()
        model = cls(seed=seed, epochs=epochs, learning_rate=learning_rate)
        config = model.configuration()
        validate_model_configuration({**config, "architecture": architecture}, expected_seed=seed)
        model.input_points = input_points
        model._build(class_count)
        expected = model.network.state_dict()
        with np.load(path, allow_pickle=False) as data:
            if set(data.files) != set(expected):
                raise ValueError("CNN checkpoint tensor names mismatch")
            tensors = {}
            for name, value in expected.items():
                array = data[name]
                if array.shape != tuple(value.shape) or array.dtype != np.float32:
                    raise ValueError("CNN checkpoint tensor shape/dtype mismatch")
                if not np.isfinite(array).all():
                    raise ValueError("CNN checkpoint contains nonfinite weights")
                tensors[name] = torch.from_numpy(array.copy())
        model.network.load_state_dict(tensors, strict=True)
        model.network.eval()
        return model
