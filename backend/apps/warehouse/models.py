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
    RISK_CONTROLLED = 'controlled'
    RISK_HIGH = 'high_risk'
    RISK_CHOICES = [
        (RISK_NORMAL, '普通耗材'),
        (RISK_CONTROLLED, '受控物资'),
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


class StockOut(models.Model):
    """出库申请模型"""
    STATUS_DRAFT = 'draft'
    STATUS_PENDING = 'pending'
    STATUS_APPROVED = 'approved'
    STATUS_REJECTED = 'rejected'
    STATUS_CANCELLED = 'cancelled'
    STATUS_COMPLETED = 'completed'
    STATUS_CHOICES = [
        (STATUS_DRAFT, '草稿'),
        (STATUS_PENDING, '审批中'),
        (STATUS_APPROVED, '已通过'),
        (STATUS_REJECTED, '已拒绝'),
        (STATUS_CANCELLED, '已撤销'),
        (STATUS_COMPLETED, '已完成'),
    ]

    PURPOSE_USE = 'use'
    PURPOSE_TRANSFER = 'transfer'
    PURPOSE_DESTRUCTION = 'destruction'
    PURPOSE_INSPECTION = 'inspection'
    PURPOSE_OTHER = 'other'
    PURPOSE_CHOICES = [
        (PURPOSE_USE, '执勤使用'),
        (PURPOSE_TRANSFER, '调拨'),
        (PURPOSE_DESTRUCTION, '销毁'),
        (PURPOSE_INSPECTION, '检验鉴定'),
        (PURPOSE_OTHER, '其他'),
    ]

    goods = models.ForeignKey(
        Goods, on_delete=models.PROTECT,
        related_name='stock_outs', verbose_name='货物'
    )
    operator = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='stock_out_applications', verbose_name='申请人'
    )
    receiver = models.CharField('领用人', max_length=100)
    receiver_dept = models.CharField('领用部门', max_length=100, blank=True)
    quantity = models.DecimalField('出库数量', max_digits=12, decimal_places=2)
    purpose_type = models.CharField(
        '用途类型', max_length=20, choices=PURPOSE_CHOICES, default=PURPOSE_USE
    )
    purpose = models.TextField('用途说明', blank=True)
    status = models.CharField('状态', max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    stock_out_time = models.DateTimeField('出库时间', null=True, blank=True)
    remark = models.TextField('备注', blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)

    # ===== 版本化审批路线 =====
    rule_version = models.ForeignKey(
        'ApprovalRuleVersion', on_delete=models.PROTECT,
        null=True, related_name='stock_outs', verbose_name='规则版本'
    )
    route_snapshot = models.JSONField('审批路线快照', default=dict, blank=True)
    # 申请创建时刻的风险/条件快照（后续规则调整不影响在办路线）
    risk_level = models.CharField('风险等级快照', max_length=20, blank=True)
    current_step = models.ForeignKey(
        'ApprovalStep', on_delete=models.SET_NULL,
        null=True, related_name='current_stock_outs', verbose_name='当前审批节点'
    )
    # 重提关联：拒绝后重新提交生成新单并递增修订号
    revised_from = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='revisions', verbose_name='修订自'
    )
    revision_no = models.PositiveIntegerField('修订号', default=1)
    # 条件变化重新评估
    reevaluated_from = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='reevaluations', verbose_name='重新评估自'
    )

    class Meta:
        db_table = 'wh_stock_out'
        verbose_name = '出库申请'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.goods.name} - {self.quantity}"

    @property
    def active_steps(self):
        """按顺序返回审批节点"""
        return self.steps.order_by('order_index')

    def can_submit(self):
        """是否允许提交（尚未进入审批）"""
        return self.status in (self.STATUS_DRAFT, self.STATUS_REJECTED, self.STATUS_CANCELLED)


