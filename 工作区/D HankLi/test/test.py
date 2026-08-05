from service import SecurityService
import mindspore

svc = SecurityService.from_config(
    csv_path=r"D:\QG\QG2026暑假训练营\中期考核\data\engine_snapshot.csv",   # F 持续写入的 CSV
    backend_base="http://192.168.1.114:8100",  # 后端地址
    demo_mode=False,                            # 测试模式：无预警也发演示预警
    interval_seconds=60,                       # 轮询间隔
)

svc.run_loop()   # 阻塞式循环，Ctrl+C 停止

# # 或只跑一次：
# result = svc.check_alerts()   # 返回 {"predictions":..., "alerts":[...], "posted":...}
# print(result)