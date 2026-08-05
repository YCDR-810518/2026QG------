from rest_framework import serializers
from .models import AdminUser, Node, Alert


class AdminUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = AdminUser
        fields = ['username', 'password']



class NodeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Node
        fields = '__all__'

# ===== 预警序列化器 =====
class AlertSerializer(serializers.ModelSerializer):
    alertId = serializers.CharField(source='alert_id', read_only=True)
    nodeId = serializers.CharField(source='node.node_id', read_only=True)
    nodeName = serializers.CharField(source='node_name', read_only=True)
    riskLevel = serializers.CharField(source='risk_level', read_only=True)
    triggerSource = serializers.CharField(source='trigger_source', read_only=True)
    createdAt = serializers.DateTimeField(source='created_at', read_only=True)
    resolvedAt = serializers.DateTimeField(source='resolved_at', read_only=True)
    actionTaken = serializers.CharField(source='action_taken', read_only=True)

    class Meta:
        model = Alert
        fields = [
            'alertId', 'nodeId', 'nodeName', 'riskLevel', 'status',
            'triggerSource', 'description', 'createdAt', 'resolvedAt', 'actionTaken'
        ]

# ===== 预警入站序列化器（D 调用时使用） =====
class AlertCreateSerializer(serializers.Serializer):
    event_id = serializers.CharField(required=False, allow_blank=True)
    timestamp = serializers.DateTimeField(required=True)
    level = serializers.ChoiceField(choices=['L1', 'L2', 'L3'], required=True)
    type = serializers.CharField(required=True)
    node_id = serializers.CharField(required=True)
    node_name = serializers.CharField(required=True)
    current_density = serializers.FloatField(required=False, allow_null=True)
    threshold_density = serializers.FloatField(required=False, allow_null=True)
    predicted_duration_min = serializers.IntegerField(required=False, allow_null=True)
    suggested_action = serializers.CharField(required=False, allow_blank=True)
    status = serializers.ChoiceField(choices=['active', 'resolved'], required=False, default='active')

    def validate_level(self, value):
        if value not in ['L1', 'L2', 'L3']:
            raise serializers.ValidationError("level 只能为 L1、L2 或 L3")
        return value


# ===== 预警解决序列化器 =====
class AlertResolveSerializer(serializers.Serializer):
    actionTaken = serializers.CharField(required=True)