class ApprovalRuleVersion(models.Model):
    """审批规则版本：发布后不可变，新规则发布为新版本"""
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
    # 有序节点定义，见 approval_engine.evaluate 中的结构说明
    nodes = models.JSONField('审批节点定义', default=list)
    remark = models.CharField('版本说明', max_length=200, blank=True)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='approval_rule_versions', verbose_name='创建人'
    )
    published_at = models.DateTimeField('生效时间', null=True, blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'wh_approval_rule_version'
        verbose_name = '审批规则版本'
        verbose_name_plural = verbose_name
        ordering = ['-version']

    def __str__(self):
        return f"审批规则 v{self.version}（{self.get_status_display()}）"


class ApprovalStep(models.Model):
    """审批节点实例：申请创建时按规则快照实例化，不再随规则变化"""
    STATUS_PENDING = 'pending'
    STATUS_ACTIVE = 'active'
    STATUS_APPROVED = 'approved'
    STATUS_REJECTED = 'rejected'
    STATUS_SKIPPED = 'skipped'
    STATUS_BLOCKED = 'blocked'
    STATUS_CHOICES = [
        (STATUS_PENDING, '待激活'),
        (STATUS_ACTIVE, '签署中'),
        (STATUS_APPROVED, '已通过'),
        (STATUS_REJECTED, '已拒绝'),
        (STATUS_SKIPPED, '已跳过'),
        (STATUS_BLOCKED, '已阻塞'),
    ]

    stock_out = models.ForeignKey(
        StockOut, on_delete=models.CASCADE,
        related_name='steps', verbose_name='出库申请'
    )
    order_index = models.PositiveIntegerField('节点顺序')
    name = models.CharField('节点名称', max_length=100)
    role_code = models.CharField('审批角色', max_length=20)
    # 会签策略：all=全员通过, any=任一通过, quorum=N 人通过
    sign_policy = models.CharField('签署策略', max_length=20, default='all')
    quorum = models.PositiveIntegerField('通过人数门槛', null=True, blank=True)
    # 该节点为何存在（命中条件的人类可读说明）
    reason = models.TextField('触发原因', blank=True)
    status = models.CharField('节点状态', max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    activated_at = models.DateTimeField('激活时间', null=True, blank=True)
    finished_at = models.DateTimeField('完成时间', null=True, blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)

    class Meta:
        db_table = 'wh_approval_step'
        verbose_name = '审批节点'
        verbose_name_plural = verbose_name
        ordering = ['stock_out', 'order_index']
        unique_together = ['stock_out', 'order_index']

    def __str__(self):
        return f"{self.stock_out_id}-{self.order_index}.{self.name}"

    @property
    def decided_signers(self):
        return self.signers.exclude(status=StepSigner.STATUS_PENDING)

    def is_satisfied(self):
        """当前签署结果是否满足通过策略"""
        approved = self.signers.filter(status=StepSigner.STATUS_APPROVED).count()
        if self.sign_policy == 'any':
            return approved >= 1
        if self.sign_policy == 'quorum':
            return approved >= (self.quorum or 1)
        total = self.signers.count()
        return total > 0 and approved == total


class StepSigner(models.Model):
    """节点签署人：多人并发签署，每人独立状态，行级锁保证确定结果"""
    STATUS_PENDING = 'pending'
    STATUS_APPROVED = 'approved'
    STATUS_REJECTED = 'rejected'
    STATUS_RECUSED = 'recused'
    STATUS_SKIPPED = 'skipped'
    STATUS_CHOICES = [
        (STATUS_PENDING, '待签署'),
        (STATUS_APPROVED, '已通过'),
        (STATUS_REJECTED, '已拒绝'),
        (STATUS_RECUSED, '已回避'),
        (STATUS_SKIPPED, '无需签署'),
    ]

    step = models.ForeignKey(
        ApprovalStep, on_delete=models.CASCADE,
        related_name='signers', verbose_name='审批节点'
    )
    user = models.ForeignKey(
        User, on_delete=models.PROTECT,
        related_name='approval_signatures', verbose_name='签署人'
    )
    status = models.CharField('签署状态', max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    # normal=正常指派; auto_recused=创建时自动回避; delegated=改派
    source = models.CharField('指派来源', max_length=20, default='normal')
    comment = models.TextField('签署意见', blank=True)
    signed_at = models.DateTimeField('签署时间', null=True, blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)

    class Meta:
        db_table = 'wh_step_signer'
        verbose_name = '节点签署人'
        verbose_name_plural = verbose_name
        ordering = ['id']
        unique_together = ['step', 'user']

    def __str__(self):
        return f"{self.step}-{self.user}:{self.get_status_display()}"


class ApprovalEvent(models.Model):
    """审批事件流：申请详情据此解释每个签署要求的来龙去脉"""
    TYPE_SUBMIT = 'submit'
    TYPE_APPROVE = 'approve'
    TYPE_REJECT = 'reject'
    TYPE_RECUSE = 'recuse'
    TYPE_AUTO_RECUSE = 'auto_recuse'
    TYPE_DELEGATE = 'delegate'
    TYPE_STEP_ACTIVATE = 'step_activate'
    TYPE_STEP_SKIP = 'step_skip'
    TYPE_STEP_BLOCK = 'step_block'
    TYPE_RESUBMIT = 'resubmit'
    TYPE_REEVALUATE = 'reevaluate'
    TYPE_COMPLETE = 'complete'
    TYPE_CANCEL = 'cancel'
    TYPE_CHOICES = [
        (TYPE_SUBMIT, '提交申请'),
        (TYPE_APPROVE, '通过'),
        (TYPE_REJECT, '拒绝'),
        (TYPE_RECUSE, '主动回避'),
        (TYPE_AUTO_RECUSE, '自动回避'),
        (TYPE_DELEGATE, '改派'),
        (TYPE_STEP_ACTIVATE, '节点激活'),
        (TYPE_STEP_SKIP, '节点跳过'),
        (TYPE_STEP_BLOCK, '节点阻塞'),
        (TYPE_RESUBMIT, '拒绝后重提'),
        (TYPE_REEVALUATE, '条件变化重新评估'),
        (TYPE_COMPLETE, '审批完成'),
        (TYPE_CANCEL, '撤销申请'),
    ]

    stock_out = models.ForeignKey(
        StockOut, on_delete=models.CASCADE,
        related_name='events', verbose_name='出库申请'
    )
    step = models.ForeignKey(
        ApprovalStep, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='events', verbose_name='审批节点'
    )
    actor = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='approval_events', verbose_name='操作人'
    )
    type = models.CharField('事件类型', max_length=20, choices=TYPE_CHOICES)
    detail = models.TextField('事件说明', blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)

    class Meta:
        db_table = 'wh_approval_event'
        verbose_name = '审批事件'
        verbose_name_plural = verbose_name
        ordering = ['created_at', 'id']

    def __str__(self):
        return f"{self.stock_out_id}-{self.get_type_display()}"


class ApprovalRoleAssignment(models.Model):
    """审批角色指派：某用户在某部门范围内承担某审批角色（scope_dept 为空表示全局）"""
    role_code = models.CharField('审批角色编码', max_length=20)
    scope_dept = models.CharField('适用领用部门', max_length=100, blank=True, default='')
    user = models.ForeignKey(
        User, on_delete=models.CASCADE,
        related_name='approval_role_assignments', verbose_name='审批人'
    )
    is_active = models.BooleanField('是否在岗', default=True)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='created_role_assignments', verbose_name='设置人'
    )
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'wh_approval_role_assignment'
        verbose_name = '审批角色指派'
        verbose_name_plural = verbose_name
        ordering = ['role_code', 'scope_dept', 'id']
        unique_together = ['role_code', 'scope_dept', 'user']

    def __str__(self):
        scope = self.scope_dept or '全局'
        return f"{self.role_code}@{scope}:{self.user}"


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
