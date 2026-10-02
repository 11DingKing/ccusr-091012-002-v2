"""
库房管理模型
"""
from django.db import models
from apps.authentication.models import User


class Unit(models.Model):
    """单位模型"""
    name = models.CharField('单位名称', max_length=5, unique=True)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='created_units', verbose_name='创建人'
    )
    is_active = models.BooleanField('是否启用', default=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)
    
    class Meta:
        db_table = 'wh_unit'
        verbose_name = '单位'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
    
    def __str__(self):
        return self.name
    
    @property
    def is_linked(self):
        """是否已关联至品类"""
        return self.categories.exists()


class Category(models.Model):
    """品类模型"""
    name = models.CharField('品类名称', max_length=10, unique=True)
    unit = models.ForeignKey(
        Unit, on_delete=models.PROTECT,
        related_name='categories', verbose_name='单位'
    )
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='created_categories', verbose_name='创建人'
    )
    is_active = models.BooleanField('是否启用', default=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)
    
    class Meta:
        db_table = 'wh_category'
        verbose_name = '品类'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
    
    def __str__(self):
        return self.name
    
    @property
    def is_linked(self):
        """是否已关联至品种"""
        return self.varieties.exists()


class Variety(models.Model):
    """品种模型"""
    name = models.CharField('品种名称', max_length=20)
    category = models.ForeignKey(
        Category, on_delete=models.PROTECT,
        related_name='varieties', verbose_name='所属品类'
    )
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='created_varieties', verbose_name='创建人'
    )
    is_active = models.BooleanField('是否启用', default=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)
    
    class Meta:
        db_table = 'wh_variety'
        verbose_name = '品种'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
        unique_together = ['category', 'name']
    
    def __str__(self):
        return f"{self.category.name} - {self.name}"
    
    @property
    def is_in_stock(self):
        """是否已入库"""
        return self.goods.exists()
    
    @property
    def unit_name(self):
        """获取单位名称"""
        return self.category.unit.name if self.category and self.category.unit else ''


class Goods(models.Model):
    """货物模型"""
    RISK_NORMAL = 'normal'
    RISK_ELEVATED = 'elevated'
    RISK_HIGH = 'high'
    RISK_CHOICES = [
        (RISK_NORMAL, '普通物资'),
        (RISK_ELEVATED, '较高风险'),
        (RISK_HIGH, '高风险物资'),
    ]

    variety = models.ForeignKey(
        Variety, on_delete=models.CASCADE,
        related_name='goods', verbose_name='所属品种'
    )
    name = models.CharField('货物名称', max_length=200)
    code = models.CharField('货物编码', max_length=50, unique=True)
    specification = models.CharField('规格型号', max_length=200, blank=True)
    quantity = models.DecimalField('库存数量', max_digits=12, decimal_places=2, default=0)
    warning_threshold = models.DecimalField('预警阈值', max_digits=12, decimal_places=2, default=10)
    risk_level = models.CharField(
        '风险等级', max_length=20, choices=RISK_CHOICES, default=RISK_NORMAL
    )
    location = models.CharField('存放位置', max_length=100, blank=True)
    remark = models.TextField('备注', blank=True)
    is_active = models.BooleanField('是否启用', default=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)
    
    class Meta:
        db_table = 'wh_goods'
        verbose_name = '货物'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
    
    def __str__(self):
        return self.name
    
    @property
    def is_warning(self):
        """是否预警"""
        return self.quantity <= self.warning_threshold


class StockIn(models.Model):
    """入库记录模型"""
    goods = models.ForeignKey(
        Goods, on_delete=models.CASCADE,
        related_name='stock_ins', verbose_name='货物'
    )
    operator = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='stock_in_operations', verbose_name='操作人'
    )
    quantity = models.DecimalField('入库数量', max_digits=12, decimal_places=2)
    batch_no = models.CharField('批次号', max_length=50, blank=True)
    supplier = models.CharField('供应商', max_length=200, blank=True)
    stock_in_time = models.DateTimeField('入库时间', auto_now_add=True)
    remark = models.TextField('备注', blank=True)
    
    class Meta:
        db_table = 'wh_stock_in'
        verbose_name = '入库记录'
        verbose_name_plural = verbose_name
        ordering = ['-stock_in_time']
    
    def __str__(self):
        return f"{self.goods.name} - {self.quantity}"


