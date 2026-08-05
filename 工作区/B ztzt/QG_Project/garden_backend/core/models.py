from django.db import models

# Create your models here.
from django.db import models

# 1. 管理员/用户表 (遵循第三范式)
class AdminUser(models.Model):
    username = models.CharField(max_length=50, unique=True, verbose_name="用户名")
    password = models.CharField(max_length=128, verbose_name="加密密码")
    role = models.CharField(max_length=20, default="超级管理员", verbose_name="角色")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    class Meta:
        db_table = 'admin_user'
        verbose_name = '管理员表'

    @property
    def is_authenticated(self):
        return True


# 2. 节点表 (ID 来源于拓扑文件)
class Node(models.Model):
    node_id = models.CharField(max_length=50, primary_key=True, verbose_name="节点编码")  # 如 zone_canteen
    node_name = models.CharField(max_length=100, verbose_name="节点名称")
    x = models.FloatField(verbose_name="地图X坐标")
    y = models.FloatField(verbose_name="地图Y坐标")
    node_type = models.CharField(max_length=30, verbose_name="节点类型")  # entrance / cross_road / divergence / hotspot / stop
    people = models.IntegerField(default=0, verbose_name="实时人数")
    density = models.FloatField(default=0.0, verbose_name="实时密度")
    level = models.CharField(max_length=20, default="low", verbose_name="拥挤等级")  # low / medium / high / critical
    gate_id = models.CharField(max_length=50, blank=True, null=True, verbose_name="门闸编号")
    signal_id = models.CharField(max_length=50, blank=True, null=True, verbose_name="红绿灯编号")
    edge_ids = models.JSONField(default=list, blank=True, verbose_name="关联边编号列表")

    class Meta:
        db_table = 'network_node'
        verbose_name = '交通网络节点表'


# 3. 门闸表 (ID 由后端物理设备表唯一维护)
class Gate(models.Model):
    gate_id = models.CharField(max_length=50, primary_key=True, verbose_name="门闸编号")  # 如 G01
    node = models.ForeignKey(Node, on_delete=models.SET_NULL, null=True, blank=True, related_name="gates", verbose_name="所属入口节点")
    mode = models.CharField(max_length=20, default="open", verbose_name="控制模式")  # open / restrict / close
    throughput_cap = models.IntegerField(default=90, verbose_name="单位tick放行上限")
    n_lanes = models.IntegerField(default=1, verbose_name="开闸数")
    gate_status = models.IntegerField(default=1, verbose_name="状态码")  # 0=关闭, 1=正常开放, 2=限流, 3=故障
    gate_flow_rate = models.FloatField(default=0.0, verbose_name="当前通行速率")

    class Meta:
        db_table = 'gate_device'
        verbose_name = '门闸设备表'


# 4. 红绿灯表 (ID 由后端物理设备表唯一维护)
class SignalLight(models.Model):
    signal_id = models.CharField(max_length=50, primary_key=True, verbose_name="红绿灯编号")  # 如 S01
    node = models.ForeignKey(Node, on_delete=models.SET_NULL, null=True, blank=True, related_name="signals", verbose_name="所属路口节点")
    mode = models.CharField(max_length=20, default="adaptive", verbose_name="控制模式")  # fixed / adaptive / flash
    phase = models.CharField(max_length=20, default="green", verbose_name="当前相位")  # green / yellow / red / off
    cycle_time = models.IntegerField(default=60, verbose_name="信号周期时长(秒)")
    green_time = models.IntegerField(default=30, verbose_name="绿灯时长(秒)")
    yellow_time = models.IntegerField(default=3, verbose_name="黄灯时长(秒)")
    red_time = models.IntegerField(default=27, verbose_name="红灯时长(秒)")
    offset = models.IntegerField(default=0, verbose_name="相位起始偏移(秒)")
    throughput_cap = models.IntegerField(default=90, verbose_name="单位tick放行上限")
    n_phases = models.IntegerField(default=2, verbose_name="相位数")
    signal_status = models.IntegerField(default=1, verbose_name="状态码")  # 0=熄灭/故障, 1=正常运行, 2=黄闪, 3=故障
    signal_flow_rate = models.FloatField(default=0.0, verbose_name="当前通行速率")

    class Meta:
        db_table = 'signal_device'
        verbose_name = '红绿灯设备表'


# 5. 车辆表 (园区准入与轨迹)
class Vehicle(models.Model):
    car_id = models.CharField(max_length=50, primary_key=True, verbose_name="车辆ID")
    owner_name = models.CharField(max_length=50, blank=True, null=True, verbose_name="车主姓名")
    license_plate = models.CharField(max_length=20, verbose_name="车牌号")
    is_allowed = models.BooleanField(default=True, verbose_name="是否允许准入")
    status = models.CharField(max_length=20, default="in_park", verbose_name="状态")

    class Meta:
        db_table = 'vehicle'
        verbose_name = '车辆管理表'


# 6. 系统/预警日志表
class Alert(models.Model):
    """
    预警记录表
    用于存储 D（AI预测模块）发来的预警数据，供前端 E 查询展示
    """
    # === 枚举选项 ===
    RISK_CHOICES = [
        ('yellow', '黄色预警'),
        ('red', '红色预警'),
    ]
    STATUS_CHOICES = [
        ('pending', '待处理'),
        ('resolved', '已解决'),
    ]
    SOURCE_CHOICES = [
        ('ai_prediction', 'AI预测'),
        ('macro_analysis', '宏观分析'),
        ('gate_abnormal', '门闸异常'),
    ]

    # === 后端自动生成 ===
    alert_id = models.CharField(max_length=50, unique=True, verbose_name='预警编号')  # ALT-20260803-001

    # === 关联节点（外键） ===
    node = models.ForeignKey('Node', on_delete=models.CASCADE, verbose_name='关联节点')
    node_name = models.CharField(max_length=100, verbose_name='节点名称')  # 冗余存储，方便查询

    # === 预警核心信息 ===
    risk_level = models.CharField(max_length=20, choices=RISK_CHOICES, verbose_name='风险等级')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending', verbose_name='状态')
    trigger_source = models.CharField(max_length=50, choices=SOURCE_CHOICES, default='ai_prediction', verbose_name='触发来源')
    description = models.TextField(verbose_name='预警描述')

    # === 时间字段 ===
    created_at = models.DateTimeField(verbose_name='创建时间')  # 使用 D 的 timestamp
    resolved_at = models.DateTimeField(null=True, blank=True, verbose_name='解决时间')

    # === 处置措施 ===
    action_taken = models.TextField(blank=True, null=True, verbose_name='处置措施')  # 来自 D 的 suggested_action

    # === D 原始数据（追溯用） ===
    event_id = models.CharField(max_length=50, blank=True, null=True, verbose_name='D事件ID')
    current_density = models.FloatField(blank=True, null=True, verbose_name='当前密度')
    threshold_density = models.FloatField(blank=True, null=True, verbose_name='阈值密度')
    predicted_duration_min = models.IntegerField(blank=True, null=True, verbose_name='预测持续时间')

    class Meta:
        db_table = 'alert'
        verbose_name = '预警记录'
        verbose_name_plural = '预警记录'

    def __str__(self):
        return f"{self.alert_id} - {self.node_name} - {self.risk_level}"