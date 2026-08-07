"""
model.py — 园区安全智能调控平台 AI 模型定义
==============================================
包含两个核心类：
  - DensityPredictor:   人流/车流密度时序预测模型（TSMixer-Lite / GRU）
  - CongestionDetector: 异常检测器（拥堵/滞留/门闸异常）

接口规范：统一遵循 sklearn 风格（fit / predict / save / load）
框架：PyTorch（后续可迁移至 MindSpore）
"""

from __future__ import annotations

import json
import os
import warnings
from typing import Dict, List, Optional, Tuple, Union

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

# ============================================================================
# 工具函数
# ============================================================================


def _get_device(device: Union[str, torch.device, None] = None) -> torch.device:
    """解析设备：cuda > mps > cpu"""
    if device is not None:
        return torch.device(device)
    if torch.cuda.is_available():
        return torch.device("cuda")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")

# ============================================================================
# 神经网络模块定义
# ============================================================================


class TSMixerBlock(nn.Module):
    """
    TSMixer 基本块：时间混合 + 特征混合 + 残差连接

    对输入 (B, N, T, F) 依次做：
      1. Time Mixing  — 沿时间轴混合（转置后过 MLP）
      2. Feature Mixing — 沿特征轴混合（过 MLP）
      3. 两层均有残差连接 + LayerNorm
    """

    def __init__(
        self,
        seq_len: int,
        n_features: int,
        hidden_dim: int = 64,
        dropout: float = 0.1,
    ):
        super().__init__()

        # ---- Time Mixing: (B, N, F, T) → (B, N, F, T) ----
        self.time_mix = nn.Sequential(
            nn.Linear(seq_len, hidden_dim),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, seq_len),
            nn.Dropout(dropout),
        )
        self.time_norm = nn.LayerNorm(n_features)

        # ---- Feature Mixing: (B, N, T, F) → (B, N, T, F) ----
        self.feature_mix = nn.Sequential(
            nn.Linear(n_features, hidden_dim),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, n_features),
            nn.Dropout(dropout),
        )
        self.feature_norm = nn.LayerNorm(n_features)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (B, N, T, F)

        # Time mixing
        residual = x
        x_t = x.permute(0, 1, 3, 2)               # (B, N, F, T)
        x_t = self.time_mix(x_t)                    # (B, N, F, T)
        x_t = x_t.permute(0, 1, 3, 2)               # (B, N, T, F)
        x = self.time_norm(residual + x_t)           # 残差 + LN

        # Feature mixing
        residual = x
        x_f = self.feature_mix(x)                    # (B, N, T, F)
        x = self.feature_norm(residual + x_f)        # 残差 + LN

        return x


class DensityNet(nn.Module):
    """
    TSMixer-Lite：全 MLP 时序预测网络

    参数：
        window_size  : 历史窗口长度 T（分钟）
        n_features   : 每个节点每个时刻的特征数 F
        pred_horizon : 预测步长 H（分钟）
        hidden_dim   : 隐藏层维度
        num_layers   : TSMixerBlock 堆叠层数
        dropout      : Dropout 比例
    """

    def __init__(
        self,
        window_size: int = 6,
        n_features: int = 10,
        pred_horizon: int = 3,
        hidden_dim: int = 64,
        num_layers: int = 2,
        dropout: float = 0.1,
    ):
        super().__init__()
        self.window_size = window_size
        self.n_features = n_features
        self.pred_horizon = pred_horizon

        self.blocks = nn.ModuleList([
            TSMixerBlock(window_size, n_features, hidden_dim, dropout)
            for _ in range(num_layers)
        ])

        # 投影头：展平 (T, F) → 映射到未来 H 步
        flat_dim = window_size * n_features
        self.projection = nn.Sequential(
            nn.Linear(flat_dim, hidden_dim * 2),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim * 2, pred_horizon),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Args:
            x: (B, N, T, F)
        Returns:
            y: (B, N, H)  — 每个节点未来 H 步密度预测
        """
        for block in self.blocks:
            x = block(x)

        B, N, T, F = x.shape
        x = x.reshape(B, N, T * F)           # (B, N, T·F)
        x = self.projection(x)                # (B, N, H)
        return x


class DensityNetGRU(nn.Module):
    """
    GRU 时序预测网络（备选方案）

    当 TSMixer 效果不达预期或遇到兼容性问题时使用。
    """

    def __init__(
        self,
        n_features: int = 10,
        pred_horizon: int = 3,
        hidden_dim: int = 64,
        num_layers: int = 2,
        dropout: float = 0.1,
    ):
        super().__init__()
        self.n_features = n_features
        self.pred_horizon = pred_horizon

        self.gru = nn.GRU(
            input_size=n_features,
            hidden_size=hidden_dim,
            num_layers=num_layers,
            dropout=dropout if num_layers > 1 else 0.0,
            batch_first=True,
        )
        self.projection = nn.Linear(hidden_dim, pred_horizon)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Args:
            x: (B, N, T, F)
        Returns:
            y: (B, N, H)
        """
        B, N, T, F = x.shape
        x = x.reshape(B * N, T, F)            # (B·N, T, F)
        _, h_n = self.gru(x)                   # h_n: (num_layers, B·N, hidden_dim)
        h = h_n[-1]                            # 取最后一层: (B·N, hidden_dim)
        h = h.reshape(B, N, -1)                # (B, N, hidden_dim)
        out = self.projection(h)               # (B, N, H)
        return out


