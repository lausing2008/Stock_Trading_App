"""PyTorch LSTM — sequence model for price direction."""
from __future__ import annotations

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

from .base import BaseModel


class _LSTMNet(nn.Module):
    def __init__(self, n_features: int, hidden: int = 64, layers: int = 2, dropout: float = 0.2):
        super().__init__()
        self.lstm = nn.LSTM(
            input_size=n_features,
            hidden_size=hidden,
            num_layers=layers,
            dropout=dropout,
            batch_first=True,
        )
        self.head = nn.Linear(hidden, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out, _ = self.lstm(x)
        return torch.sigmoid(self.head(out[:, -1, :])).squeeze(-1)


def _windowed(X: np.ndarray, y: np.ndarray, seq_len: int) -> tuple[np.ndarray, np.ndarray]:
    xs, ys = [], []
    for i in range(seq_len, len(X)):
        xs.append(X[i - seq_len : i])
        ys.append(y[i])
    return np.stack(xs).astype(np.float32), np.array(ys, dtype=np.float32)


class LSTMModel(BaseModel):
    name = "lstm"

    def __init__(self, seq_len: int = 30, hidden: int = 64, epochs: int = 10, lr: float = 1e-3):
        self.seq_len = seq_len
        self.hidden = hidden
        self.epochs = epochs
        self.lr = lr
        self.net: _LSTMNet | None = None
        self.device = "cuda" if torch.cuda.is_available() else "cpu"

    def fit(self, X: np.ndarray, y: np.ndarray) -> None:
        Xs, ys = _windowed(X, y, self.seq_len)
        if len(Xs) == 0:
            raise ValueError("Insufficient data for LSTM training")
        self.net = _LSTMNet(n_features=X.shape[1], hidden=self.hidden).to(self.device)
        opt = torch.optim.Adam(self.net.parameters(), lr=self.lr)
        loss_fn = nn.BCELoss()

        ds = TensorDataset(torch.from_numpy(Xs), torch.from_numpy(ys))
        dl = DataLoader(ds, batch_size=64, shuffle=True)
        self.net.train()
        for _ in range(self.epochs):
            for xb, yb in dl:
                xb, yb = xb.to(self.device), yb.to(self.device)
                opt.zero_grad()
                pred = self.net(xb)
                loss = loss_fn(pred, yb)
                loss.backward()
                opt.step()

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        if self.net is None or len(X) < self.seq_len:
            return np.full(len(X), 0.5, dtype=np.float32)
        self.net.eval()
        Xs, _ = _windowed(X, np.zeros(len(X)), self.seq_len)
        with torch.no_grad():
            preds = self.net(torch.from_numpy(Xs).to(self.device)).cpu().numpy()
        # Pad leading positions where no full window exists
        pad = np.full(self.seq_len, 0.5, dtype=np.float32)
        return np.concatenate([pad, preds])
