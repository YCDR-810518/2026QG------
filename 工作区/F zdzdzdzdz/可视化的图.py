import matplotlib.pyplot as plt
import networkx as nx

# 1. 设置 Matplotlib 中文显示与负号问题
plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'PingFang SC', 'Arial Unicode MS']
plt.rcParams['axes.unicode_minus'] = False

# 2. 节点定义: (node_id, 节点名称, 节点类型, (X坐标, Y坐标), 是否有红绿灯, 是否有闸机)
nodes_raw = [
    # --- 校门与主干道枢纽 ---
    ("gate_south", "南大门", "entrance", (65, 5), True, True),
    ("gate_west", "西门出口", "entrance", (8, 55), True, True),
    ("gate_east", "东门出口", "entrance", (92, 55), True, True),
    ("cross_zh_south", "中环西南路口", "road", (45, 12), True, False),
    ("cross_zh_mid", "中环天桥/通道口", "road", (40, 58), True, False),
    ("cross_zh_north", "中环西北路口", "road", (45, 80), True, False),
    ("rd_guanggong_1", "广工一路节点", "road", (85, 68), True, False),
    ("pedestrian_bridge", "天桥/下沉球场", "road", (35, 58), False, False),
    ("underpass", "广工通道", "road", (48, 58), False, False),

    # --- 行政与公共地标区 ---
    ("admin_building", "行政楼/综合楼", "admin", (62, 12), False, False),
    ("auditorium", "大讲堂(圆弧报告厅)", "admin", (68, 8), False, False),
    ("library", "图书馆", "academic", (58, 38), False, True),

    # --- 教学与创客区 ---
    ("gongchuanggu", "工创谷", "academic", (62, 48), False, False),
    ("teach_1", "教一", "academic", (78, 48), False, False),
    ("teach_2", "教二", "academic", (78, 56), False, False),
    ("teach_3", "教三", "academic", (68, 48), False, False),
    ("teach_4", "教四", "academic", (70, 56), False, False),
    ("teach_5", "教五", "academic", (62, 44), False, False),
    ("teach_6", "教六", "academic", (64, 56), False, False),
    ("large_classroom", "大教室(54/32)", "academic", (72, 52), False, False),

    # --- 工学馆、实验楼与研究院 ---
    ("eng_1", "工一", "lab", (72, 32), False, False),
    ("eng_2", "工二", "lab", (72, 28), False, False),
    ("eng_3", "工三", "lab", (72, 24), False, False),
    ("eng_4", "工四", "lab", (72, 20), False, False),
    ("exp_1", "实一", "lab", (82, 32), False, False),
    ("exp_2", "实二", "lab", (82, 28), False, False),
    ("exp_3", "实三", "lab", (82, 24), False, False),
    ("exp_4", "实四", "lab", (82, 20), False, False),
    ("science_hall", "理学馆", "lab", (82, 14), False, False),
    ("struct_center", "结构实验中心", "lab", (90, 32), False, False),
    ("env_inst", "环境生态研究院", "lab", (90, 26), False, False),
    ("biomed_inst", "生物医药学院", "lab", (90, 20), False, False),

    # --- 运动场馆区 ---
    ("sports_gym", "体育馆", "sports", (32, 52), False, False),
    ("sports_swimming", "游泳馆", "sports", (32, 45), False, False),
    ("sports_fitness", "健身房/田径场", "sports", (42, 48), False, False),
    ("sports_cricket", "板球场", "sports", (12, 40), False, False),
    ("sports_tennis", "网球场", "sports", (22, 45), False, False),
    ("sports_volleyball", "排球场", "sports", (26, 38), False, False),
    ("sports_basketball_c", "篮球场C区", "sports", (28, 30), False, False),
    ("sports_basketball_b", "篮球场B区", "sports", (36, 30), False, False),
    ("sports_basketball_a", "篮球场A区", "sports", (42, 30), False, False),
    ("sports_football", "足球场", "sports", (48, 35), False, False),
    ("sports_training", "综合训练场", "sports", (48, 22), False, False),
    ("youth_center", "青年活动中心", "sports", (45, 52), False, False),

    # --- 西区生活区 ---
    ("west_dorm_13_16", "西十三~十六宿", "living", (10, 78), False, True),
    ("west_dorm_9_12", "西九~十二宿舍", "living", (20, 78), False, True),
    ("west_dorm_1_4", "西一~四宿舍", "living", (28, 78), False, True),
    ("west_dorm_5_8", "西五~八宿舍", "living", (22, 68), False, True),
    ("west_dorm_17_18", "西十七~十八宿", "living", (12, 65), False, True),
    ("canteen_3", "三饭堂", "living", (28, 62), False, False),
    ("canteen_4", "四饭堂", "living", (8, 60), False, False),
    ("west_express", "西区快递点", "living", (28, 72), False, False),

    # --- 东区生活区 ---
    ("east_dorm_12_14", "东十二~十四宿", "living", (48, 75), False, True),
    ("east_dorm_8_11", "东八~十一宿", "living", (58, 70), False, True),
    ("east_dorm_4_7", "东四~七宿舍", "living", (58, 62), False, True),
    ("east_dorm_1_3", "东一~三宿舍", "living", (68, 65), False, True),
    ("teacher_apt", "教师公寓", "living", (48, 85), False, True),
    ("hospital", "广工校医院", "living", (58, 92), False, False),
    ("supermarket", "校内超市", "living", (55, 86), False, False),
    ("canteen_1", "东区一饭", "living", (62, 86), False, False),
    ("canteen_2", "东区二饭", "living", (48, 68), False, False),
]

