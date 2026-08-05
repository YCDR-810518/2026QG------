from service import SecurityService
import torch

svc = SecurityService.from_config(
    csv_path=r"D:\资料-study\6-QG暑期考核\1-QG园区\data\engine_timeseries.csv",   # F 持续写入的 CSV
    backend_base="http://192.168.1.114:8100",  # 后端地址
    demo_mode=False,                            # 测试模式：无预警也发演示预警
    interval_seconds=60,                       # 轮询间隔
)

svc.run_loop()   # 阻塞式循环，Ctrl+C 停止

# # 或只跑一次：
# result = svc.check_alerts()   # 返回 {"predictions":..., "alerts":[...], "posted":...}
# print(result)