class ApprovalRuleVersion(models.Model):
    """审批规则版本：发布后不可变；在途申请永久引用其创建时的版本"""
    STATUS_DRAFT = 'draft'
    STATUS_ACTIVE = 'active'
    STATUS_ARCHIVED = 'archived'
    STATUS_CHOICES = [
        (STATUS_DRAFT, '草稿'),
        (STATUS_ACTIVE, '生效中'),
        (STATUS_ARCHIVED, '已归档'),
    ]

    version = models.PositiveIntegerField('版本号', unique=True)
    status = models.CharField('状态', max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    remark = models.CharField('版本说明', max_length=200, blank=True)
    published_at = models.DateTimeField('发布时间', null=True, blank=True)
    published_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='published_rule_versions', verbose_name='发布人'
    )
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='created_rule_versions', verbose_name='创建人'
    )
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'wh_approval_rule_version'
        verbose_name = '审批规则版本'
        verbose_name_plural = verbose_name
        ordering = ['-version']

    def __str__(self):
        return f"审批规则v{self.version}（{self.get_status_display()}）"


class ApprovalRoute(models.Model):
    """审批路线：在版本内按优先级匹配物资风险、数量、领用部门与用途"""
    RISK_MATCH_CHOICES = Goods.RISK_CHOICES

    version = models.ForeignKey(
        ApprovalRuleVersion, on_delete=models.CASCADE,
        related_name='routes', verbose_name='所属版本'
    )
    name = models.CharField('路线名称', max_length=100)
    risk_level = models.CharField(
        '风险等级', max_length=20, choices=RISK_MATCH_CHOICES,
        blank=True, default='', help_text='为空表示不限风险等级'
    )
    min_quantity = models.DecimalField(
        '数量下限（含）', max_digits=12, decimal_places=2, null=True, blank=True
    )
    max_quantity = models.DecimalField(
        '数量上限（含）', max_digits=12, decimal_places=2, null=True, blank=True
    )
    receiver_dept = models.CharField(
        '领用部门', max_length=100, blank=True, default='',
        help_text='为空表示不限部门'
    )
    purpose = models.CharField(
        '用途', max_length=50, blank=True, default='',
        help_text='为空表示不限用途'
    )
    priority = models.PositiveIntegerField('优先级', default=100, help_text='数值越小越优先')
    is_fallback = models.BooleanField('兜底路线', default=False)
    is_active = models.BooleanField('是否启用', default=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'wh_approval_route'
        verbose_name = '审批路线'
        verbose_name_plural = verbose_name
        ordering = ['priority', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['version'], condition=models.Q(is_fallback=True),
                name='uniq_fallback_route_per_version'
            ),
        ]

    def __str__(self):
        return f"{self.version.version}版-{self.name}"


class ApprovalNode(models.Model):
    """审批节点：隶属于路线，顺序执行；sign_mode 决定单人/或签/会签"""
    SIGN_SINGLE = 'single'
    SIGN_ANY = 'any'
    SIGN_ALL = 'all'
    SIGN_MODE_CHOICES = [
        (SIGN_SINGLE, '单人签署'),
        (SIGN_ANY, '任一签署（或签）'),
        (SIGN_ALL, '多人会签'),
    ]

    route = models.ForeignKey(
        ApprovalRoute, on_delete=models.CASCADE,
        related_name='nodes', verbose_name='所属路线'
    )
    name = models.CharField('节点名称', max_length=100)
    order = models.PositiveIntegerField('节点顺序', default=1)
    sign_mode = models.CharField('签署方式', max_length=20, choices=SIGN_MODE_CHOICES, default=SIGN_SINGLE)
    use_active_admins = models.BooleanField(
        '在职管理员兜底', default=True,
        help_text='未指定审批人或指定人全部回避时，取在职管理员'
    )
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'wh_approval_node'
        verbose_name = '审批节点'
        verbose_name_plural = verbose_name
        ordering = ['order', 'id']
        unique_together = [('route', 'order')]

    def __str__(self):
        return f"{self.route.name}-{self.order}.{self.name}"


