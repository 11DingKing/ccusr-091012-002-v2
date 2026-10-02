"""
仓库管理序列化器
"""
from decimal import Decimal

from rest_framework import serializers
from .models import (
    Unit, Category, Variety, Goods, StockIn, StockOut, Warning,
    ApprovalRuleVersion, ApprovalRoute, ApprovalNode, ApprovalNodeApprover,
    ApprovalTask,
)


class UnitSerializer(serializers.ModelSerializer):
    """单位序列化器"""
    is_linked = serializers.BooleanField(read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)
    
    class Meta:
        model = Unit
        fields = [
            'id', 'name', 'is_linked', 'is_active',
            'created_by', 'created_by_name', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class UnitCreateSerializer(serializers.Serializer):
    """单位创建序列化器"""
    name = serializers.CharField(min_length=1, max_length=5, required=True, error_messages={
        'required': '请输入单位名称',
        'blank': '单位名称不能为空',
        'min_length': '单位名称至少1个字',
        'max_length': '单位名称最多5个字',
    })
    
    def validate_name(self, value):
        instance = self.context.get('instance')
        if instance:
            if Unit.objects.filter(name=value).exclude(pk=instance.pk).exists():
                raise serializers.ValidationError('单位名称已存在')
        else:
            if Unit.objects.filter(name=value).exists():
                raise serializers.ValidationError('单位名称已存在')
        return value


class CategorySerializer(serializers.ModelSerializer):
    """品类序列化器"""
    is_linked = serializers.BooleanField(read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)
    unit_name = serializers.CharField(source='unit.name', read_only=True)
    
    class Meta:
        model = Category
        fields = [
            'id', 'name', 'unit', 'unit_name', 'is_linked', 'is_active',
            'created_by', 'created_by_name', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class CategoryCreateSerializer(serializers.Serializer):
    """品类创建序列化器"""
    name = serializers.CharField(min_length=1, max_length=10, required=True, error_messages={
        'required': '请输入品类名称',
        'blank': '品类名称不能为空',
        'min_length': '品类名称至少1个字',
        'max_length': '品类名称最多10个字',
    })
    unit = serializers.IntegerField(required=True, error_messages={
        'required': '请选择单位',
    })
    
    def validate_name(self, value):
        instance = self.context.get('instance')
        if instance:
            if Category.objects.filter(name=value).exclude(pk=instance.pk).exists():
                raise serializers.ValidationError('品类名称已存在')
        else:
            if Category.objects.filter(name=value).exists():
                raise serializers.ValidationError('品类名称已存在')
        return value
    
    def validate_unit(self, value):
        if not Unit.objects.filter(pk=value).exists():
            raise serializers.ValidationError('单位不存在')
        return value


class VarietySerializer(serializers.ModelSerializer):
    """品种序列化器"""
    is_in_stock = serializers.BooleanField(read_only=True)
    unit_name = serializers.CharField(read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)
    category_name = serializers.CharField(source='category.name', read_only=True)
    
    class Meta:
        model = Variety
        fields = [
            'id', 'name', 'category', 'category_name', 'unit_name',
            'is_in_stock', 'is_active',
            'created_by', 'created_by_name', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class VarietyCreateSerializer(serializers.Serializer):
    """品种创建序列化器"""
    name = serializers.CharField(min_length=1, max_length=20, required=True, error_messages={
        'required': '请输入品种名称',
        'blank': '品种名称不能为空',
        'min_length': '品种名称至少1个字',
        'max_length': '品种名称最多20个字',
    })
    category = serializers.IntegerField(required=True, error_messages={
        'required': '请选择品类',
    })
    
    def validate_category(self, value):
        if not Category.objects.filter(pk=value).exists():
            raise serializers.ValidationError('品类不存在')
        return value
    
    def validate(self, data):
        instance = self.context.get('instance')
        name = data['name']
        category_id = data['category']
        
        if instance:
            if Variety.objects.filter(name=name, category_id=category_id).exclude(pk=instance.pk).exists():
                raise serializers.ValidationError('该品类下已存在同名品种')
        else:
            if Variety.objects.filter(name=name, category_id=category_id).exists():
                raise serializers.ValidationError('该品类下已存在同名品种')
        return data


class GoodsSerializer(serializers.ModelSerializer):
    """货物序列化器"""
    variety_name = serializers.CharField(source='variety.name', read_only=True)
    category_name = serializers.CharField(source='variety.category.name', read_only=True)
    unit_name = serializers.CharField(source='variety.category.unit.name', read_only=True)
    risk_level_display = serializers.CharField(source='get_risk_level_display', read_only=True)
    is_warning = serializers.BooleanField(read_only=True)

    class Meta:
        model = Goods
        fields = [
            'id', 'name', 'code', 'variety', 'variety_name',
            'category_name', 'unit_name', 'specification',
            'quantity', 'warning_threshold', 'risk_level', 'risk_level_display',
            'location', 'remark', 'is_active', 'is_warning',
            'created_at', 'updated_at'
        ]


class StockInSerializer(serializers.ModelSerializer):
    """入库记录序列化器"""
    goods_name = serializers.CharField(source='goods.name', read_only=True)
    operator_name = serializers.CharField(source='operator.username', read_only=True)
    
    class Meta:
        model = StockIn
        fields = [
            'id', 'goods', 'goods_name', 'operator', 'operator_name',
            'quantity', 'batch_no', 'supplier', 'stock_in_time', 'remark'
        ]


class StockOutSerializer(serializers.ModelSerializer):
    """出库申请序列化器（列表/详情）"""
    goods_name = serializers.CharField(source='goods.name', read_only=True)
    risk_level = serializers.CharField(source='goods.risk_level', read_only=True)
    risk_level_display = serializers.SerializerMethodField()
    operator_name = serializers.CharField(source='operator.username', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    purpose_display = serializers.CharField(source='get_purpose_display', read_only=True)
    rule_version_no = serializers.IntegerField(source='rule_version.version', read_only=True)
    route_name = serializers.SerializerMethodField()
    my_pending_task_id = serializers.SerializerMethodField()

    class Meta:
        model = StockOut
        fields = [
            'id', 'goods', 'goods_name', 'risk_level', 'risk_level_display',
            'operator', 'operator_name',
            'receiver', 'receiver_dept', 'purpose', 'purpose_display',
            'quantity', 'status', 'status_display',
            'rule_version', 'rule_version_no', 'route_name',
            'route_generation', 'current_node_order',
            'my_pending_task_id',
            'resubmitted_from',
            'stock_out_time', 'remark', 'created_at', 'updated_at'
        ]

    def get_risk_level_display(self, obj):
        return obj.goods.get_risk_level_display() if obj.goods_id else ''

    def get_route_name(self, obj):
        return (obj.route_snapshot or {}).get('route', {}).get('name', '')

    def get_my_pending_task_id(self, obj):
        request = self.context.get('request')
        if request is None or not request.user.is_authenticated:
            return None
        task = obj.approval_tasks.filter(
            approver=request.user, status=ApprovalTask.STATUS_PENDING,
            generation=obj.route_generation,
        ).first()
        return task.id if task else None


class StockOutCreateSerializer(serializers.Serializer):
    """创建出库申请"""
    goods = serializers.IntegerField(required=True, error_messages={'required': '请选择物资'})
    quantity = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal('0.01'),
        required=True, error_messages={'required': '请填写申领数量', 'min_value': '数量必须大于0'}
    )
    receiver = serializers.CharField(max_length=100, required=True, error_messages={
        'required': '请填写领用人', 'blank': '领用人不能为空'})
    receiver_dept = serializers.CharField(max_length=100, required=False, allow_blank=True, default='')
    purpose = serializers.ChoiceField(
        choices=StockOut.PURPOSE_CHOICES, required=False, default='', allow_blank=True
    )
    remark = serializers.CharField(max_length=2000, required=False, allow_blank=True, default='')

    def validate_goods(self, value):
        if not Goods.objects.filter(pk=value, is_active=True).exists():
            raise serializers.ValidationError('物资不存在或已停用')
        return value


class StockOutReevaluateSerializer(serializers.Serializer):
    """在途申请条件变更"""
    goods = serializers.IntegerField(required=False)
    quantity = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal('0.01'), required=False
    )
    receiver_dept = serializers.CharField(max_length=100, required=False, allow_blank=True)
    purpose = serializers.ChoiceField(
        choices=StockOut.PURPOSE_CHOICES, required=False, allow_blank=True
    )

    def validate_goods(self, value):
        if not Goods.objects.filter(pk=value, is_active=True).exists():
            raise serializers.ValidationError('物资不存在或已停用')
        return value


class StockOutResubmitSerializer(serializers.Serializer):
    """拒绝后重提：字段全部可选，缺省沿用原申请内容"""
    goods = serializers.IntegerField(required=False)
    quantity = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal('0.01'), required=False
    )
    receiver = serializers.CharField(max_length=100, required=False)
    receiver_dept = serializers.CharField(max_length=100, required=False, allow_blank=True)
    purpose = serializers.ChoiceField(
        choices=StockOut.PURPOSE_CHOICES, required=False, allow_blank=True
    )
    remark = serializers.CharField(max_length=2000, required=False, allow_blank=True)

    def validate_goods(self, value):
        if not Goods.objects.filter(pk=value, is_active=True).exists():
            raise serializers.ValidationError('物资不存在或已停用')
        return value


class DecisionSerializer(serializers.Serializer):
    """审批人签署：同意 / 拒绝 / 回避"""
    ACTION_CHOICES = [('approved', '同意'), ('rejected', '拒绝'), ('recused', '回避')]

    action = serializers.ChoiceField(choices=ACTION_CHOICES, required=True)
    remark = serializers.CharField(max_length=1000, required=False, allow_blank=True, default='')

    def validate(self, data):
        if data['action'] == 'rejected' and not (data.get('remark') or '').strip():
            raise serializers.ValidationError({'remark': '拒绝时必须填写审批意见'})
        return data


# ==================== 审批规则配置 ====================

class NodeApproverInputSerializer(serializers.Serializer):
    approver = serializers.IntegerField(required=True)
    order = serializers.IntegerField(required=False, min_value=1)


class NodeInputSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100, required=True)
    order = serializers.IntegerField(required=True, min_value=1)
    sign_mode = serializers.ChoiceField(
        choices=[('single', '单人签署'), ('any', '任一签署'), ('all', '多人会签')],
        required=False, default='single'
    )
    use_active_admins = serializers.BooleanField(required=False, default=True)
    approvers = NodeApproverInputSerializer(many=True, required=False, default=list)

    def validate_approvers(self, value):
        ids = [item['approver'] for item in value]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError('同一节点不能重复指定审批人')
        from apps.authentication.models import User
        missing = set(ids) - set(User.objects.filter(id__in=ids).values_list('id', flat=True))
        if missing:
            raise serializers.ValidationError(f'审批人不存在：{sorted(missing)}')
        return value


class RouteInputSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100, required=True)
    risk_level = serializers.ChoiceField(
        choices=Goods.RISK_CHOICES, required=False, allow_blank=True, default=''
    )
    min_quantity = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True, min_value=0
    )
    max_quantity = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True, min_value=0
    )
    receiver_dept = serializers.CharField(max_length=100, required=False, allow_blank=True, default='')
    purpose = serializers.ChoiceField(
        choices=StockOut.PURPOSE_CHOICES, required=False, allow_blank=True, default=''
    )
    priority = serializers.IntegerField(required=False, min_value=1, default=100)
    is_fallback = serializers.BooleanField(required=False, default=False)
    is_active = serializers.BooleanField(required=False, default=True)
    nodes = NodeInputSerializer(many=True, required=True)

    def validate(self, data):
        if not data.get('nodes'):
            raise serializers.ValidationError({'nodes': '每条路线至少配置一个审批节点'})
        orders = [n['order'] for n in data['nodes']]
        if len(orders) != len(set(orders)):
            raise serializers.ValidationError({'nodes': '节点顺序不能重复'})
        mn, mx = data.get('min_quantity'), data.get('max_quantity')
        if mn is not None and mx is not None and mn > mx:
            raise serializers.ValidationError('数量下限不能大于上限')
        return data