# ============================================================================
# DensityPredictor — 密度预测模型
# ============================================================================


class DensityPredictor:
    """
    人流/车流密度时序预测模型

    使用 TSMixer-Lite（推荐）或 GRU（备选）对未来 H 分钟的
    各节点密度进行预测。

    Parameters
    ----------
    window_size : int, default=60
        历史窗口长度（分钟）。
    pred_horizon : int, default=10
        预测步长（分钟）。
    hidden_dim : int, default=64
        隐层维度。
    num_layers : int, default=2
        TSMixer Block 或 GRU 层数。
    dropout : float, default=0.1
        Dropout 概率。
    model_type : {'tsmixer', 'gru'}, default='tsmixer'
        模型架构选择。
    device : str or torch.device, optional
        计算设备，默认自动选择（cuda > mps > cpu）。

    Attributes
    ----------
    model_ : nn.Module or None
        底层 PyTorch 模型，fit 后可用。
    is_fitted_ : bool
        是否已完成训练。
    feature_names_ : list of str or None
        特征名称列表（fit 时传入）。
    """

    VALID_MODEL_TYPES = ("tsmixer", "gru")

    def __init__(
        self,
        window_size: int = 6,
        pred_horizon: int = 3,
        hidden_dim: int = 64,
        num_layers: int = 2,
        dropout: float = 0.1,
        model_type: str = "tsmixer",
        device: Union[str, torch.device, None] = None,
    ):
        if model_type not in self.VALID_MODEL_TYPES:
            raise ValueError(
                f"model_type 必须为 {self.VALID_MODEL_TYPES}，收到: {model_type}"
            )

        self.window_size = window_size
        self.pred_horizon = pred_horizon
        self.hidden_dim = hidden_dim
        self.num_layers = num_layers
        self.dropout = dropout
        self.model_type = model_type
        self.device = _get_device(device)

        self.model_: Optional[nn.Module] = None
        self.is_fitted_ = False
        self.feature_names_: Optional[List[str]] = None
        self._n_features: Optional[int] = None
        self._training_history: Dict[str, List[float]] = {"train_loss": [], "val_loss": []}

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _build_model(self, n_features: int) -> None:
        """按指定特征数构建底层网络"""
        if self.model_type == "tsmixer":
            self.model_ = DensityNet(
                window_size=self.window_size,
                n_features=n_features,
                pred_horizon=self.pred_horizon,
                hidden_dim=self.hidden_dim,
                num_layers=self.num_layers,
                dropout=self.dropout,
            )
        else:  # gru
            self.model_ = DensityNetGRU(
                n_features=n_features,
                pred_horizon=self.pred_horizon,
                hidden_dim=self.hidden_dim,
                num_layers=self.num_layers,
                dropout=self.dropout,
            )
        self.model_.to(self.device)
        self._n_features = n_features

    def _validate_input(
        self, X: np.ndarray, y: Optional[np.ndarray] = None, fit: bool = False
    ) -> Tuple[int, int]:
        """
        校验输入形状并返回 (n_features, n_nodes)。

        X 期望形状:
          - fit:  (n_samples, n_nodes, window_size, n_features)
          - predict: (n_samples, n_nodes, window_size, n_features)
        y 期望形状 (fit 时):
          - (n_samples, n_nodes, pred_horizon)
        """
        if X.ndim != 4:
            raise ValueError(
                f"X 期望 4 维 (n_samples, n_nodes, window_size, n_features)，"
                f"收到 {X.ndim} 维: {X.shape}"
            )
        n_samples, n_nodes, w_size, n_feat = X.shape

        if fit and w_size != self.window_size:
            raise ValueError(
                f"X 的窗口长度 ({w_size}) 与初始化 window_size "
                f"({self.window_size}) 不一致"
            )

        if y is not None:
            if y.ndim != 3:
                raise ValueError(
                    f"y 期望 3 维 (n_samples, n_nodes, pred_horizon)，"
                    f"收到 {y.ndim} 维: {y.shape}"
                )
            if y.shape[0] != n_samples or y.shape[1] != n_nodes:
                raise ValueError(
                    f"X {X.shape} 与 y {y.shape} 的前两维不一致"
                )
            if y.shape[2] != self.pred_horizon:
                raise ValueError(
                    f"y 的预测步长 ({y.shape[2]}) 与初始化 pred_horizon "
                    f"({self.pred_horizon}) 不一致"
                )

        return n_feat, n_nodes

    # ------------------------------------------------------------------
    # 训练
    # ------------------------------------------------------------------

    def fit(
        self,
        X: np.ndarray,
        y: np.ndarray,
        *,
        epochs: int = 100,
        batch_size: int = 32,
        lr: float = 1e-3,
        val_split: float = 0.15,
        patience: int = 10,
        feature_names: Optional[List[str]] = None,
        validation_data: Optional[Tuple[np.ndarray, np.ndarray]] = None,
        init_from: bool = False,
        verbose: bool = True,
        **fit_params,
    ) -> "DensityPredictor":
        """
        训练密度预测模型。

        Parameters
        ----------
        X : np.ndarray, shape (n_samples, n_nodes, window_size, n_features)
            历史特征序列。
        y : np.ndarray, shape (n_samples, n_nodes, pred_horizon)
            目标密度值（未来 H 步）。
        epochs : int, default=100
            最大训练轮数。
        batch_size : int, default=32
            批次大小。
        lr : float, default=1e-3
            初始学习率（Adam）。
        val_split : float, default=0.15
            验证集比例（从训练数据尾部切分）。传 0.0 且不提供
            validation_data 时，不使用内部验证集（以训练损失做早停）。
        patience : int, default=10
            早停耐心值（验证损失连续不降则停）。
        feature_names : list of str, optional
            特征名称列表（仅用于记录，不参与计算）。
        validation_data : tuple (X_val, y_val), optional
            外部传入的验证集，优先级高于 val_split。提供后
            直接用它做早停，不再从 X 内部切分。
        init_from : bool, default=False
            增量训练开关：
              - False：每次重建模型，从零训练（清空参数）
              - True：不重建模型，沿用当前 self.model_ 的参数继续训练。
                调用前需先加载已训练模型（或先 fit 过一次）。
        verbose : bool, default=True
            是否打印训练进度。

        Returns
        -------
        self : DensityPredictor
        """
        n_feat, n_nodes = self._validate_input(X, y, fit=True)
        self.feature_names_ = feature_names

        if init_from:
            # 增量训练：复用现有模型参数，不重建
            if self.model_ is None or not self.is_fitted_:
                raise RuntimeError(
                    "init_from=True 但当前没有已训练的模型，"
                    "请先 load 已训练模型或先 fit 一次"
                )
            # 校验模型结构匹配
            if getattr(self.model_, "window_size", None) != self.window_size or \
               getattr(self.model_, "pred_horizon", None) != self.pred_horizon or \
               self._n_features != n_feat:
                raise ValueError(
                    "增量训练要求模型结构与新数据一致："
                    f"当前 window={getattr(self.model_, 'window_size', '?')}, "
                    f"pred={getattr(self.model_, 'pred_horizon', '?')}, "
                    f"feat={self._n_features}；新数据 window={self.window_size}, "
                    f"pred={self.pred_horizon}, feat={n_feat}"
                )
        else:
            # 从零训练：重建模型
            self._build_model(n_feat)

        # ---- 划分训练/验证集（按时间顺序，避免未来信息泄露） ----
        if validation_data is not None:
            X_val, y_val = validation_data
            if X_val.ndim != 4 or X_val.shape[1:] != X.shape[1:]:
                raise ValueError(
                    f"validation_data 的 X_val 形状 {X_val.shape} "
                    f"与 X {X.shape} 不一致"
                )
            X_train, y_train = X, y
            n_train, n_val = X_train.shape[0], X_val.shape[0]
        else:
            n_total = X.shape[0]
            n_val = max(1, int(n_total * val_split)) if val_split > 0 else 0
            n_train = n_total - n_val
            X_train, X_val = X[:n_train], X[n_train:]
            y_train, y_val = y[:n_train], y[n_train:]

        train_ds = TensorDataset(
            torch.from_numpy(X_train).float(),
            torch.from_numpy(y_train).float(),
        )
        train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True)

        val_loader = None
        if n_val > 0:
            val_ds = TensorDataset(
                torch.from_numpy(X_val).float(),
                torch.from_numpy(y_val).float(),
            )
            val_loader = DataLoader(val_ds, batch_size=batch_size, shuffle=False)

        # ---- 优化器 & 损失 & 调度器 ----
        optimizer = torch.optim.Adam(self.model_.parameters(), lr=lr)
        scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
            optimizer, mode="min", factor=0.5, patience=5
        )
        criterion = nn.MSELoss()

        best_val_loss = float("inf")
        best_state_dict = None
        epochs_no_improve = 0
        self._training_history = {"train_loss": [], "val_loss": []}

        # ---- 训练循环 ----
        for epoch in range(1, epochs + 1):
            # -- 训练阶段 --
            self.model_.train()
            train_loss_sum = 0.0
            for batch_x, batch_y in train_loader:
                batch_x = batch_x.to(self.device)
                batch_y = batch_y.to(self.device)

                optimizer.zero_grad()
                pred = self.model_(batch_x)
                loss = criterion(pred, batch_y)
                loss.backward()
                optimizer.step()

                train_loss_sum += loss.item() * batch_x.size(0)

            avg_train_loss = train_loss_sum / n_train

            # -- 验证阶段 --
            self.model_.eval()
            if val_loader is not None:
                val_loss_sum = 0.0
                with torch.no_grad():
                    for batch_x, batch_y in val_loader:
                        batch_x = batch_x.to(self.device)
                        batch_y = batch_y.to(self.device)
                        pred = self.model_(batch_x)
                        loss = criterion(pred, batch_y)
                        val_loss_sum += loss.item() * batch_x.size(0)
                avg_val_loss = val_loss_sum / max(n_val, 1)
            else:
                avg_val_loss = avg_train_loss  # 无验证集时以训练损失近似

            self._training_history["train_loss"].append(avg_train_loss)
            self._training_history["val_loss"].append(avg_val_loss)

            scheduler.step(avg_val_loss)

            if verbose and (epoch % 10 == 0 or epoch == 1):
                print(
                    f"Epoch {epoch:3d}/{epochs} | "
                    f"train_loss: {avg_train_loss:.6f} | "
                    f"val_loss: {avg_val_loss:.6f} | "
                    f"lr: {scheduler.get_last_lr()[0]:.2e}"
                )

            # -- 早停 & 保存最佳模型 --
            if avg_val_loss < best_val_loss:
                best_val_loss = avg_val_loss
                best_state_dict = {k: v.cpu().clone() for k, v in self.model_.state_dict().items()}
                epochs_no_improve = 0
            else:
                epochs_no_improve += 1
                if epochs_no_improve >= patience:
                    if verbose:
                        print(f"早停于 epoch {epoch}（{patience} 轮未改善）")
                    break

        # 恢复最佳权重
        if best_state_dict is not None:
            self.model_.load_state_dict(best_state_dict)
        self.model_.eval()
        self.is_fitted_ = True

        if verbose:
            print(f"训练完成 | 最佳 val_loss: {best_val_loss:.6f}")

        return self

    # ------------------------------------------------------------------
    # 预测
    # ------------------------------------------------------------------

    def predict(self, X: np.ndarray, batch_size: int = 256) -> np.ndarray:
        """
        预测未来人流/车流密度。

        Parameters
        ----------
        X : np.ndarray, shape (n_samples, n_nodes, window_size, n_features)
            历史特征序列。
        batch_size : int, default=256
            分批推理的批大小。样本量很大时避免一次性占满显存。

        Returns
        -------
        y_pred : np.ndarray, shape (n_samples, n_nodes, pred_horizon)
            每个节点未来 H 步的密度预测值。
        """
        if not self.is_fitted_ or self.model_ is None:
            raise RuntimeError("模型尚未训练，请先调用 fit()")

        n_feat, n_nodes = self._validate_input(X, fit=False)
        n_samples = X.shape[0]

        self.model_.eval()
        y_pred_list = []
        with torch.no_grad():
            for start in range(0, n_samples, batch_size):
                end = min(start + batch_size, n_samples)
                x_tensor = torch.from_numpy(X[start:end]).float().to(self.device)
                y_batch = self.model_(x_tensor)
                y_pred_list.append(y_batch.cpu().numpy())

        y_pred = np.concatenate(y_pred_list, axis=0)
        return y_pred

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------

    def save(self, path: str) -> None:
        """
        保存模型权重与配置到指定目录（目录不存在则自动创建）。

        产出：
            {path}/model_state.pt   — PyTorch 权重
            {path}/config.json     — 超参与元信息
        """
        if not self.is_fitted_ or self.model_ is None:
            raise RuntimeError("模型尚未训练，没有可保存的权重")

        os.makedirs(path, exist_ok=True)

        # 权重
        torch.save(self.model_.state_dict(), os.path.join(path, "model_state.pt"))

        # 配置
        config = {
            "model_type": self.model_type,
            "window_size": self.window_size,
            "pred_horizon": self.pred_horizon,
            "hidden_dim": self.hidden_dim,
            "num_layers": self.num_layers,
            "dropout": self.dropout,
            "n_features": self._n_features,
            "feature_names": self.feature_names_,
            "is_fitted": self.is_fitted_,
            "training_history": self._training_history,
        }
        with open(os.path.join(path, "config.json"), "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path: str, device: Union[str, torch.device, None] = None) -> "DensityPredictor":
        """
        从目录加载已训练模型。

        Parameters
        ----------
        path : str
            包含 model_state.pt 和 config.json 的目录路径。
        device : str or torch.device, optional
            加载到的设备。

        Returns
        -------
        model : DensityPredictor
        """
        config_path = os.path.join(path, "config.json")
        state_path = os.path.join(path, "model_state.pt")

        if not os.path.exists(config_path):
            raise FileNotFoundError(f"配置文件不存在: {config_path}")
        if not os.path.exists(state_path):
            raise FileNotFoundError(f"权重文件不存在: {state_path}")

        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)

        instance = cls(
            window_size=config["window_size"],
            pred_horizon=config["pred_horizon"],
            hidden_dim=config["hidden_dim"],
            num_layers=config["num_layers"],
            dropout=config["dropout"],
            model_type=config["model_type"],
            device=device,
        )
        instance.feature_names_ = config.get("feature_names")
        instance._n_features = config["n_features"]
        instance._build_model(config["n_features"])
        instance.model_.load_state_dict(torch.load(state_path, map_location=instance.device))
        instance.model_.eval()
        instance.is_fitted_ = True
        instance._training_history = config.get("training_history", {})

        return instance


