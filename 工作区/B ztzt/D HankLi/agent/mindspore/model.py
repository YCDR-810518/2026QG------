"""
model.py — 园区安全智能调控平台 AI 模型定义（MindSpore 版本）
==============================================================
包含两个核心类：
  - DensityPredictor:   人流/车流密度时序预测模型（TSMixer-Lite / GRU）
  - CongestionDetector: 异常检测器（拥堵/滞留/门闸异常）

接口规范：统一遵循 sklearn 风格（fit / predict / save / load）
框架：MindSpore（CPU 训练）。与原 PyTorch 版逻辑完全一致，仅替换底层框架。

说明：本文件刻意不 import torch。网络层用 mindspore.nn，数据用 numpy，
     训练循环用手写 batch 循环 + mindspore.ops 计算梯度（兼容 CPU / GPU / Ascend）。
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Optional, Tuple, Union

import numpy as np

import mindspore as ms
import mindspore.nn as nn
import mindspore.ops as ops
from mindspore import Tensor

# ============================================================================
# 设备自动检测：同时兼容 CPU / GPU / Ascend
# ============================================================================
# 说明：
#   - 装了 MindSpore GPU 版且有 NVIDIA GPU → 用 GPU
#   - 装了 CPU 版 → 用 CPU
#   - 装了 Ascend 版 → 用 Ascend
#   - 可用环境变量 MINDSPORE_DEVICE 强制指定：GPU / CPU / Ascend
# set_context 必须在 import 后尽早调用，且只能设置一次，所以放在模块加载时。


def _detect_device() -> str:
    """自动检测可用设备：GPU > Ascend > CPU。兼容不同 MindSpore 版本。"""
    env = os.environ.get("MINDSPORE_DEVICE", "").strip().lower()
    if env in ("gpu", "cpu", "ascend"):
        return env.upper()

    # 尝试 ms.hal（新版 API）
    try:
        if ms.hal.device_count("GPU") > 0:
            return "GPU"
    except Exception:
        pass
    try:
        if ms.hal.device_count("Ascend") > 0:
            return "Ascend"
    except Exception:
        pass
    # 兜底：CPU
    return "CPU"


# 模块加载时统一设置设备（Pynative 模式 + 检测到的设备）
# 若已设置过（重复 import 场景），ms.set_context 再次调用会报错，故先查询
try:
    current_target = ms.get_context("device_target")
except Exception:
    current_target = None

if current_target is None:
    ms.set_context(mode=ms.PYNATIVE_MODE, device_target=_detect_device())
else:
    # 已设置过设备，只确保模式
    ms.set_context(mode=ms.PYNATIVE_MODE)

_DETECTED_DEVICE = _detect_device()


# ============================================================================
# 工具函数
# ============================================================================


def _get_device(device: Union[str, None] = None) -> str:
    """解析设备：显式指定优先，否则用自动检测结果（GPU > Ascend > CPU）。"""
    if device is not None:
        return str(device).lower()
    return _DETECTED_DEVICE.lower()
    if device is not None:
        return str(device).lower()
    return _DETECTED_DEVICE.lower()


# ============================================================================
# 神经网络模块定义
# ============================================================================


class TSMixerBlock(nn.Cell):
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
        self.time_mix = nn.SequentialCell(
            nn.Dense(seq_len, hidden_dim),
            nn.GELU(),
            nn.Dropout(p=dropout),
            nn.Dense(hidden_dim, seq_len),
            nn.Dropout(p=dropout),
        )
        # MindSpore LayerNorm 期望最后一维为归一化维，此处作用于时间轴需转置后处理
        self.time_norm = nn.LayerNorm((n_features,))

        # ---- Feature Mixing: (B, N, T, F) → (B, N, T, F) ----
        self.feature_mix = nn.SequentialCell(
            nn.Dense(n_features, hidden_dim),
            nn.GELU(),
            nn.Dropout(p=dropout),
            nn.Dense(hidden_dim, n_features),
            nn.Dropout(p=dropout),
        )
        self.feature_norm = nn.LayerNorm((n_features,))

        self.transpose = ops.Transpose()

    def construct(self, x: Tensor) -> Tensor:
        """x: (B, N, T, F)"""
        # ---- Time mixing: 转置为 (B, N, F, T) 过 MLP，再转回 (B, N, T, F) ----
        residual = x
        x_t = self.transpose(x, (0, 1, 3, 2))          # (B, N, F, T)
        x_t = self.time_mix(x_t)                        # (B, N, F, T)
        x_t = self.transpose(x_t, (0, 1, 3, 2))         # (B, N, T, F)
        x = self.time_norm(residual + x_t)              # (B, N, T, F) 在 F 维归一化

        # ---- Feature mixing: 直接在特征轴混合 ----
        residual = x
        x_f = self.feature_mix(x)                        # (B, N, T, F)
        x = self.feature_norm(residual + x_f)

        return x


class DensityNet(nn.Cell):
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

        self.blocks = nn.CellList([
            TSMixerBlock(window_size, n_features, hidden_dim, dropout)
            for _ in range(num_layers)
        ])

        # 投影头：展平 (T, F) → 映射到未来 H 步
        flat_dim = window_size * n_features
        self.projection = nn.SequentialCell(
            nn.Dense(flat_dim, hidden_dim * 2),
            nn.GELU(),
            nn.Dropout(p=dropout),
            nn.Dense(hidden_dim * 2, pred_horizon),
        )

    def construct(self, x: Tensor) -> Tensor:
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


class DensityNetGRU(nn.Cell):
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
        self.projection = nn.Dense(hidden_dim, pred_horizon)

    def construct(self, x: Tensor) -> Tensor:
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
    人流/车流密度时序预测模型（MindSpore 版）

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
    device : str, optional
        计算设备（默认 "cpu"）。

    Attributes
    ----------
    model_ : nn.Cell or None
        底层 MindSpore 模型，fit 后可用。
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
        device: Union[str, None] = None,
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

        self.model_: Optional[nn.Cell] = None
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
        verbose: bool = True,
        **fit_params,
    ) -> "DensityPredictor":
        """
        训练密度预测模型（MindSpore，手写 batch 循环）。

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
            验证集比例（从训练数据尾部切分）。
        patience : int, default=10
            早停耐心值（验证损失连续不降则停）。
        feature_names : list of str, optional
            特征名称列表（仅用于记录）。
        validation_data : tuple (X_val, y_val), optional
            外部传入的验证集，优先级高于 val_split。
        verbose : bool, default=True
            是否打印训练进度。

        Returns
        -------
        self : DensityPredictor
        """
        n_feat, n_nodes = self._validate_input(X, y, fit=True)
        self.feature_names_ = feature_names

        # 构建 / 重建模型
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

        # ---- 优化器 & 损失 ----
        optimizer = nn.Adam(self.model_.trainable_params(), learning_rate=lr)
        criterion = nn.MSELoss()

        # MindSpore value_and_grad：forward_fn 必须自己算 loss
        def forward_fn(x, y):
            pred = self.model_(x)
            return criterion(pred, y)

        grad_fn = ops.value_and_grad(forward_fn, None, optimizer.parameters)

        best_val_loss = float("inf")
        best_param_dict = None
        epochs_no_improve = 0
        self._training_history = {"train_loss": [], "val_loss": []}

        # ---- 训练循环 ----
        for epoch in range(1, epochs + 1):
            # -- 训练阶段 --
            self.model_.set_train(True)
            train_loss_sum = 0.0
            n_train_batches = 0
            for start in range(0, n_train, batch_size):
                end = min(start + batch_size, n_train)
                batch_x = Tensor(X_train[start:end], ms.float32)
                batch_y = Tensor(y_train[start:end], ms.float32)

                loss, grads = grad_fn(batch_x, batch_y)
                optimizer(grads)

                train_loss_sum += float(loss.asnumpy()) * (end - start)
                n_train_batches += 1

            avg_train_loss = train_loss_sum / max(n_train, 1)

            # -- 验证阶段 --
            self.model_.set_train(False)
            if n_val > 0:
                val_loss_sum = 0.0
                for start in range(0, n_val, batch_size):
                    end = min(start + batch_size, n_val)
                    batch_x = Tensor(X_val[start:end], ms.float32)
                    batch_y = Tensor(y_val[start:end], ms.float32)
                    pred = self.model_(batch_x)
                    loss = criterion(pred, batch_y)
                    val_loss_sum += float(loss.asnumpy()) * (end - start)
                avg_val_loss = val_loss_sum / n_val
            else:
                avg_val_loss = avg_train_loss

            self._training_history["train_loss"].append(avg_train_loss)
            self._training_history["val_loss"].append(avg_val_loss)

            if verbose and (epoch % 10 == 0 or epoch == 1):
                print(
                    f"Epoch {epoch:3d}/{epochs} | "
                    f"train_loss: {avg_train_loss:.6f} | "
                    f"val_loss: {avg_val_loss:.6f}"
                )

            # -- 早停 & 保存最佳参数 --
            if avg_val_loss < best_val_loss:
                best_val_loss = avg_val_loss
                best_param_dict = [p.asnumpy().copy() for p in self.model_.trainable_params()]
                epochs_no_improve = 0
            else:
                epochs_no_improve += 1
                if epochs_no_improve >= patience:
                    if verbose:
                        print(f"早停于 epoch {epoch}（{patience} 轮未改善）")
                    break

        # 恢复最佳权重
        if best_param_dict is not None:
            params = self.model_.trainable_params()
            for p, best_p in zip(params, best_param_dict):
                p.set_data(Tensor(best_p, ms.float32))
        self.model_.set_train(False)
        self.is_fitted_ = True

        if verbose:
            print(f"训练完成 | 最佳 val_loss: {best_val_loss:.6f}")

        return self

    # ------------------------------------------------------------------
    # 预测
    # ------------------------------------------------------------------

    def predict(self, X: np.ndarray) -> np.ndarray:
        """
        预测未来人流/车流密度。

        Parameters
        ----------
        X : np.ndarray, shape (n_samples, n_nodes, window_size, n_features)
            历史特征序列。

        Returns
        -------
        y_pred : np.ndarray, shape (n_samples, n_nodes, pred_horizon)
            每个节点未来 H 步的密度预测值。
        """
        if not self.is_fitted_ or self.model_ is None:
            raise RuntimeError("模型尚未训练，请先调用 fit()")

        self._validate_input(X, fit=False)

        self.model_.set_train(False)
        x_tensor = Tensor(X, ms.float32)
        y_pred = self.model_(x_tensor)
        return y_pred.asnumpy()

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------

    def save(self, path: str) -> None:
        """
        保存模型权重与配置到指定目录。

        产出：
            {path}/model_state.ckpt  — MindSpore 权重
            {path}/config.json       — 超参与元信息
        """
        if not self.is_fitted_ or self.model_ is None:
            raise RuntimeError("模型尚未训练，没有可保存的权重")

        os.makedirs(path, exist_ok=True)

        # 权重（保存到 .ckpt，MindSpore 标准格式）
        state_dict = self.model_.parameters_dict()
        ckpt_path = os.path.join(path, "model_state.ckpt")
        ms.save_checkpoint(state_dict, ckpt_path)

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
            "framework": "mindspore",
        }
        with open(os.path.join(path, "config.json"), "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path: str, device: Union[str, None] = None) -> "DensityPredictor":
        """
        从目录加载已训练模型。

        Parameters
        ----------
        path : str
            包含 model_state.ckpt 和 config.json 的目录路径。
        device : str, optional
            加载到的设备。

        Returns
        -------
        model : DensityPredictor
        """
        config_path = os.path.join(path, "config.json")
        ckpt_path = os.path.join(path, "model_state.ckpt")

        if not os.path.exists(config_path):
            raise FileNotFoundError(f"配置文件不存在: {config_path}")
        if not os.path.exists(ckpt_path):
            raise FileNotFoundError(f"权重文件不存在: {ckpt_path}")

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
        ms.load_param_into_net(instance.model_, ms.load_checkpoint(ckpt_path))
        instance.model_.set_train(False)
        instance.is_fitted_ = True
        instance._training_history = config.get("training_history", {})

        return instance


# ============================================================================
# CongestionDetector — 异常检测器
# ============================================================================


class CongestionDetector:
    """
    园区异常检测器（MindSpore 版）

    基于统计基线实现三类异常检测：
      1. 异常拥堵 — 密度超历史分位数 + 预测残差验证
      2. 人员滞留 — 密度变化率持续低于阈值
      3. 门闸异常 — 状态码故障 / 流速异常 / 响应失灵

    注意：本类只使用 numpy，不涉及 MindSpore 张量，因此与原 PyTorch 版
    逻辑完全一致、无需改动。

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
    gate_status_feature_idx : int, default=3
        门闸状态码在 X 中的列索引。
    gate_flow_feature_idx : int, default=4
        门闸流速在 X 中的列索引。
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
    ):
        if not 50.0 <= density_percentile <= 100.0:
            raise ValueError(f"density_percentile 应在 [50, 100]，收到 {density_percentile}")
        if loitering_duration < 1:
            raise ValueError(f"loitering_duration 应 ≥ 1，收到 {loitering_duration}")

        self.density_percentile = density_percentile
        self.loitering_duration = loitering_duration
        self.loitering_rate_threshold = loitering_rate_threshold
        self.sigma_threshold = sigma_threshold
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
        self._alerted_events: set = set()                # 已告警事件去重

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
        """
        events = []
        for node_i in range(self.n_nodes_):
            stats = self.node_stats_[str(node_i)]
            density = float(current_density[node_i])

            # 条件 1：超过历史分位数
            if density <= stats["density_p95"]:
                self._congestion_counter[str(node_i)] = 0
                continue

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
                "threshold_p95": round(stats["density_p95"], 4),
                "exceed_ratio": round(density / max(stats["density_p95"], 1e-6), 2),
                "predicted_duration_min": duration_est,
                "timestamp": None,
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
            异常事件列表。
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

        # 去重（同 node + 同 type 在短时间内不重复告警）
        deduped = []
        for e in events:
            key = f"{e['type']}_{e['node_id']}"
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
