from django.shortcuts import render

# Create your views here.
from rest_framework.views import APIView
from rest_framework.response import Response
from django.contrib.auth.hashers import make_password, check_password
from rest_framework_simplejwt.tokens import RefreshToken
from drf_spectacular.utils import extend_schema, OpenApiParameter

from .models import AdminUser, Node, Alert
from .serializers import AdminUserSerializer, NodeSerializer, AlertSerializer, AlertCreateSerializer, \
    AlertResolveSerializer

from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed
from datetime import datetime
from django.db.models import Q




# --- 统一返回格式工具函数 (保留了你 FastAPI 的习惯，并适配约定的 code 码) ---
def success_response(data=None, message="ok"):
    return {"code": 0, "message": message, "data": data or {}}


def error_response(message="失败", code=40001):
    return {"code": code, "message": message, "data": {}}


# ==========================================
#           管理员系统视图
# ==========================================

class CustomJWTAuthentication(JWTAuthentication):
    def get_user(self, validated_token):
        # 1. 从 token 中解析出当初存进去的 user_id
        user_id = validated_token.get('user_id')
        # 2. 去我们自己的 AdminUser 表里查人，而不是 Django 默认的表
        user = AdminUser.objects.filter(id=user_id).first()

        if not user:
            raise AuthenticationFailed('User not found', code='user_not_found')
        return user


class RegisterView(APIView):
    @extend_schema(summary="管理员注册",tags=["1. 管理员系统"], request=AdminUserSerializer)
    def post(self, request):
        username = request.data.get("username")
        password = request.data.get("password")

        # 1. 查：检查用户名是否已被注册
        if AdminUser.objects.filter(username=username).exists():
            return Response(error_response("该用户名已被注册，请换一个", code=40002))

        # 2. 增：使用 Django 内置函数替代原生 bcrypt 进行哈希加密
        hashed_pwd = make_password(password)
        AdminUser.objects.create(username=username, password=hashed_pwd)

        return Response(success_response(message=f"管理员 {username} 注册成功！"))


class LoginView(APIView):
    @extend_schema(summary="管理员登录",tags=["1. 管理员系统"], request=AdminUserSerializer)
    def post(self, request):
        username = request.data.get("username")
        password = request.data.get("password")

        # 1. 查：去数据库匹配用户名
        user = AdminUser.objects.filter(username=username).first()

        # 2. 核对：替代原生 bcrypt.checkpw，使用 check_password 校验
        if not user or not check_password(password, user.password):
            return Response(error_response("用户名或密码错误", code=40401))

        # 3. 接入 JWT 签发 Token
        refresh = RefreshToken.for_user(user)
        access_token = str(refresh.access_token)

        return Response(success_response({"token": access_token}, message="登录成功"))


class AdminInfoView(APIView):
    authentication_classes = [CustomJWTAuthentication, JWTAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="获取当前管理员信息", tags=["1. 管理员系统"])
    def get(self, request):
        # 核心改动：直接从 request 中提取刚刚通过 Token 鉴权的用户对象
        user = request.user

        # 组装返回数据，不需要再去数据库 filter 了！
        safe_info = {
            "id": user.id,
            "username": user.username,
            "role": user.role
        }
        return Response(success_response(safe_info, message="查看成功"))

class NodeMonitorView(APIView):
    authentication_classes = [CustomJWTAuthentication, JWTAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="查询交通网络各节点的实时状态",
        tags=["2. 交通网络"],
        parameters=[
            OpenApiParameter(name="nodeId", description="节点编码", required=False, type=str),
            OpenApiParameter(name="type", description="按节点类型过滤", required=False, type=str),
        ]
    )
    def get(self, request):
        node_id = request.query_params.get("nodeId")
        node_type = request.query_params.get("type")

        queryset = Node.objects.all()
        if node_id:
            queryset = queryset.filter(node_id=node_id)
        if node_type:
            queryset = queryset.filter(node_type=node_type)

        serializer = NodeSerializer(queryset, many=True)
        return Response({
            "code": 0,
            "message": "ok",
            "data": {"nodes": serializer.data}
        })
# ===== 统一响应工具函数 =====
def success_response(data=None, message="ok"):
    return {"code": 0, "message": message, "data": data or {}}

def error_response(message="失败", code=40001):
    return {"code": code, "message": message, "data": {}}