class RuleVersionWriteSerializer(serializers.Serializer):
    """创建草稿版本（可一次带路线，也可建空草稿再逐条维护）"""
    remark = serializers.CharField(max_length=200, required=False, allow_blank=True, default='')
    routes = RouteInputSerializer(many=True, required=False, default=list)

    def validate_routes(self, value):
        fallbacks = [r for r in value if r.get('is_fallback')]
        if value and len(fallbacks) > 1:
            raise serializers.ValidationError('一个版本最多配置一条兜底路线')
        names = [r['name'] for r in value]
        if len(names) != len(set(names)):
            raise serializers.ValidationError('同版本内路线名称不能重复')
        return value


class NodeApproverOutputSerializer(serializers.ModelSerializer):
    approver_name = serializers.CharField(source='approver.real_name', read_only=True)
    username = serializers.CharField(source='approver.username', read_only=True)

    class Meta:
        model = ApprovalNodeApprover
        fields = ['id', 'approver', 'approver_name', 'username', 'order']


class NodeOutputSerializer(serializers.ModelSerializer):
    sign_mode_display = serializers.CharField(source='get_sign_mode_display', read_only=True)
    approvers = NodeApproverOutputSerializer(many=True, read_only=True)

    class Meta:
        model = ApprovalNode
        fields = ['id', 'name', 'order', 'sign_mode', 'sign_mode_display',
                  'use_active_admins', 'approvers']


