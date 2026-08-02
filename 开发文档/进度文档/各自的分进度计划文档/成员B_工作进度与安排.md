# 成员B（张紫桐）工作进度与安排

> 角色：后端开发 / 数据库负责人
>
> 框架：**Django + Django REST Framework**（8.2 起更新）
>
> 覆盖：2026.08.01 - 2026.08.08 ｜ 状态标记：□ 未开始 ｜ ◐ 进行中 ｜ ☑ 完成

## 一、总体目标

- 负责 FR-01~05：管理员系统、RESTful API、数据库建表（第三范式）、系统安全防护
- 交付物：Django 项目工程、六表 Model 与 Migration、全部 REST 接口实现、安全防护配置、接口文档定稿

## 二、接口清单

| 接口 | 说明 | FR | 实现方式 | 状态 |
|------|------|-----|---------|------|
| Django 项目脚手架 | `startproject` + settings + logging + 目录划分 | FR-03/04 | Django 项目结构 | □ |
| 六表 Model 定义 | 用户/车辆/节点/门闸/日志/预警（第三范式） | FR-04 | Django ORM `models.Model` | □ |
| Django Admin 注册 | 后台数据管理（可顶替部分 FR-26 页面） | FR-02 | `admin.site.register` | □ |
| `POST /admin/register` | 管理员注册 | FR-01 | DRF ViewSet + `create` | □ |
| `POST /admin/login` | 登录（JWT + 密码加密） | FR-01 | `simplejwt` TokenObtainPairView | □ |
| `GET /admin/info` | 管理员信息 | FR-02 | DRF ViewSet + `retrieve` | □ |
| 实体 CRUD | 用户/车辆/节点/门闸/日志/预警 | FR-02 | DRF ModelViewSet | □ |
| 实时数据接口 | 密度实时/历史/热力图/预测/准入状态 | FR-27 | DRF ViewSet + `@action` | □ |
| 交通网络接口 | 节点监控/热度/最短路径/热点 | FR-06~09 | DRF ViewSet + `@action` | □ |
| 门闸接口 | 状态/控制/策略查询/策略配置 | FR-19 | DRF ViewSet + `@action` | □ |
| 车辆准入接口 | 申请/决策/路线 | FR-13 | DRF ViewSet + `@action` | □ |
| 预警接口 | 查询/入库/响应状态 | FR-22/23 | DRF ViewSet + `@action` | □ |
| 安全防护 | 防注入/XSS/限流/恶意访问检测 | FR-05 | ORM 自动防注入 + `django-ratelimit` + DRF permissions | □ |
| 通用约定 | Base URL/JWT/响应/错误码/分页 | FR-03 | DRF settings + exception_handler | □ |

## 三、每日安排表

| 日期 | 阶段 | 当天完成内容 | 协作对接 | 状态 |
|------|------|-------------|---------|------|
| 8.1(六) | 规划 | [已逾] 任务并入 8.2 | — | — |
| 8.2(日) | 规划 | Django Tutorial Part 1-4 + DRF Quickstart（上午）；主持协作1 接口字段汇总冻结（下午）；`django-admin startproject` + settings 配置（晚上） | 上午 协作1：主持全员接口字段汇总冻结 | □ |
| 8.3(一) | 开发 | 六表 Model 定义 + `makemigrations` + `migrate`；Django Admin 注册各模型；`register`/`login`+JWT、密码加密；`admin/info` | 向 E 答疑接口细节 | □ |
| 8.4(二) | 开发 | 全业务接口 DRF ViewSet + Serializer（实时数据/交通网络/门闸/车辆准入/预警）；安全防护（`django-ratelimit` + DRF permission_classes） | — | □ |
| 8.5(三) | 联调 | 接口自测修 bug；联调 | 上午 与 E 联调认证 + CRUD；下午 与 D 预警入库打通（D 通过 Django Model 写入） | □ |
| 8.6(四) | 联调 | 联调全部业务接口、修复问题；修订接口文档 | 全天 与 E 联调全部业务接口 | □ |
| 8.7(五) | 测试 | 接口文档冻结；配合全流程测试 | 下午 全员整合测试 | □ |
| 8.8(六) | 交付 | 交付前自检 | — | □ |

## 四、协作日历

| 时间 | 对方 | 事项 | 产出 |
|------|------|------|------|
| 8.2 下午 | 全员（主持） | 接口字段汇总定稿 | 接口冻结版 + Django models.py |
| 8.3-8.4 | E | 接口答疑 | E 照文档顺利开发 |
| 8.5 上午 | E | 联调认证+CRUD 接口 | 登录/数据管理可用 |
| 8.5 下午 | E / D | 联调业务接口；D 预警数据入库 | 业务接口通、预警全链路通 |
| 8.6 全天 | E | 联调全部业务接口 | 大屏真实数据 |
| 8.7 下午 | 全员 | 整合测试 | 测试通过 |
| 8.7 晚 | — | 接口文档冻结 | 冻结版 |

## 五、风险与兜底

- **Django 学习曲线** → 今日（8.2）完成 Tutorial + DRF Quickstart 最小集（4 小时），仅需掌握 Model / ModelViewSet / Serializer / JWT 配置四项，不碰 Template / Form / Celery
- **依赖 A/C/D/F 须在 8.2 提供字段** → 兜底：先按需求分析字段占位，变更走全员确认
- **E 联调受阻** → 接口 8.2 冻结，联调差异统一记录修订
- **Django 推进严重受阻（8.3 傍晚判定）** → 兜底：退回 Flask，接口文档已冻结不受影响，损失 ≤ 1 天

## 六、Django 安装清单（最小集）

```
pip install django djangorestframework djangorestframework-simplejwt django-ratelimit django-cors-headers
```

### 项目目录结构（建议）

```
backend/
├── manage.py
├── config/                  # settings / urls / wsgi
│   ├── settings.py
│   └── urls.py
└── api/                     # 主 app
    ├── models.py            # 六张表
    ├── serializers.py       # DRF Serializer
    ├── views.py             # DRF ViewSet
    ├── urls.py              # DRF Router
    ├── permissions.py       # 自定义权限
    └── admin.py             # Admin 注册
```

## 附注：飞书同步规则

- 每日站会后在**飞书多维表格**更新任务状态（□→◐→☑）
- 飞书为实时进度源；本文档为交付快照，**8.7 冻结**时按飞书结果落最终状态