class ApprovalNodeApprover(models.Model):
    """节点指定审批人（固定顺序，替补按此顺序取人）"""
    node = models.ForeignKey(
        ApprovalNode, on_delete=models.CASCADE,
        related_name='approvers', verbose_name='所属节点'
    )
    approver = models.ForeignKey(
        User, on_delete=models.PROTECT,
        related_name='node_approver_links', verbose_name='审批人'
    )
    order = models.PositiveIntegerField('顺序', default=1)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)

    class Meta:
        db_table = 'wh_approval_node_approver'
        verbose_name = '节点审批人'
        verbose_name_plural = verbose_name
        ordering = ['order', 'id']
        unique_together = [('node', 'approver')]

    def __str__(self):
        return f"{self.node.name}-{self.approver.username}"


class StockOut(models.Model):
    """出库（领用）申请：创建时固化规则版本与路线快照"""
    STATUS_PENDING = 'pending'
    STATUS_APPROVED = 'approved'
    STATUS_REJECTED = 'rejected'
    STATUS_BLOCKED = 'blocked'
    STATUS_COMPLETED = 'completed'
    STATUS_CHOICES = [
        (STATUS_PENDING, '待审批'),
        (STATUS_APPROVED, '已通过'),
        (STATUS_REJECTED, '已拒绝'),
        (STATUS_BLOCKED, '无法流转'),
        (STATUS_COMPLETED, '已完成'),
    ]

    PURPOSE_CHOICES = [
        ('case_handling', '办案使用'),
        ('daily_duty', '日常执勤'),
        ('training', '训练演练'),
        ('storage', '入库保管'),
        ('scrap', '报废处置'),
        ('other', '其他'),
    ]

    goods = models.ForeignKey(
        Goods, on_delete=models.CASCADE,
        related_name='stock_outs', verbose_name='货物'
    )
    operator = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='stock_out_operations', verbose_name='申请人'
    )
    receiver = models.CharField('领用人', max_length=100)
    receiver_dept = models.CharField('领用部门', max_length=100, blank=True)
    purpose = models.CharField('用途', max_length=50, choices=PURPOSE_CHOICES, blank=True, default='')
    quantity = models.DecimalField('出库数量', max_digits=12, decimal_places=2)
    status = models.CharField('状态', max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    rule_version = models.ForeignKey(
        ApprovalRuleVersion, on_delete=models.PROTECT, null=True, blank=True,
        related_name='stock_outs', verbose_name='规则版本'
    )
    route_snapshot = models.JSONField('审批路线快照', default=dict, blank=True)
    current_node_order = models.PositiveIntegerField('当前节点顺序', null=True, blank=True)
    resubmitted_from = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='resubmissions', verbose_name='重提自申请'
    )
    route_generation = models.PositiveIntegerField(
        '路线代次', default=1,
        help_text='条件变化重评且换路线时递增；签署任务按代次归属'
    )
    stock_out_time = models.DateTimeField('出库时间', null=True, blank=True)
    remark = models.TextField('备注', blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'wh_stock_out'
        verbose_name = '出库记录'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.goods.name} - {self.quantity}"


class Warning(models.Model):
    """预警记录模型"""
    TYPE_CHOICES = [
        ('low_stock', '库存不足'),
        ('expiring', '即将过期'),
        ('expired', '已过期'),
    ]
    
    goods = models.ForeignKey(
        Goods, on_delete=models.CASCADE,
        related_name='warnings', verbose_name='货物'
    )
    type = models.CharField('预警类型', max_length=20, choices=TYPE_CHOICES)
    message = models.TextField('预警信息')
    is_read = models.BooleanField('是否已读', default=False)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    
    class Meta:
        db_table = 'wh_warning'
        verbose_name = '预警记录'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.goods.name} - {self.get_type_display()}"