# 3. 构建图对象与属性映射
G = nx.Graph()
pos = {}
labels = {}
node_colors_map = []

type_colors = {
    'entrance': '#FF4757',  # 红色：校门出入口
    'road': '#2ED573',      # 绿/青色：交通路口与通道
    'admin': '#747D8C',     # 灰色：行政楼与大讲堂
    'academic': '#1E90FF',  # 蓝/天蓝色：教学楼与图书馆
    'lab': '#70A1FF',       # 淡蓝色：工学馆与实验楼
    'sports': '#FFA502',    # 橙黄色：体育场馆
    'living': '#FF6B81'     # 粉红/洋红色：宿舍与饭堂
}

for n_id, name, n_type, (x, y), has_light, has_gate in nodes_raw:
    G.add_node(n_id, name=name, type=n_type, light=has_light, gate=has_gate)
    pos[n_id] = (x, y)
    
    # 格式化三要素标签：[名称]\n红绿灯:有/无 | 闸机:有/无
    light_str = "有" if has_light else "无"
    gate_str = "有" if has_gate else "无"
    labels[n_id] = f"{name}\n(灯:{light_str} | 闸:{gate_str})"
    
    node_colors_map.append(type_colors.get(n_type, '#A4B0BE'))

# 4. 高密度道路连线定义 (提升边数量与网络连通度)
edges = [
    # --- 南大门与行政主干道网络 ---
    ("gate_south", "auditorium"), ("gate_south", "admin_building"), ("gate_south", "cross_zh_south"),
    ("admin_building", "auditorium"), ("admin_building", "sports_training"), ("admin_building", "eng_4"),
    ("admin_building", "library"), ("cross_zh_south", "sports_training"), ("cross_zh_south", "sports_football"),

    # --- 中环西路脊梁线及贯通通道 ---
    ("cross_zh_south", "cross_zh_mid"), ("cross_zh_mid", "cross_zh_north"),
    ("cross_zh_mid", "underpass"), ("cross_zh_mid", "pedestrian_bridge"),
    ("underpass", "youth_center"), ("underpass", "gongchuanggu"), ("underpass", "east_dorm_12_14"),
    ("pedestrian_bridge", "sports_gym"), ("pedestrian_bridge", "sports_fitness"),

    # --- 教学与核心网格 (教1~教6, 大教室, 工创谷, 图书馆) ---
    ("library", "gongchuanggu"), ("library", "teach_5"), ("library", "teach_3"), ("library", "eng_1"),
    ("gongchuanggu", "teach_5"), ("gongchuanggu", "teach_6"), ("gongchuanggu", "teach_3"),
    ("teach_5", "teach_3"), ("teach_5", "teach_6"),
    ("teach_6", "teach_4"), ("teach_6", "rd_guanggong_1"),
    ("teach_3", "teach_4"), ("teach_3", "large_classroom"), ("teach_3", "teach_1"),
    ("teach_4", "large_classroom"), ("teach_4", "teach_2"),
    ("teach_1", "large_classroom"), ("teach_1", "teach_2"), ("teach_1", "eng_1"),
    ("teach_2", "rd_guanggong_1"), ("large_classroom", "rd_guanggong_1"),

    # --- 工学馆、实验楼与科研大楼拓扑网格 ---
    ("eng_1", "eng_2"), ("eng_2", "eng_3"), ("eng_3", "eng_4"),
    ("exp_1", "exp_2"), ("exp_2", "exp_3"), ("exp_3", "exp_4"),
    ("eng_1", "exp_1"), ("eng_2", "exp_2"), ("eng_3", "exp_3"), ("eng_4", "exp_4"),
    ("eng_4", "science_hall"), ("exp_4", "science_hall"),
    ("exp_1", "struct_center"), ("exp_2", "env_inst"), ("exp_3", "biomed_inst"),
    ("struct_center", "env_inst"), ("env_inst", "biomed_inst"), ("science_hall", "biomed_inst"),

    # --- 运动场馆集群全联通网络 ---
    ("sports_cricket", "gate_west"), ("sports_cricket", "sports_tennis"),
    ("sports_tennis", "canteen_4"), ("sports_tennis", "sports_swimming"), ("sports_tennis", "sports_volleyball"),
    ("sports_swimming", "sports_gym"), ("sports_swimming", "sports_fitness"), ("sports_swimming", "sports_volleyball"),
    ("sports_gym", "pedestrian_bridge"), ("sports_gym", "canteen_3"),
    ("sports_volleyball", "sports_basketball_c"), ("sports_basketball_c", "sports_basketball_b"),
    ("sports_basketball_b", "sports_basketball_a"), ("sports_basketball_a", "sports_fitness"),
    ("sports_fitness", "sports_football"), ("sports_fitness", "youth_center"),
    ("sports_football", "sports_training"), ("sports_basketball_a", "sports_training"),

    # --- 西区生活与宿舍区内网 ---
    ("gate_west", "canteen_4"), ("canteen_4", "west_dorm_17_18"), ("west_dorm_17_18", "west_dorm_13_16"),
    ("west_dorm_13_16", "west_dorm_9_12"), ("west_dorm_9_12", "west_dorm_1_4"),
    ("west_dorm_13_16", "west_dorm_17_18"), ("west_dorm_9_12", "west_dorm_5_8"),
    ("west_dorm_1_4", "west_dorm_5_8"), ("west_dorm_5_8", "west_dorm_17_18"),
    ("west_dorm_5_8", "canteen_3"), ("west_dorm_1_4", "west_express"),
    ("west_express", "canteen_3"), ("canteen_3", "sports_gym"), ("canteen_3", "cross_zh_mid"),
    ("west_dorm_1_4", "cross_zh_north"),

    # --- 东区生活、医院与宿舍区内网 ---
    ("cross_zh_north", "teacher_apt"), ("cross_zh_north", "east_dorm_12_14"),
    ("teacher_apt", "hospital"), ("hospital", "supermarket"), ("supermarket", "canteen_1"),
    ("canteen_1", "gate_east"), ("gate_east", "rd_guanggong_1"),
    ("east_dorm_12_14", "east_dorm_8_11"), ("east_dorm_8_11", "east_dorm_4_7"),
    ("east_dorm_4_7", "east_dorm_1_3"), ("east_dorm_1_3", "canteen_2"),
    ("east_dorm_12_14", "canteen_2"), ("canteen_2", "underpass"),
    ("east_dorm_8_11", "supermarket"), ("east_dorm_1_3", "rd_guanggong_1"),
]