# ===== 1. 预警事件查询接口（前端 E 调用） =====
class AlertListView(APIView):
    authentication_classes = [CustomJWTAuthentication, JWTAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="查询预警事件列表",
        tags=["4. 预警与应急响应"],
        parameters=[
            OpenApiParameter(name="status", description="pending/resolved/all", required=False, type=str),
            OpenApiParameter(name="riskLevel", description="yellow/red", required=False, type=str),
            OpenApiParameter(name="nodeId", description="节点编码", required=False, type=str),
            OpenApiParameter(name="page", description="当前页码", required=False, type=int),
            OpenApiParameter(name="pageSize", description="每页条数", required=False, type=int),
        ]
    )
    def get(self, request):
        status = request.query_params.get('status', 'all')
        risk_level = request.query_params.get('riskLevel')
        node_id = request.query_params.get('nodeId')

        queryset = Alert.objects.all().order_by('-created_at')

        if status != 'all':
            queryset = queryset.filter(status=status)
        if risk_level:
            queryset = queryset.filter(risk_level=risk_level)
        if node_id:
            queryset = queryset.filter(node__node_id=node_id)

        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('pageSize', 20))
        start = (page - 1) * page_size
        end = start + page_size

        total = queryset.count()
        items = queryset[start:end]

        serializer = AlertSerializer(items, many=True)

        return Response(success_response({
            "items": serializer.data,
            "total": total,
            "page": page,
            "pageSize": page_size
        }))


# ===== 2. 预警事件入库接口（D 直接调用） =====
class AlertCreateView(APIView):
    authentication_classes = [CustomJWTAuthentication, JWTAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="创建预警事件（由 D 调用）",
        tags=["4. 预警与应急响应"],
        request=AlertCreateSerializer
    )
    def post(self, request):
        data = request.data

        # 1. 校验必填字段
        required_fields = ['node_id', 'level', 'timestamp', 'type']
        for field in required_fields:
            if field not in data:
                return Response(error_response(f"缺少必填字段: {field}", 40001))

        # 2. 校验节点是否存在
        try:
            node = Node.objects.get(node_id=data['node_id'])
        except Node.DoesNotExist:
            return Response(error_response(f"节点不存在: {data['node_id']}", 40401))

        # 3. 等级映射：L1→yellow, L2→red, L3→red
        level_map = {'L1': 'yellow', 'L2': 'red', 'L3': 'red'}
        risk_level = level_map.get(data['level'], 'yellow')
        if risk_level not in ['yellow', 'red']:
            return Response(error_response(f"level 取值非法: {data['level']}，只能为 L1/L2/L3", 40002))

        # 4. 状态映射：active→pending, resolved→resolved
        status_map = {'active': 'pending', 'resolved': 'resolved'}
        status = status_map.get(data.get('status', 'active'), 'pending')

        # 5. 拼接描述
        description = (
            f"{data.get('type', 'unknown')}预警 - "
            f"节点:{data.get('node_name', data['node_id'])}，"
            f"当前密度:{data.get('current_density', 'N/A')}，"
            f"阈值:{data.get('threshold_density', 'N/A')}，"
            f"预计持续:{data.get('predicted_duration_min', 'N/A')}分钟"
        )

        # 6. 生成 alertId
        today = datetime.now().strftime('%Y%m%d')
        count = Alert.objects.filter(created_at__date=datetime.now().date()).count() + 1
        alert_id = f"ALT-{today}-{count:03d}"

        # 7. 解析时间
        try:
            created_at = datetime.strptime(data['timestamp'], '%Y-%m-%dT%H:%M:%S')
        except ValueError:
            return Response(error_response("timestamp 格式错误，应为 YYYY-MM-DDTHH:MM:SS", 40001))

        # 8. 创建预警
        alert = Alert.objects.create(
            alert_id=alert_id,
            node=node,
            node_name=data.get('node_name', node.node_name),
            risk_level=risk_level,
            status=status,
            trigger_source='ai_prediction',
            description=description,
            created_at=created_at,
            action_taken=data.get('suggested_action'),
            event_id=data.get('event_id'),
            current_density=data.get('current_density'),
            threshold_density=data.get('threshold_density'),
            predicted_duration_min=data.get('predicted_duration_min')
        )

        return Response(success_response({
            "alertId": alert.alert_id,
            "status": alert.status,
            "createdAt": alert.created_at.strftime('%Y-%m-%d %H:%M:%S')
        }))


# ===== 3. 应急响应状态更新接口 =====
class AlertResolveView(APIView):
    authentication_classes = [CustomJWTAuthentication, JWTAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="更新预警状态为已解决",
        tags=["4. 预警与应急响应"],
        request=AlertResolveSerializer
    )
    def put(self, request, alert_id):
        data = request.data

        if 'actionTaken' not in data:
            return Response(error_response("actionTaken 参数缺失", 40001))

        try:
            alert = Alert.objects.get(alert_id=alert_id)
        except Alert.DoesNotExist:
            return Response(error_response("预警不存在", 40401))

        if alert.status == 'resolved':
            return Response(error_response("预警已被解决", 40402))

        alert.status = 'resolved'
        alert.resolved_at = datetime.now()
        alert.action_taken = data['actionTaken']
        alert.save()

        return Response(success_response({
            "alertId": alert.alert_id,
            "status": alert.status,
            "resolvedAt": alert.resolved_at.strftime('%Y-%m-%d %H:%M:%S'),
            "actionTaken": alert.action_taken
        }))