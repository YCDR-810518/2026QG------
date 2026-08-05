from django.urls import path
from . import views

urlpatterns = [
    # 后续业务路由在此配置
    # 模块一：管理员系统
    path('admin/register', views.RegisterView.as_view(), name='admin-register'),
    path('admin/login', views.LoginView.as_view(), name='admin-login'),
    path('admin/info', views.AdminInfoView.as_view(), name='admin-info'),
    path('network/nodes', views.NodeMonitorView.as_view(), name='network-nodes'),

    # ===== 模块四：预警与应急响应 =====
    path('security/alerts', views.AlertListView.as_view(), name='alert-list'),
    path('security/alerts/create', views.AlertCreateView.as_view(), name='alert-create'),
    path('security/alerts/<str:alert_id>/resolve', views.AlertResolveView.as_view(), name='alert-resolve'),
]
