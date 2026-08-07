# F-3 改动说明：每 tick 微包发射通道（编队实时帧）

> **性质**：给成员 F 复核合入的改动申请（**唯一剩余 F 侧改动**；6.1 trip 钩子 / 6.2 union_pack 接线已合入 ✅）
>
> 提出方：C（陈思敏）｜ 需求：FR-12/FR-27 ｜ 日期：2026-08-06
>
> 关联模块：`simulation/micro_fleet.py`（C 侧**已就绪**，每 tick 从真实引擎组装一帧）
> 关联配置：`config.yaml` 的 `micro_fleet:` 段（默认 `enabled: false`，仅演示窗口打开）
> 字段契约：见《CavIdmMovement 结构及使用说明.md》第八节（对齐《CAV小车编队接口定义文档.md》）

---

## 一、背景

前端需要**每秒**渲染 4 辆 CAV 小车编队沿真实路径行驶（10s 的 union_pack 大包太慢）。
因此：演示窗口（`micro_fleet.enabled: true`）内，每 tick（1s）由 `micro_fleet.collect_micro_fleet(eng)`
从真实引擎挑选"车多的大门 → 固定终点"的最多 4 辆在途车，组装帧
`{tick, gate_id, path:{start_node_id, end_node_id, route_nodes}, fleet:[4]}`，
POST 至 B 后端（`POST /api/v1/sim/micro-fleet`，只存最新帧），E 每秒轮询渲染。

本申请共 **3 处纯新增**，不触碰任何现有逻辑。

---

## 二、改动 1：`sender.py::run_and_send` 增加可选 `on_tick` 回调（约 2 行）

同样给 `csv_recorder.py::run_and_record_send` 加同名参数（签名一致）：

```python
def run_and_send(engine, n_ticks, sender=None, interval=10, on_package=None,
                 tick_hz=None, timestamp_fn=None, on_tick=None):
    ...
    for t in range(n_ticks):
        snap = engine.step(t)
        if on_tick is not None:          # ← 新增：每 tick 调用（约 2 行）
            on_tick(engine, t)
        if period is not None:
            _sleep_until(start + (t + 1) * period)
        if t % interval == 0:
            ...
```

> 注意：`on_tick` 在 `engine.step(t)` **之后**调用（引擎本 tick 状态已就绪）；
> 放在节拍 `_sleep_until` 之前（不阻塞实时节奏）。

## 三、改动 2：`main.py::cmd_run` 组装微包并 POST（`micro_fleet.enabled=True` 时生效）

```python
from micro_fleet import collect_micro_fleet, clear_speed_cache
from cav_pack import collect_cav_stats, pack_micro_results

mf = cfg.get("micro_fleet", {})
if mf.get("enabled"):
    clear_speed_cache()

def _on_tick(engine, t):
    mf = cfg.get("micro_fleet", {})
    if not mf.get("enabled") or t % int(mf.get("interval", 1)) != 0:
        return
    frame = collect_micro_fleet(engine, fleet_size=int(mf.get("fleet_size", 4)),
                                dst_node_id=str(mf.get("dst_node_id", "canteen_1")))
    if frame["fleet"] and client is not None:      # client = BackendClient（复用登录态）
        ok = client.post_json(mf.get("backend_endpoint", "/api/v1/sim/micro-fleet"), frame)
        if not ok:
            print(f"  [micro-fleet 上传失败] tick={frame['tick']}")

# run_and_record_send(..., on_tick=_on_tick)
```

> 空帧（候选不足 4 辆）跳过上传，保留上一帧由 E 侧继续渲染。

## 四、改动 3：`backend_client.py` 增加通用 `post_json(endpoint, payload)`

现有 `send_payload` 固定 POST union_pack 路径（`realtime_endpoint`）；新增通用方法，纯增量：

```python
def post_json(self, endpoint, payload):
    """POST 任意端点 JSON（复用登录 token），成功返回 True。"""
    try:
        resp = self.session.post(self._base + endpoint,
                                 json=payload,
                                 timeout=10)
        return resp.status_code < 400
    except Exception as exc:
        logger.warning("[BackendClient.post_json] %s 失败: %s", endpoint, exc)
        return False
```

（实现细节以 F 现有 `send_payload` 的鉴权/会话风格为准，此处为规格示意。）

---

## 五、验证方法（F 可自行执行）

```bash
cd 项目目录
# config.yaml 临时改 micro_fleet.enabled: true
python simulation/main.py run --n-people 1000 --n-vehicles 500 --n-ticks 1200 --n-days 1
```
预期：
- 控制台每 1s 出现 `[micro-fleet 上传失败]` 仅在后端未就绪时出现（B 端点未建时属预期）；
- `micro_fleet.enabled=false` 时无任何输出、零开销；
- 原有 `verify` / union_pack 行为不受影响。

## 六、风险与回退

| 风险 | 应对 |
|---|---|
| 影响现有行为 | 全部纯新增（参数默认 None / enabled 默认 false）；回退 = 删除新增段 |
| 1s 频率压力 | 仅演示窗口开启；B 端只覆盖最新帧不落库 |
| 帧为空 | 跳过上传，E 侧保留上一帧渲染 |

## 七、待 F 确认事项

1. `on_tick` 回调的插入位置（step 后、节拍前）是否认可？
2. 微包经 `BackendClient.post_json` 直接 POST B 端点（不经 JsonSender 窗口文件）是否认可？
3. 改动由 F 合入还是由 C/组长按本说明实施后 F 复核？

> 合入后即打通"真实引擎 → 每秒编队帧 → B → E"整条链，8/7 演示即可使用。
