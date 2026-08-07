# -*- coding: utf-8 -*-
"""backend_client.py —— 后端实时数据上传客户端（成员 F）

仿真引擎每 10s 生成的 union_pack.json（engine_snapshot + 宏观预测 + 预警等）
经本模块 POST 到后端实时数据接口。对接 B 后端《实时数据接口.md》：

- 认证：POST /api/v1/admin/login 拿 JWT token，之后请求头
  Authorization: Bearer <token>；token 缓存 + 过期重登，401 时自动重试一次。
- 接口：POST <base_url><endpoint>，body 为 union_pack 原始 JSON（snake_case）；
  按《通用约定》，snake_case → camelCase 转换由 B 后端负责。
- 失败兜底：网络异常/超时/非 0 响应一律捕获并返回 False，不中断仿真循环。

用法：
    client = BackendClient(
        base_url="http://192.168.1.114:8100",
        username="ZTZT", password="...",
        endpoint="/api/v1/realtime/upload",
    )
    ok = client.send_payload(union_pack_dict)      # 成功返回 True
    ok = client.send_files([Path("data/json/union_pack.json")])
"""
import json
import os
import time
from datetime import datetime, timedelta

from pathlib import Path


class BackendClient:
    """把打包窗口 JSON 上传到后端实时数据接口的客户端。

    Parameters
    ----------
    base_url : str
        后端服务地址（如 http://192.168.1.114:8100，不带结尾斜杠）。
    username / password : str
        后端登录账号密码（与 test/service.py 登录流程一致）。
    endpoint : str
        实时数据上传路径（缺省 /api/v1/realtime/upload，B 定稿后改配置）。
    login_path : str
        登录路径（缺省 /api/v1/admin/login）。
    timeout : float
        单次请求超时秒数。
    token_ttl : int
        token 缓存有效秒数（JWT 短时长场景可设小）。
    """

    def __init__(self, base_url="", username="ZTZT", password="Zzt20070124",
                 endpoint="/api/v1/realtime/upload",
                 login_path="/api/v1/admin/login",
                 timeout=5.0, token_ttl=3600):
        self.base_url = str(base_url or "").rstrip("/")
        self.username = username
        self.password = password
        self.endpoint = endpoint
        self.login_path = login_path
        self.timeout = float(timeout)
        self.token_ttl = float(token_ttl)

        self._token = None
        self._token_expires = datetime.min
        self._login_url = self.base_url + self.login_path if self.base_url else ""
        self._upload_url = self.base_url + self.endpoint if self.base_url else ""

    # ------------------------------------------------------------------ 登录
    def login(self):
        """登录后端拿 token；成功返回 token 字符串，失败返回 None。"""
        if not self._login_url:
            print("  [后端] 未配置 base_url，跳过登录")
            return None
        import requests

        try:
            resp = requests.post(
                self._login_url,
                json={"username": self.username, "password": self.password},
                timeout=self.timeout,
            )
            body = resp.json()
            if resp.status_code == 200 and body.get("code") == 0:
                self._token = body["data"]["token"]
                self._token_expires = datetime.now() + timedelta(seconds=self.token_ttl)
                return self._token
            print(f"  [后端] 登录失败：code={body.get('code')} "
                  f"msg={body.get('message')}")
        except Exception as e:
            print(f"  [后端] 登录异常：{e}")
        return None

    def _get_token(self):
        """返回有效缓存 token，过期/缺失则重新登录。"""
        if self._token and datetime.now() < self._token_expires:
            return self._token
        return self.login()

    # ------------------------------------------------------------------ 上传
    def send_payload(self, payload):
        """POST 一个 JSON 可序列化对象到后端实时数据接口。

        Returns
        -------
        bool
            True 表示后端成功接收（code==0）；False 表示失败（保留窗口待重试）。
        """
        if not self._upload_url:
            print("  [后端] 未配置 base_url，跳过上传")
            return False

        import requests

        token = self._get_token()
        if not token:
            return False

        headers = {"Authorization": f"Bearer {token}",
                   "Content-Type": "application/json"}
        try:
            resp = requests.post(self._upload_url, json=payload,
                                 headers=headers, timeout=self.timeout)
            if resp.status_code in (401, 403):
                # token 失效 → 重新登录再试一次
                token = self.login()
                if not token:
                    return False
                headers["Authorization"] = f"Bearer {token}"
                resp = requests.post(self._upload_url, json=payload,
                                     headers=headers, timeout=self.timeout)
            body = resp.json()
            if resp.status_code in (200, 201) and body.get("code") == 0:
                return True
            print(f"  [后端] 上传失败：HTTP {resp.status_code} "
                  f"code={body.get('code')} msg={body.get('message')}")
        except Exception as e:
            print(f"  [后端] 上传异常：{e}")
        return False

    def send_files(self, files):
        """读取窗口内 json 文件并逐一上传；全部成功返回 True，任一失败返回 False。

        Parameters
        ----------
        files : iterable of str/Path
            打包窗口文件列表（如 sender.package_files()）。
        """
        payloads = []
        for p in files:
            path = Path(p)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    payloads.append((path.name, json.load(f)))
            except Exception as e:
                print(f"  [后端] 读取 {path} 失败：{e}")
                return False
        if not payloads:
            return False
        return all(self.send_payload(payload) for _, payload in payloads)


def from_config(config=None):
    """从 config 的 simulation 段构造客户端；未配置 realtime_backend 返回 None。"""
    from config import get_config

    cfg = config or get_config()
    sim = cfg["simulation"]
    base = str(sim.get("realtime_backend", "") or "").strip()
    if not base:
        return None
    return BackendClient(
        base_url=base,
        username=sim.get("realtime_username", "ZTZT"),
        password=sim.get("realtime_password", "Zzt20070124"),
        endpoint=sim.get("realtime_endpoint", "/api/v1/realtime/upload"),
        token_ttl=sim.get("realtime_token_ttl", 3456000),
    )


if __name__ == "__main__":
    import sys

    base = os.environ.get("BACKEND_BASE", "")
    if not base:
        print("用法：BACKEND_BASE=http://127.0.0.1:8000 python backend_client.py")
        print("      （先起一个可用的后端，或本地 mock 服务）")
        sys.exit(0)

    client = from_config() or BackendClient(base_url=base)
    demo = {
        "engine_snapshot": [
            {"tick": 10, "timestamp": "2026-08-05 12:00:10", "node_id": "zone_canteen",
             "people": 1200, "vehicles": 15, "density": 0.8, "level": "high",
             "gate_status": "restricted", "gate_flow_rate": 90,
             "door_status": "open", "door_flow_rate": "",
             "signal_status": "green", "signal_flow_rate": 45},
        ],
        "predict_network": [],
        "predict_hotspots": [],
        "prediction": None,
        "alerts": [],
    }
    print(f"== backend_client selftest：POST {client._upload_url} ==")
    ok = client.send_payload(demo)
    print("send_payload ->", ok)
    sys.exit(0 if ok else 1)
