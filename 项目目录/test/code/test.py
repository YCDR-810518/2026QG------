import torch

print(torch.cuda.is_available())   # 是否可用
print(torch.cuda.device_count())   # GPU数量
print(torch.cuda.get_device_name(0))  # GPU名称