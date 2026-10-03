"""
仓库管理序列化器
"""
from decimal import Decimal

from rest_framework import serializers
from .models import (
    Unit, Category, Variety, Goods, StockIn, StockOut, Warning,
    ApprovalRuleVersion, ApprovalStep, StepSigner, ApprovalEvent,
    ApprovalRoleAssignment,
)
from . import approval_engine


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


class GoodsCreateSerializer(serializers.Serializer):
    """货物创建/更新序列化器"""
    name = serializers.CharField(max_length=200, required=True)
    code = serializers.CharField(max_length=50, required=True)
    variety = serializers.IntegerField(required=True)
    specification = serializers.CharField(max_length=200, required=False, allow_blank=True, default='')
    quantity = serializers.DecimalField(max_digits=12, decimal_places=2, required=False,
                                        min_value=Decimal('0'), default=Decimal('0'))
    warning_threshold = serializers.DecimalField(max_digits=12, decimal_places=2, required=False,
                                                 min_value=Decimal('0'), default=Decimal('10'))
    risk_level = serializers.ChoiceField(choices=Goods.RISK_CHOICES, required=False, default=Goods.RISK_NORMAL)
    location = serializers.CharField(max_length=100, required=False, allow_blank=True, default='')
    remark = serializers.CharField(required=False, allow_blank=True, default='')
    is_active = serializers.BooleanField(required=False, default=True)

    def validate_variety(self, value):
        if not Variety.objects.filter(pk=value).exists():
            raise serializers.ValidationError('品种不存在')
        return value

    def validate_code(self, value):
        instance = self.context.get('instance')
        qs = Goods.objects.filter(code=value)
        if instance:
            qs = qs.exclude(pk=instance.pk)
        if qs.exists():
            raise serializers.ValidationError('货物编码已存在')
        return value


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
    """出库申请列表序列化器"""
    goods_name = serializers.CharField(source='goods.name', read_only=True)
    goods_code = serializers.CharField(source='goods.code', read_only=True)
    operator_name = serializers.CharField(source='operator.username', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    purpose_type_display = serializers.CharField(source='get_purpose_type_display', read_only=True)
    risk_level_display = serializers.CharField(source='get_risk_level_display', read_only=True)
    rule_version_number = serializers.IntegerField(source='rule_version.version', read_only=True)
    current_step_name = serializers.CharField(source='current_step.name', read_only=True, default=None)

    class Meta:
        model = StockOut
        fields = [
            'id', 'goods', 'goods_name', 'goods_code', 'operator', 'operator_name',
            'receiver', 'receiver_dept', 'quantity',
            'purpose_type', 'purpose_type_display', 'purpose',
            'risk_level', 'risk_level_display',
            'status', 'status_display', 'rule_version_number', 'current_step_name',
            'revision_no', 'revised_from',
            'stock_out_time', 'remark', 'created_at'
        ]
        read_only_fields = fields


class StockOutCreateSerializer(serializers.Serializer):
    """出库申请创建序列化器（创建即提交，提交即冻结规则快照）"""
    goods = serializers.IntegerField(required=True, error_messages={'required': '请选择货物'})
    receiver = serializers.CharField(max_length=100, required=True,
                                     error_messages={'required': '请填写领用人', 'blank': '领用人不能为空'})
    receiver_dept = serializers.CharField(max_length=100, required=False, allow_blank=True, default='')
    quantity = serializers.DecimalField(max_digits=12, decimal_places=2, required=True,
                                        min_value=Decimal('0.01'),
                                        error_messages={'required': '请填写出库数量'})
    purpose_type = serializers.ChoiceField(choices=StockOut.PURPOSE_CHOICES, required=False,
                                           default=StockOut.PURPOSE_USE)
    purpose = serializers.CharField(required=False, allow_blank=True, default='')
    remark = serializers.CharField(required=False, allow_blank=True, default='')

    def validate_goods(self, value):
        try:
            goods = Goods.objects.get(pk=value)
        except Goods.DoesNotExist:
            raise serializers.ValidationError('货物不存在')
        if not goods.is_active:
            raise serializers.ValidationError('货物已停用，不能申领')
        return goods

    def validate(self, data):
        if data['quantity'] > data['goods'].quantity:
            raise serializers.ValidationError(f"库存不足，当前库存 {data['goods'].quantity}")
        return data


class StockOutResubmitSerializer(StockOutCreateSerializer):
    """拒绝后重提：允许沿用原单信息并覆盖修订"""


class StepSignerSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source='user.real_name', read_only=True)
    username = serializers.CharField(source='user.username', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    source_display = serializers.SerializerMethodField()

    class Meta:
        model = StepSigner
        fields = [
            'id', 'user', 'username', 'user_name', 'status', 'status_display',
            'source', 'source_display', 'comment', 'signed_at', 'created_at'
        ]

    def get_source_display(self, obj):
        return {'normal': '正常指派', 'delegated': '改派',
                'auto_recused': '自动回避', 'active_recused': '主动回避'}.get(obj.source, obj.source)


class ApprovalStepSerializer(serializers.ModelSerializer):
    signers = StepSignerSerializer(many=True, read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    role_name = serializers.SerializerMethodField()
    policy_text = serializers.SerializerMethodField()

    class Meta:
        model = ApprovalStep
        fields = [
            'id', 'order_index', 'name', 'role_code', 'role_name',
            'sign_policy', 'policy_text', 'quorum', 'reason',
            'status', 'status_display', 'signers',
            'activated_at', 'finished_at', 'created_at'
        ]

    def get_role_name(self, obj):
        return approval_engine.ROLE_NAMES.get(obj.role_code, obj.role_code)

    def get_policy_text(self, obj):
        return approval_engine.policy_text(obj)


class ApprovalEventSerializer(serializers.ModelSerializer):
    type_display = serializers.CharField(source='get_type_display', read_only=True)
    actor_name = serializers.CharField(source='actor.username', read_only=True, default=None)
    step_name = serializers.CharField(source='step.name', read_only=True, default=None)

    class Meta:
        model = ApprovalEvent
        fields = [
            'id', 'type', 'type_display', 'detail',
            'actor', 'actor_name', 'step', 'step_name', 'created_at'
        ]


class StockOutDetailSerializer(StockOutSerializer):
    """申请详情：解释路线快照、各节点签署状态与当前为何需要这些签署"""
    steps = ApprovalStepSerializer(many=True, read_only=True)
    events = ApprovalEventSerializer(many=True, read_only=True)
    route_explanation = serializers.SerializerMethodField()
    route_snapshot = serializers.JSONField(read_only=True)

    class Meta(StockOutSerializer.Meta):
        fields = StockOutSerializer.Meta.fields + [
            'steps', 'events', 'route_explanation', 'route_snapshot',
        ]

    def get_route_explanation(self, obj):
        snapshot = obj.route_snapshot or {}
        matched = snapshot.get('matched_steps', [])
        step_lines = []
        for index, spec in enumerate(matched, start=1):
            facts = spec.get('facts') or {}
            fact_text = ''
            if facts:
                labels = {
                    'risk_level': None,  # 用 choices 映射
                    'quantity': '数量',
                    'receiver_dept': '领用部门',
                    'purpose_type': '用途',
                }
                parts = []
                risk_map = dict(Goods.RISK_CHOICES)
                purpose_map = dict(StockOut.PURPOSE_CHOICES)
                for key, value in facts.items():
                    if key == 'risk_level':
                        parts.append(f'风险等级={risk_map.get(value, value)}')
                    elif key == 'purpose_type':
                        parts.append(f'用途={purpose_map.get(value, value)}')
                    else:
                        parts.append(f'{labels.get(key, key)}={value}')
                fact_text = f'（命中：{"，".join(parts)}）'
            step_lines.append(f'{index}. {spec["name"]}：{spec.get("reason", "")}{fact_text}')

        return {
            'rule_version': snapshot.get('rule_version'),
            'summary': (
                f'本申请按审批规则 v{snapshot.get("rule_version")} 冻结路线，'
                f'依据创建时的风险等级、数量、领用部门与用途评估出 {len(matched)} 个签署节点；'
                '规则后续调整不影响本申请。'
            ),
            'why_these_signatures': step_lines,
            'current_requirement': approval_engine.current_requirement(obj),
        }


class ApprovalRuleVersionSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)

    class Meta:
        model = ApprovalRuleVersion
        fields = [
            'id', 'version', 'status', 'status_display', 'nodes',
            'remark', 'created_by', 'created_by_name',
            'published_at', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'version', 'status', 'published_at', 'created_at', 'updated_at']


class RuleVersionPublishSerializer(serializers.Serializer):
    nodes = serializers.ListField(child=serializers.DictField(), required=True)
    remark = serializers.CharField(max_length=200, required=False, allow_blank=True, default='')


class RoleAssignmentSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source='user.real_name', read_only=True)
    username = serializers.CharField(source='user.username', read_only=True)
    role_name = serializers.SerializerMethodField()

    class Meta:
        model = ApprovalRoleAssignment
        fields = [
            'id', 'role_code', 'role_name', 'scope_dept',
            'user', 'user_name', 'username', 'is_active',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_role_name(self, obj):
        return approval_engine.ROLE_NAMES.get(obj.role_code, obj.role_code)

    def validate_role_code(self, value):
        if value not in approval_engine.ROLE_NAMES:
            raise serializers.ValidationError('审批角色非法')
        return value

    def validate_user(self, value):
        if not value.is_active:
            raise serializers.ValidationError('用户已停用')
        return value

    def validate(self, data):
        qs = ApprovalRoleAssignment.objects.filter(
            role_code=data['role_code'],
            scope_dept=data.get('scope_dept', ''),
            user=data['user'],
        )
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError('该审批人已在此角色/部门范围内')
        return data


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


class SignActionSerializer(serializers.Serializer):
    """签署动作"""
    action = serializers.ChoiceField(choices=[('approve', '通过'), ('reject', '拒绝')], required=True)
    comment = serializers.CharField(required=False, allow_blank=True, default='')


class RecuseSerializer(serializers.Serializer):
    comment = serializers.CharField(required=False, allow_blank=True, default='')


class DelegateSerializer(serializers.Serializer):
    user = serializers.IntegerField(required=True, error_messages={'required': '请选择改派人员'})
    comment = serializers.CharField(required=False, allow_blank=True, default='')

    def validate_user(self, value):
        from apps.authentication.models import User
        try:
            return User.objects.get(pk=value)
        except User.DoesNotExist:
            raise serializers.ValidationError('用户不存在')