G.add_edges_from(edges)

# 5. 开始绘图
plt.figure(figsize=(15, 10), dpi=120)
ax = plt.gca()
ax.set_facecolor("#F8F9FA")

# 绘制高密度边
nx.draw_networkx_edges(
    G, pos,
    width=1.0,
    edge_color='#95A5A6',
    alpha=0.6,
    style='solid'
)

# 绘制节点
nx.draw_networkx_nodes(
    G, pos,
    node_size=700,
    node_color=node_colors_map,
    edgecolors='#2C3E50',
    linewidths=1.2
)

# 绘制三要素文本标注
nx.draw_networkx_labels(
    G, pos,
    labels=labels,
    font_size=5.5,
    font_weight="bold",
    font_color="#2C3E50"
)

# 6. 图例与配置
legend_items = [
    plt.Line2D([0], [0], marker='o', color='w', label='校门出入口 (Entrance)', markerfacecolor=type_colors['entrance'], markersize=11),
    plt.Line2D([0], [0], marker='o', color='w', label='交通枢纽与通道 (Road/Cross)', markerfacecolor=type_colors['road'], markersize=11),
    plt.Line2D([0], [0], marker='o', color='w', label='教学楼/图书馆/创客谷 (Academic)', markerfacecolor=type_colors['academic'], markersize=11),
    plt.Line2D([0], [0], marker='o', color='w', label='工学馆/实验楼/研究院 (Labs)', markerfacecolor=type_colors['lab'], markersize=11),
    plt.Line2D([0], [0], marker='o', color='w', label='宿舍区/饭堂/生活服务 (Living)', markerfacecolor=type_colors['living'], markersize=11),
    plt.Line2D([0], [0], marker='o', color='w', label='体育场馆集群 (Sports)', markerfacecolor=type_colors['sports'], markersize=11),
    plt.Line2D([0], [0], marker='o', color='w', label='行政与公共会堂 (Admin)', markerfacecolor=type_colors['admin'], markersize=11),
]

plt.legend(handles=legend_items, loc='upper left', frameon=True, facecolor='white', framealpha=0.95, fontsize=10)
plt.title("广东工业大学（大学城校区）拓扑节点图\n（含节点名称、红绿灯、闸机标注及稠密路网拓扑）", fontsize=16, fontweight='bold', pad=15)
plt.xlabel("相对坐标 X", fontsize=10)
plt.ylabel("相对坐标 Y", fontsize=10)

plt.grid(True, linestyle='--', alpha=0.3)
plt.axis('on')
plt.tight_layout()

# 显示图形
plt.show()