class ApprovalRouteEvaluation(models.Model):
    """路线选定/重新评估记录：保存条件快照与命中原因，供申请详情解释"""
    TYPE_INITIAL = 'initial'
    TYPE_RESUBMIT = 'resubmit'
    TYPE_REEVALUATE = 'reevaluate'
    TYPE_CHOICES = [
        (TYPE_INITIAL, '创建选定'),
        (TYPE_RESUBMIT, '拒绝后重提'),
        (TYPE_REEVALUATE, '条件变化重评'),
    ]

    stock_out = models.ForeignKey(
        StockOut, on_delete=models.CASCADE,
        related_name='evaluations', verbose_name='出库申请'
    )
    type = models.CharField('评估类型', max_length=20, choices=TYPE_CHOICES, default=TYPE_INITIAL)
    rule_version = models.ForeignKey(
        ApprovalRuleVersion, on_delete=models.PROTECT,
        related_name='evaluations', verbose_name='规则版本'
    )
    route = models.ForeignKey(
        ApprovalRoute, on_delete=models.PROTECT,
        related_name='evaluations', verbose_name='命中路线'
    )
    conditions = models.JSONField('申请条件快照', default=dict)
    matched_reasons = models.JSONField('命中原因', default=list)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='route_evaluations', verbose_name='操作人'
    )
    created_at = models.DateTimeField('创建时间', auto_now_add=True)

    class Meta:
        db_table = 'wh_approval_route_evaluation'
        verbose_name = '审批路线评估记录'
        verbose_name_plural = verbose_name
        ordering = ['created_at']

    def __str__(self):
        return f"{self.stock_out_id}-{self.get_type_display()}-{self.route.name}"


class ApprovalTask(models.Model):
    """审批签署任务：每个审批人在每个节点上的确定性状态"""
    STATUS_WAITING = 'waiting'
    STATUS_PENDING = 'pending'
    STATUS_APPROVED = 'approved'
    STATUS_REJECTED = 'rejected'
    STATUS_RECUSED = 'recused'
    STATUS_CANCELLED = 'cancelled'
    STATUS_CHOICES = [
        (STATUS_WAITING, '待激活'),
        (STATUS_PENDING, '待签署'),
        (STATUS_APPROVED, '已同意'),
        (STATUS_REJECTED, '已拒绝'),
        (STATUS_RECUSED, '已回避'),
        (STATUS_CANCELLED, '已取消'),
    ]
    ACTIVE_STATUSES = [STATUS_WAITING, STATUS_PENDING]
    FINAL_STATUSES = [STATUS_APPROVED, STATUS_REJECTED, STATUS_RECUSED, STATUS_CANCELLED]

    stock_out = models.ForeignKey(
        StockOut, on_delete=models.CASCADE,
        related_name='approval_tasks', verbose_name='出库申请'
    )
    generation = models.PositiveIntegerField('路线代次', default=1)
    node_order = models.PositiveIntegerField('节点顺序')
    node_name = models.CharField('节点名称（快照）', max_length=100)
    sign_mode = models.CharField('签署方式（快照）', max_length=20, default=ApprovalNode.SIGN_SINGLE)
    approver = models.ForeignKey(
        User, on_delete=models.PROTECT,
        related_name='approval_tasks', verbose_name='审批人'
    )
    status = models.CharField('任务状态', max_length=20, choices=STATUS_CHOICES, default=STATUS_WAITING)
    remark = models.TextField('审批意见', blank=True)
    acted_at = models.DateTimeField('签署时间', null=True, blank=True)
    replacement_for = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='replacements', verbose_name='替补的任务'
    )
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'wh_approval_task'
        verbose_name = '审批签署任务'
        verbose_name_plural = verbose_name
        ordering = ['generation', 'node_order', 'id']
        indexes = [
            models.Index(fields=['stock_out', 'status']),
            models.Index(fields=['approver', 'status']),
        ]

    def __str__(self):
        return f"{self.stock_out_id}-{self.node_order}-{self.approver.username}-{self.status}"