class RouteOutputSerializer(serializers.ModelSerializer):
    risk_level_display = serializers.CharField(source='get_risk_level_display', read_only=True)
    nodes = NodeOutputSerializer(many=True, read_only=True)

    class Meta:
        model = ApprovalRoute
        fields = ['id', 'name', 'risk_level', 'risk_level_display',
                  'min_quantity', 'max_quantity', 'receiver_dept', 'purpose',
                  'priority', 'is_fallback', 'is_active', 'nodes']


class RuleVersionOutputSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    routes = RouteOutputSerializer(many=True, read_only=True)
    published_by_name = serializers.SerializerMethodField()
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)

    class Meta:
        model = ApprovalRuleVersion
        fields = ['id', 'version', 'status', 'status_display', 'remark',
                  'published_at', 'published_by_name',
                  'created_by_name', 'created_at', 'routes']

    def get_published_by_name(self, obj):
        return obj.published_by.username if obj.published_by_id else ''


class ApprovalTaskSerializer(serializers.ModelSerializer):
    """签署任务序列化器"""
    approver_name = serializers.CharField(source='approver.real_name', read_only=True)
    username = serializers.CharField(source='approver.username', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    is_replacement = serializers.BooleanField(source='replacement_for_id', read_only=True)

    class Meta:
        model = ApprovalTask
        fields = [
            'id', 'stock_out', 'generation', 'node_order', 'node_name',
            'sign_mode', 'approver', 'approver_name', 'username',
            'status', 'status_display', 'is_replacement',
            'remark', 'acted_at', 'created_at',
        ]


class WarningSerializer(serializers.ModelSerializer):
    """预警记录序列化器"""
    goods_name = serializers.CharField(source='goods.name', read_only=True)
    type_display = serializers.CharField(source='get_type_display', read_only=True)

    class Meta:
        model = Warning
        fields = [
            'id', 'goods', 'goods_name', 'type', 'type_display',
            'message', 'is_read', 'created_at'
        ]