# ============================================================================
# CongestionDetector — 异常检测器
# ============================================================================


class CongestionDetector:
    """
    园区异常检测器

    基于统计基线实现三类异常检测：
      1. 异常拥堵 — 密度超历史分位数 + 预测残差验证
      2. 人员滞留 — 密度变化率持续低于阈值
      3. 门闸异常 — 状态码故障 / 流速异常 / 响应失灵

    Parameters
    ----------
    density_percentile : float, default=95.0
        拥堵判定的密度分位数（P95）。
    loitering_duration : int, default=5
        滞留判定所需的最小连续帧数（分钟）。
    loitering_rate_threshold : float, default=0.01
        滞留判定的密度变化率阈值（人/m²/分钟）。
    sigma_threshold : float, default=2.0
        残差异常阈值（σ 倍数），用于拥堵确认。
    gate_flow_sigma : float, default=3.0
        门闸流速异常的 σ 倍数。
    density_feature_idx : int, default=0
        密度特征在 X 中的列索引。
    gate_status_feature_idx : int, default=2
        门闸状态码在 X 中的列索引。
    gate_flow_feature_idx : int, default=3
        门闸流速在 X 中的列索引。

    Attributes
    ----------
    is_fitted_ : bool
    node_stats_ : dict
        以 node 索引为 key 的统计量字典。
        {"0": {"density_mean", "density_std", "density_p85", "density_p95",
               "gate_flow_mean", "gate_flow_std", "density_change_mean",
               "density_change_std"}, ...}
    """

    def __init__(
        self,
        density_percentile: float = 95.0,
        loitering_duration: int = 5,
        loitering_rate_threshold: float = 0.01,
        sigma_threshold: float = 2.0,
        gate_flow_sigma: float = 3.0,
        density_feature_idx: int = 0,
        gate_status_feature_idx: int = 3,
        gate_flow_feature_idx: int = 4,
        min_p95_threshold: float = 0.05,
    ):
        if not 50.0 <= density_percentile <= 100.0:
            raise ValueError(f"density_percentile 应在 [50, 100]，收到 {density_percentile}")
        if loitering_duration < 1:
            raise ValueError(f"loitering_duration 应 ≥ 1，收到 {loitering_duration}")

        self.density_percentile = density_percentile
        self.loitering_duration = loitering_duration
        self.loitering_rate_threshold = loitering_rate_threshold
        self.sigma_threshold = sigma_threshold
        self.min_p95_threshold = min_p95_threshold
        self.gate_flow_sigma = gate_flow_sigma
        self.density_feature_idx = density_feature_idx
        self.gate_status_feature_idx = gate_status_feature_idx
        self.gate_flow_feature_idx = gate_flow_feature_idx

        # 拟合后填充
        self.is_fitted_ = False
        self.node_stats_: Dict[str, dict] = {}
        self.n_nodes_: int = 0
        self.feature_names_: Optional[List[str]] = None

        # 运行时状态（跨 predict 调用持续追踪）
        self._loitering_counter: Dict[str, int] = {}    # node_idx → 连续低流速帧数
        self._congestion_counter: Dict[str, int] = {}   # node_idx → 连续拥堵帧数
        self._alerted_events: set = set()                # 已告警事件去重（event_hash）

    # ------------------------------------------------------------------
    # 拟合
    # ------------------------------------------------------------------

    def fit(
        self,
        X: np.ndarray,
        *,
        feature_names: Optional[List[str]] = None,
        **fit_params,
    ) -> "CongestionDetector":
        """
        基于历史正常数据计算各节点统计基线。

        Parameters
        ----------
        X : np.ndarray, shape (n_timesteps, n_nodes, n_features)
            历史"正常"数据（不包含已知异常时段）。
        feature_names : list of str, optional
            特征名称，与列顺序对应。

        Returns
        -------
        self : CongestionDetector
        """
        if X.ndim != 3:
            raise ValueError(
                f"X 期望 3 维 (n_timesteps, n_nodes, n_features)，"
                f"收到 {X.ndim} 维: {X.shape}"
            )

        n_timesteps, n_nodes, n_features = X.shape
        self.n_nodes_ = n_nodes
        self.feature_names_ = feature_names

        # 检查特征索引是否越界
        max_idx = max(
            self.density_feature_idx,
            self.gate_status_feature_idx,
            self.gate_flow_feature_idx,
        )
        if max_idx >= n_features:
            raise ValueError(
                f"特征索引 ({max_idx}) 超出特征总数 ({n_features})，"
                f"请检查 *_feature_idx 参数"
            )

        self.node_stats_ = {}

        for node_i in range(n_nodes):
            node_data = X[:, node_i, :]  # (T, F)
            density = node_data[:, self.density_feature_idx]
            gate_flow = node_data[:, self.gate_flow_feature_idx]

            # 密度统计
            density_mean = float(np.mean(density))
            density_std = float(np.std(density))
            density_p85 = float(np.percentile(density, 85.0))
            density_p95 = float(np.percentile(density, self.density_percentile))

            # 密度变化率统计（相邻时刻差分）
            density_diff = np.diff(density)
            density_change_mean = float(np.mean(np.abs(density_diff)))
            density_change_std = float(np.std(np.abs(density_diff)))

            # 门闸流速统计（仅对流速 > 0 的样本计算，避免关闭状态拉低均值）
            flow_positive = gate_flow[gate_flow > 0]
            if len(flow_positive) > 0:
                gate_flow_mean = float(np.mean(flow_positive))
                gate_flow_std = float(np.std(flow_positive))
            else:
                gate_flow_mean = 0.0
                gate_flow_std = 0.0

            self.node_stats_[str(node_i)] = {
                "density_mean": density_mean,
                "density_std": density_std,
                "density_p85": density_p85,
                "density_p95": density_p95,
                "density_change_mean": density_change_mean,
                "density_change_std": density_change_std,
                "gate_flow_mean": gate_flow_mean,
                "gate_flow_std": gate_flow_std,
            }

        self._reset_state()
        self.is_fitted_ = True
        return self

    def _reset_state(self) -> None:
        """重置运行时追踪状态"""
        self._loitering_counter = {}
        self._congestion_counter = {}
        self._alerted_events.clear()

    # ------------------------------------------------------------------
    # 三类异常检测
    # ------------------------------------------------------------------

    def _detect_congestion(
        self,
        current_density: np.ndarray,
        X_pred: Optional[np.ndarray],
    ) -> List[dict]:
        """
        检测异常拥堵。

        current_density: (n_nodes,) 当前密度
        X_pred: (n_nodes, pred_horizon) DensityPredictor 输出（可为 None）

        等级判定：
          - L1（关注）：密度 > P85 且 <= P95（尚未到拥堵，但需关注）
          - L2（预警）：密度 > P95（确认拥堵）
          - L3（严重）：密度 > 1.5 × P95（严重拥堵）

        防误报：
          - 节点 P95 阈值过低（< 0.05，即历史上几乎无人）则跳过，
            避免天桥/地下通道等天然空节点微小波动就报警
          - L1 也要求连续 3 帧，过滤瞬时抖动
        """
        # 节点 P95 阈值下限：低于此值视为"天然空节点"，不参与拥堵/关注判定
        min_p95_threshold = self.min_p95_threshold

        events = []
        for node_i in range(self.n_nodes_):
            stats = self.node_stats_[str(node_i)]
            density = float(current_density[node_i])

            # ---- 天然空节点过滤：P95 阈值过低则跳过 ----
            if stats["density_p95"] < min_p95_threshold:
                self._congestion_counter[str(node_i)] = 0
                continue

            # ---- L1 关注档：密度超 P85 但未达 P95 ----
            if density <= stats["density_p85"]:
                self._congestion_counter[str(node_i)] = 0
                continue

            if density <= stats["density_p95"]:
                # 超 P85 未达 P95 → L1 关注（需连续 3 帧，过滤瞬时抖动）
                self._congestion_counter[str(node_i)] = (
                    self._congestion_counter.get(str(node_i), 0) + 1
                )
                if self._congestion_counter[str(node_i)] < 3:
                    continue
                events.append({
                    "type": "congestion",
                    "node_id": node_i,
                    "severity": "L1",
                    "current_density": round(density, 4),
                    "threshold_p85": round(stats["density_p85"], 4),
                    "threshold_p95": round(stats["density_p95"], 4),
                    "exceed_ratio": round(density / max(stats["density_p95"], 1e-6), 2),
                    "predicted_duration_min": None,
                    "timestamp": None,
                })
                continue

            # ---- L2/L3：密度超过 P95 ----
            # 条件 2（可选）：超过预测值 + 2σ
            if X_pred is not None:
                pred_now = float(X_pred[node_i, 0])  # 预测的第一步即为"此刻"
                threshold = pred_now + self.sigma_threshold * max(stats["density_std"], 1e-6)
                if density <= threshold:
                    self._congestion_counter[str(node_i)] = 0
                    continue

            # 持续帧计数
            self._congestion_counter[str(node_i)] = (
                self._congestion_counter.get(str(node_i), 0) + 1
            )
            if self._congestion_counter[str(node_i)] < 3:
                continue  # 需连续 3 帧才告警

            # 估算持续时间（预测曲线何时回落到 P85 以下）
            duration_est = None
            if X_pred is not None:
                pred_series = X_pred[node_i, 1:]  # 未来几步
                below_mask = pred_series < stats["density_p85"]
                if np.any(below_mask):
                    duration_est = int(np.argmax(below_mask)) + 1  # 分钟后回落

            severity = "L3" if density > stats["density_p95"] * 1.5 else "L2"

            event = {
                "type": "congestion",
                "node_id": node_i,
                "severity": severity,
                "current_density": round(density, 4),
                "threshold_p85": round(stats["density_p85"], 4),
                "threshold_p95": round(stats["density_p95"], 4),
                "exceed_ratio": round(density / max(stats["density_p95"], 1e-6), 2),
                "predicted_duration_min": duration_est,
                "timestamp": None,  # 由调用方填入
            }
            events.append(event)

        return events

    def _detect_loitering(self, density_recent: np.ndarray) -> List[dict]:
        """
        检测人员滞留。

        density_recent: (n_recent_frames, n_nodes) 最近若干帧的密度
        """
        events = []
        if density_recent.shape[0] < 2:
            return events  # 至少需要 2 帧才能算变化率

        for node_i in range(self.n_nodes_):
            stats = self.node_stats_[str(node_i)]
            node_density = density_recent[:, node_i]

            # 计算最近帧的平均绝对变化率
            diffs = np.abs(np.diff(node_density))
            recent_rate = float(np.mean(diffs))

            # 变化率低于阈值 → 可能滞留
            if recent_rate < self.loitering_rate_threshold:
                self._loitering_counter[str(node_i)] = (
                    self._loitering_counter.get(str(node_i), 0) + 1
                )
            else:
                self._loitering_counter[str(node_i)] = 0
                continue

            if self._loitering_counter[str(node_i)] >= self.loitering_duration:
                severity = (
                    "L3" if self._loitering_counter[str(node_i)] >= self.loitering_duration * 2
                    else "L2"
                )
                event = {
                    "type": "loitering",
                    "node_id": node_i,
                    "severity": severity,
                    "density_change_rate": round(recent_rate, 6),
                    "rate_threshold": self.loitering_rate_threshold,
                    "sustained_frames": self._loitering_counter[str(node_i)],
                    "current_density": round(float(node_density[-1]), 4),
                    "timestamp": None,
                }
                events.append(event)

        return events

    def _detect_gate_anomaly(self, gate_status: np.ndarray, gate_flow: np.ndarray) -> List[dict]:
        """
        检测门闸异常。

        gate_status: (n_nodes,) 门闸状态码
        gate_flow:   (n_nodes,) 门闸流速
        """
        events = []
        for node_i in range(self.n_nodes_):
            status = int(gate_status[node_i])
            flow = float(gate_flow[node_i])

            # 规则 1：硬件故障
            if status == 3:
                events.append({
                    "type": "gate_anomaly",
                    "node_id": node_i,
                    "severity": "L3",
                    "subtype": "hardware_fault",
                    "gate_status": status,
                    "detail": "门闸上报故障状态",
                    "timestamp": None,
                })
                continue

            # 规则 2：流速为零但状态非关闭 → 疑似传感器故障
            if flow == 0.0 and status != 0:
                events.append({
                    "type": "gate_anomaly",
                    "node_id": node_i,
                    "severity": "L2",
                    "subtype": "sensor_suspect",
                    "gate_status": status,
                    "gate_flow_rate": flow,
                    "detail": "门闸开放但流速为零，疑似传感器异常",
                    "timestamp": None,
                })
                continue

            # 规则 3：流速统计异常（低于历史均值 3σ）
            stats = self.node_stats_[str(node_i)]
            if stats["gate_flow_std"] > 0 and flow > 0:
                z_score = (flow - stats["gate_flow_mean"]) / stats["gate_flow_std"]
                if z_score < -self.gate_flow_sigma:
                    events.append({
                        "type": "gate_anomaly",
                        "node_id": node_i,
                        "severity": "L2",
                        "subtype": "flow_drop",
                        "gate_status": status,
                        "gate_flow_rate": flow,
                        "expected_flow": round(stats["gate_flow_mean"], 4),
                        "z_score": round(z_score, 2),
                        "detail": f"流速异常偏低（z={z_score:.1f}σ）",
                        "timestamp": None,
                    })

        return events

    # ------------------------------------------------------------------
    # 预测（检测）
    # ------------------------------------------------------------------

    def predict(
        self,
        X_current: np.ndarray,
        X_pred: Optional[np.ndarray] = None,
        gate_status: Optional[np.ndarray] = None,
    ) -> List[dict]:
        """
        对当前时刻进行异常检测。

        Parameters
        ----------
        X_current : np.ndarray
            近期数据，支持两种形状：
              - (n_recent_timesteps, n_nodes, n_features)：多帧（含历史），可检测滞留
              - (n_nodes, n_features)：单帧，仅检测拥堵和门闸异常
        X_pred : np.ndarray, shape (n_nodes, pred_horizon), optional
            DensityPredictor 的预测输出，用于拥堵趋势验证。
        gate_status : np.ndarray, shape (n_nodes,), optional
            单独传入的门闸状态码。为 None 则从 X_current 中提取。

        Returns
        -------
        events : list of dict
            异常事件列表，每项包含 type / node_id / severity / detail 等字段。
        """
        if not self.is_fitted_:
            raise RuntimeError("检测器尚未拟合，请先调用 fit()")

        # 统一为 3 维
        if X_current.ndim == 2:
            X_current = X_current[np.newaxis, :, :]  # (1, N, F)
        elif X_current.ndim != 3:
            raise ValueError(
                f"X_current 期望 2 维或 3 维，收到 {X_current.ndim} 维: {X_current.shape}"
            )

        if X_current.shape[1] != self.n_nodes_:
            raise ValueError(
                f"X_current 节点数 ({X_current.shape[1]}) 与拟合时 "
                f"({self.n_nodes_}) 不一致"
            )

        # 提取特征列
        current_density = X_current[-1, :, self.density_feature_idx]       # 最新帧密度
        current_gate_status = X_current[-1, :, self.gate_status_feature_idx]
        current_gate_flow = X_current[-1, :, self.gate_flow_feature_idx]

        if gate_status is not None:
            current_gate_status = gate_status

        # 密度历史（用于滞留检测）
        density_recent = X_current[:, :, self.density_feature_idx]  # (T, N)

        # ---- 执行三类检测 ----
        events = []
        events.extend(self._detect_congestion(current_density, X_pred))
        events.extend(self._detect_loitering(density_recent))
        events.extend(self._detect_gate_anomaly(current_gate_status, current_gate_flow))

        # 去重（同 node + 同 type + 同等级在短时间内不重复告警）
        # 注意：key 含 severity，避免 L1 关注占用后拦截后续 L2/L3 拥堵
        deduped = []
        for e in events:
            key = f"{e['type']}_{e['severity']}_{e['node_id']}"
            if key not in self._alerted_events:
                self._alerted_events.add(key)
                deduped.append(e)

        return deduped

    def reset_alerts(self) -> None:
        """清空告警去重记录（如切换时段时调用）。"""
        self._alerted_events.clear()
        self._loitering_counter.clear()
        self._congestion_counter.clear()

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------

    def save(self, path: str) -> None:
        """
        保存检测器统计量与配置到指定目录。

        产出：
            {path}/statistics.json  — 各节点统计基线
            {path}/config.json      — 阈值参数
        """
        if not self.is_fitted_:
            raise RuntimeError("检测器尚未拟合，没有可保存的统计量")

        os.makedirs(path, exist_ok=True)

        stats_payload = {
            "n_nodes": self.n_nodes_,
            "feature_names": self.feature_names_,
            "node_stats": self.node_stats_,
        }
        with open(os.path.join(path, "statistics.json"), "w", encoding="utf-8") as f:
            json.dump(stats_payload, f, ensure_ascii=False, indent=2)

        config = {
            "density_percentile": self.density_percentile,
            "loitering_duration": self.loitering_duration,
            "loitering_rate_threshold": self.loitering_rate_threshold,
            "sigma_threshold": self.sigma_threshold,
            "gate_flow_sigma": self.gate_flow_sigma,
            "density_feature_idx": self.density_feature_idx,
            "gate_status_feature_idx": self.gate_status_feature_idx,
            "gate_flow_feature_idx": self.gate_flow_feature_idx,
        }
        with open(os.path.join(path, "config.json"), "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path: str) -> "CongestionDetector":
        """
        从目录加载已拟合的检测器。

        Parameters
        ----------
        path : str
            包含 statistics.json 和 config.json 的目录路径。

        Returns
        -------
        detector : CongestionDetector
        """
        config_path = os.path.join(path, "config.json")
        stats_path = os.path.join(path, "statistics.json")

        if not os.path.exists(config_path):
            raise FileNotFoundError(f"配置文件不存在: {config_path}")
        if not os.path.exists(stats_path):
            raise FileNotFoundError(f"统计文件不存在: {stats_path}")

        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        with open(stats_path, "r", encoding="utf-8") as f:
            stats_payload = json.load(f)

        instance = cls(
            density_percentile=config["density_percentile"],
            loitering_duration=config["loitering_duration"],
            loitering_rate_threshold=config["loitering_rate_threshold"],
            sigma_threshold=config["sigma_threshold"],
            gate_flow_sigma=config["gate_flow_sigma"],
            density_feature_idx=config["density_feature_idx"],
            gate_status_feature_idx=config["gate_status_feature_idx"],
            gate_flow_feature_idx=config["gate_flow_feature_idx"],
        )
        instance.n_nodes_ = stats_payload["n_nodes"]
        instance.feature_names_ = stats_payload.get("feature_names")
        instance.node_stats_ = stats_payload["node_stats"]
        instance.is_fitted_ = True
        instance._reset_state()

        return instance


# ============================================================================
# 简易测试入口（开发期自检用，正式环境可删除）
# ============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("DensityPredictor & CongestionDetector — 自检")
    print("=" * 60)

    # ---- DensityPredictor 自检 ----
    print("\n[1/4] 初始化 DensityPredictor (TSMixer) ...")
    dp = DensityPredictor(
        window_size=12,   # 用较短窗口加速自检
        pred_horizon=4,
        hidden_dim=16,
        num_layers=1,
        model_type="tsmixer",
    )
    print(f"  设备: {dp.device}")
    print(f"  模型类型: {dp.model_type}")

    print("\n[2/4] 生成合成数据并训练 ...")
    n_samples, n_nodes, n_feat = 200, 5, 5
    X_syn = np.random.randn(n_samples, n_nodes, dp.window_size, n_feat).astype(np.float32)
    # 目标：用最后一步密度 + 噪声作为未来 4 步
    y_syn = np.tile(X_syn[:, :, -1:, 0], (1, 1, dp.pred_horizon))
    y_syn += np.random.randn(*y_syn.shape).astype(np.float32) * 0.05

    dp.fit(X_syn, y_syn, epochs=5, batch_size=16, val_split=0.2, patience=3, verbose=False)
    assert dp.is_fitted_

    print("\n[3/4] 预测并检查形状 ...")
    X_test = np.random.randn(3, n_nodes, dp.window_size, n_feat).astype(np.float32)
    y_hat = dp.predict(X_test)
    assert y_hat.shape == (3, n_nodes, dp.pred_horizon), f"形状错误: {y_hat.shape}"
    print(f"  输入: {X_test.shape} → 输出: {y_hat.shape} ✓")

    # ---- CongestionDetector 自检 ----
    print("\n[4/4] 初始化并测试 CongestionDetector ...")
    cd = CongestionDetector(
        density_percentile=90.0,
        loitering_duration=3,
        density_feature_idx=0,
        gate_status_feature_idx=3,
        gate_flow_feature_idx=4,
    )

    # 合成历史数据（节点 0 正常，节点 1 密度高）
    n_hist = 100
    X_hist = np.random.randn(n_hist, n_nodes, n_feat).astype(np.float32) * 0.3 + 1.0
    X_hist[:, 0, 0] += 0.5   # 节点 0 略高

    cd.fit(X_hist)

    # 当前帧：节点 4 密度异常高
    X_now = np.random.randn(10, n_nodes, n_feat).astype(np.float32)
    X_now[-1, 4, 0] = 5.0    # 远超 P90
    X_now[-1, 4, 3] = 3      # 门闸故障
    X_now[-1, 4, 4] = 0.0    # 流速为零

    events = cd.predict(X_now)
    print(f"  检测到 {len(events)} 个异常事件:")
    for ev in events:
        print(f"    - [{ev['severity']}] {ev['type']} @ node={ev['node_id']}")

    print("\n" + "=" * 60)
    print("自检通过 ✓")
    print("=" * 60)
