"""
版本化审批引擎

职责：
1. 依据物资风险、数量、领用部门、用途评估审批节点（evaluate）；
2. 规则以 ApprovalRuleVersion 形式版本化，发布即不可变，旧版本归档；
3. 申请创建时把「规则定义 + 角色人员映射 + 评估输入」整体冻结为快照并实例化节点，
   后续规则调整、人员变动均不影响在办路线；
4. 提供签署、回避、改派、拒绝后重提、条件变化重新评估等确定性状态流转。

节点定义（JSON）结构：
{
    "code": "safety_officer",        # 节点编码
    "name": "安全负责人会签",
    "role": "safety_officer",       # 审批角色编码，见 ROLE_CODES
    "policy": "all",                # all=全员通过 / any=任一通过 / quorum=达到 quorum 人数
    "quorum": null,                 # policy=quorum 时的通过人数
    "conditions": [ {...}, {...} ], # 触发条件，组内 AND；省略或空数组表示恒触发
    "conditions_any": [ [ {...} ], [ {...} ] ],  # 多组 OR，每组内 AND；与 conditions 二选一
    "reason": "为何需要该节点签署的人类可读说明"
}

条件算子：eq / ne / in / not_in / gte / gt / lte / lt
字段：risk_level、quantity、receiver_dept、purpose_type
"""
from decimal import Decimal, InvalidOperation
import time

from django.db import OperationalError, transaction
from django.utils import timezone

from apps.authentication.models import User
from .models import (
    ApprovalRuleVersion, ApprovalStep, StepSigner, ApprovalEvent,
    StockOut,
)


_LOCK_MARKERS = ('database is locked', 'database table is locked')


def with_lock_retry(max_attempts=5):
    """
    SQLite 写锁冲突时整体重试（事务回滚后从头再执行，幂等安全：
    所有状态推进均由 CAS 条件保护）。
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            # 已在外层事务中时不重试，由最外层被装饰函数统一重试
            if not transaction.get_autocommit():
                return func(*args, **kwargs)
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except OperationalError as exc:
                    if attempt == max_attempts - 1 or \
                            not any(marker in str(exc) for marker in _LOCK_MARKERS):
                        raise
                    time.sleep(0.02 * (attempt + 1))
        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        return wrapper
    return decorator


# ==================== 角色与默认规则 ====================

ROLE_KEEPER = 'keeper'
ROLE_DEPT_HEAD = 'dept_head'
ROLE_SAFETY_OFFICER = 'safety_officer'
ROLE_DIRECTOR = 'director'

ROLE_CHOICES = [
    (ROLE_KEEPER, '保管员'),
    (ROLE_DEPT_HEAD, '领用部门负责人'),
    (ROLE_SAFETY_OFFICER, '安全负责人'),
    (ROLE_DIRECTOR, '分管领导'),
]
ROLE_NAMES = dict(ROLE_CHOICES)

POLICY_NAMES = {
    'all': '全员会签',
    'any': '任一签署',
    'quorum': '人数门槛',
}

DEFAULT_NODES = [
    {
        'code': 'keeper',
        'name': '保管员核准',
        'role': ROLE_KEEPER,
        'policy': 'all',
        'conditions': [],
        'reason': '所有出库申请均需保管员核对物资台账与领用信息。',
    },
    {
        'code': 'dept_head',
        'name': '领用部门负责人审批',
        'role': ROLE_DEPT_HEAD,
        'policy': 'all',
        'conditions_any': [
            [{'field': 'risk_level', 'op': 'in', 'value': ['controlled', 'high_risk']}],
            [{'field': 'quantity', 'op': 'gte', 'value': 10}],
            [{'field': 'purpose_type', 'op': 'eq', 'value': 'destruction'}],
        ],
        'reason': '受控/高风险物资、申领数量 10 件及以上，或用途为销毁的，须经领用部门负责人复核。',
    },
    {
        'code': 'safety_officer',
        'name': '安全负责人会签',
        'role': ROLE_SAFETY_OFFICER,
        'policy': 'all',
        'conditions_any': [
            [{'field': 'risk_level', 'op': 'eq', 'value': 'high_risk'}],
            [{'field': 'purpose_type', 'op': 'eq', 'value': 'destruction'}],
        ],
        'reason': '高风险物资放行或销毁处置须经安全负责人复核，在岗安全负责人全员会签。',
    },
    {
        'code': 'director',
        'name': '分管领导终审',
        'role': ROLE_DIRECTOR,
        'policy': 'all',
        'conditions_any': [
            [
                {'field': 'risk_level', 'op': 'eq', 'value': 'high_risk'},
                {'field': 'quantity', 'op': 'gte', 'value': 10},
            ],
            [
                {'field': 'risk_level', 'op': 'eq', 'value': 'high_risk'},
                {'field': 'purpose_type', 'op': 'eq', 'value': 'destruction'},
            ],
        ],
        'reason': '高风险物资大批量（10 件及以上）放行或销毁处置，须由分管领导终审。',
    },
]


class ApprovalConfigError(Exception):
    """规则/审批人配置错误（阻止提交）"""


class ApprovalStateError(Exception):
    """申请当前状态不允许该操作"""


# ==================== 条件评估 ====================

_CONDITION_FIELDS = {'risk_level', 'quantity', 'receiver_dept', 'purpose_type'}
_CONDITION_OPS = {'eq', 'ne', 'in', 'not_in', 'gte', 'gt', 'lte', 'lt'}


def _coerce_number(value):
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _match_condition(condition, context):
    field = condition.get('field')
    op = condition.get('op')
    expected = condition.get('value')
    if field not in _CONDITION_FIELDS or op not in _CONDITION_OPS:
        raise ApprovalConfigError(f'规则条件非法：{condition}')

    actual = context.get(field)
    if field == 'quantity':
        actual = _coerce_number(actual)
        expected = _coerce_number(expected)

    if op == 'eq':
        return actual == expected
    if op == 'ne':
        return actual != expected
    if op == 'in':
        return actual in (expected or [])
    if op == 'not_in':
        return actual not in (expected or [])
    if actual is None or expected is None:
        return False
    if op == 'gte':
        return actual >= expected
    if op == 'gt':
        return actual > expected
    if op == 'lte':
        return actual <= expected
    if op == 'lt':
        return actual < expected
    return False


def _node_condition_groups(node):
    """返回 OR 组列表，每组为 AND 条件列表"""
    if 'conditions_any' in node:
        groups = node.get('conditions_any') or []
        if not isinstance(groups, list):
            raise ApprovalConfigError('conditions_any 必须是条件组数组')
        return groups
    return [node.get('conditions') or []]


def evaluate(nodes, context):
    """
    按定义顺序评估命中的节点。
    返回 [{...node, 'facts': 命中组的实际取值}], 未命中的节点不实例化。
    """
    matched = []
    for node in nodes:
        groups = _node_condition_groups(node)
        hit_group = None
        for group in groups:
            if all(_match_condition(c, context) for c in group):
                hit_group = group
                break
        if hit_group is None:
            continue
        facts = {c['field']: _json_safe(context.get(c['field'])) for c in hit_group}
        matched.append({**node, 'facts': facts})
    return matched


def validate_nodes(nodes):
    """发布前校验规则定义，非法直接抛 ApprovalConfigError"""
    if not isinstance(nodes, list) or not nodes:
        raise ApprovalConfigError('审批节点不能为空')
    seen_codes = set()
    for i, node in enumerate(nodes):
        prefix = f'第 {i + 1} 个节点'
        for key in ('code', 'name', 'role'):
            if not node.get(key):
                raise ApprovalConfigError(f'{prefix}缺少 {key}')
        if node['code'] in seen_codes:
            raise ApprovalConfigError(f'节点编码重复：{node["code"]}')
        seen_codes.add(node['code'])
        if node['role'] not in ROLE_NAMES:
            raise ApprovalConfigError(f'{prefix}审批角色非法：{node["role"]}')
        policy = node.get('policy', 'all')
        if policy not in POLICY_NAMES:
            raise ApprovalConfigError(f'{prefix}签署策略非法：{policy}')
        if policy == 'quorum':
            quorum = node.get('quorum')
            if not isinstance(quorum, int) or quorum < 1:
                raise ApprovalConfigError(f'{prefix}quorum 策略必须指定不小于 1 的 quorum')
        # 触碰一次条件以校验结构
        for group in _node_condition_groups(node):
            for condition in group:
                if condition.get('field') not in _CONDITION_FIELDS:
                    raise ApprovalConfigError(f'{prefix}条件字段非法：{condition}')
                if condition.get('op') not in _CONDITION_OPS:
                    raise ApprovalConfigError(f'{prefix}条件算子非法：{condition}')
    return nodes


def _json_safe(value):
    if isinstance(value, Decimal):
        return str(value)
    return value


# ==================== 规则版本 ====================

@with_lock_retry()
def publish_version(nodes, remark='', created_by=None):
    """发布新版本：校验、归档当前生效版本、新版本置为生效。"""
    validate_nodes(nodes)
    with transaction.atomic():
        ApprovalRuleVersion.objects.select_for_update().filter(
            status=ApprovalRuleVersion.STATUS_ACTIVE
        ).update(status=ApprovalRuleVersion.STATUS_ARCHIVED)
        last = ApprovalRuleVersion.objects.select_for_update().order_by('-version').first()
        version = (last.version + 1) if last else 1
        rule = ApprovalRuleVersion.objects.create(
            version=version,
            status=ApprovalRuleVersion.STATUS_ACTIVE,
            nodes=nodes,
            remark=remark,
            created_by=created_by,
            published_at=timezone.now(),
        )
    return rule


def get_active_version():
    return ApprovalRuleVersion.objects.filter(status=ApprovalRuleVersion.STATUS_ACTIVE).first()


# ==================== 审批人解析 ====================

def resolve_role_users(role_code, receiver_dept=''):
    """
    解析某角色当前的在岗审批人。
    - dept_head：优先匹配领用部门的指派，其次全局指派；
    - keeper：无显式指派时回退到系统管理员（role=admin）；
    - 其他角色：仅取显式指派。
    返回去重、保持稳定顺序的用户列表。
    """
    qs = _assignment_model().objects
    assignments = []
    if role_code == ROLE_DEPT_HEAD and receiver_dept:
        assignments = list(
            qs.filter(
                role_code=role_code, scope_dept=receiver_dept, is_active=True,
                user__is_active=True,
            ).select_related('user').order_by('id')
        )
    if not assignments:
        assignments = list(
            qs.filter(
                role_code=role_code, scope_dept='', is_active=True,
                user__is_active=True,
            ).select_related('user').order_by('id')
        )

    users = [a.user for a in assignments]
    if not users and role_code == ROLE_KEEPER:
        users = list(User.objects.filter(role='admin', is_active=True).order_by('id'))

    # 去重（同一用户可能多条指派）
    deduped, seen = [], set()
    for user in users:
        if user.id not in seen:
            seen.add(user.id)
            deduped.append(user)
    return deduped


def _assignment_model():
    from .models import ApprovalRoleAssignment
    return ApprovalRoleAssignment


def _snapshot_role_members(matched_steps, context):
    members = {}
    for step in matched_steps:
        role = step['role']
        if role in members:
            continue
        users = resolve_role_users(role, context.get('receiver_dept', ''))
        members[role] = [
            {'id': u.id, 'username': u.username, 'real_name': u.real_name or u.username}
            for u in users
        ]
    return members


def build_context(stock_out):
    goods = stock_out.goods
    return {
        'risk_level': goods.risk_level,
        'quantity': stock_out.quantity,
        'receiver_dept': stock_out.receiver_dept or '',
        'purpose_type': stock_out.purpose_type,
        'goods_name': goods.name,
        'applicant_id': stock_out.operator_id,
        'applicant_name': stock_out.operator.username if stock_out.operator else '',
    }


# ==================== 路线实例化（创建时快照） ====================

def _record_event(stock_out, event_type, detail='', actor=None, step=None):
    return ApprovalEvent.objects.create(
        stock_out=stock_out, type=event_type, detail=detail,
        actor=actor, step=step,
    )


@with_lock_retry()
def instantiate_route(stock_out, actor=None):
    """
    依据当前生效版本评估并实例化审批路线，冻结快照。
    自管事务；在已有事务中调用时作为保存点嵌套。
    """
    with transaction.atomic():
        return _instantiate_route(stock_out, actor=actor)


def _instantiate_route(stock_out, actor=None):
    if stock_out.pk is None:
        stock_out.save()
    rule = get_active_version()
    if rule is None:
        raise ApprovalConfigError('尚未发布任何审批规则，无法提交申请')

    context = build_context(stock_out)
    matched = evaluate(rule.nodes, context)
    if not matched:
        raise ApprovalConfigError('当前规则未匹配到任何审批节点，请检查规则配置')

    role_members = _snapshot_role_members(matched, context)
    # 未配置审批人的角色直接阻止提交（区别于“唯一审批人就是申请人”的回避后阻塞）
    missing = [
        ROLE_NAMES.get(step['role'], step['role'])
        for step in matched if not role_members.get(step['role'])
    ]
    if missing:
        raise ApprovalConfigError(f"以下审批角色尚未配置在岗审批人：{'、'.join(dict.fromkeys(missing))}")

    snapshot = {
        'rule_version': rule.version,
        'rule_version_id': rule.id,
        'published_at': rule.published_at.isoformat() if rule.published_at else None,
        'nodes': rule.nodes,
        'matched_steps': [
            {'code': s['code'], 'name': s['name'], 'role': s['role'],
             'policy': s.get('policy', 'all'), 'quorum': s.get('quorum'),
             'reason': s.get('reason', ''), 'facts': s.get('facts', {})}
            for s in matched
        ],
        'inputs': {k: _json_safe(v) for k, v in context.items()},
        'role_members': role_members,
    }

    steps = []
    applicant_id = context['applicant_id']
    for index, spec in enumerate(matched, start=1):
        step = ApprovalStep.objects.create(
            stock_out=stock_out,
            order_index=index,
            name=spec['name'],
            role_code=spec['role'],
            sign_policy=spec.get('policy', 'all'),
            quorum=spec.get('quorum'),
            reason=spec.get('reason', ''),
        )
        users = resolve_role_users(spec['role'], context['receiver_dept'])
        for user in users:
            is_applicant = user.id == applicant_id
            StepSigner.objects.create(
                step=step, user=user,
                status=StepSigner.STATUS_RECUSED if is_applicant else StepSigner.STATUS_PENDING,
                source='auto_recused' if is_applicant else 'normal',
            )
            if is_applicant:
                _record_event(
                    stock_out, ApprovalEvent.TYPE_AUTO_RECUSE,
                    detail=f'{spec["name"]}：申请人与审批人为同一人（{user.username}），系统自动回避',
                    actor=user, step=step,
                )
        if not _step_has_quorum_potential(step):
            step.status = ApprovalStep.STATUS_BLOCKED
            step.activated_at = timezone.now()
            step.save(update_fields=['status', 'activated_at'])
            _record_event(
                stock_out, ApprovalEvent.TYPE_STEP_BLOCK,
                detail=f'{spec["name"]}：可用审批人全部回避，节点阻塞，等待管理员改派',
                step=step,
            )
        steps.append(step)

    stock_out.rule_version = rule
    stock_out.route_snapshot = snapshot
    stock_out.risk_level = context['risk_level']
    first_step = steps[0]
    if first_step.status == ApprovalStep.STATUS_PENDING:
        first_step.status = ApprovalStep.STATUS_ACTIVE
        first_step.activated_at = timezone.now()
        first_step.save(update_fields=['status', 'activated_at'])
    stock_out.current_step = first_step
    stock_out.status = StockOut.STATUS_PENDING
    stock_out.save()
    _record_event(
        stock_out, ApprovalEvent.TYPE_SUBMIT,
        detail=f'按审批规则 v{rule.version} 提交，共实例化 {len(steps)} 个审批节点',
        actor=actor,
    )
    if first_step.status == ApprovalStep.STATUS_ACTIVE:
        _record_event(
            stock_out, ApprovalEvent.TYPE_STEP_ACTIVATE,
            detail=f'进入节点：{first_step.name}', step=first_step,
        )
    return stock_out


def _step_has_quorum_potential(step):
    """排除已回避/无需签署者后，节点是否仍存在通过可能"""
    available = step.signers.filter(status=StepSigner.STATUS_PENDING).count()
    if step.sign_policy == 'quorum':
        return step.signers.filter(status=StepSigner.STATUS_APPROVED).count() + available >= (step.quorum or 1)
    return available >= 1


def _required_approvals(step):
    if step.sign_policy == 'any':
        return 1
    if step.sign_policy == 'quorum':
        return step.quorum or 1
    return step.signers.exclude(
        status__in=[StepSigner.STATUS_RECUSED, StepSigner.STATUS_SKIPPED]).count()


def _settle_remaining_signers(step):
    """节点终态化后，把未决签署行收敛为“无需签署”，保证每人状态确定。"""
    StepSigner.objects.filter(
        step=step, status=StepSigner.STATUS_PENDING
    ).update(status=StepSigner.STATUS_SKIPPED, signed_at=timezone.now())


# ==================== 签署 / 拒绝 / 回避 ====================
#
# 并发正确性（SQLite 下 select_for_update 为空操作）：
# 每个状态变更事务的【第一条写语句】都是带前置状态条件的 UPDATE（CAS）。
# - SQLite：写语句立即获取数据库级保留写锁，并发请求在 busy timeout 内排队，
#   后到者在锁释放后按已提交数据重新求值，CAS 条件不再成立 -> rows=0；
# - PostgreSQL：条件 UPDATE 直接锁定命中行，效果等价。
# 因此“多人并发签署”的结果是确定的：每一行签署状态只会被推进一次，
# 节点级流转再用一次 CAS 认领（status=active -> 终态）保证只推进一次。


@with_lock_retry()
def sign_approval(stock_out_id, user, action, comment=''):
    """action: approve / reject。多人并发签署安全。"""
    if action not in ('approve', 'reject'):
        raise ApprovalStateError(f'未知签署动作：{action}')

    with transaction.atomic():
        # 事务首语句即写操作：先 CAS 认领签署行，再决定节点流转
        stock_out = StockOut.objects.select_related('goods', 'operator').get(pk=stock_out_id)
        if stock_out.status != StockOut.STATUS_PENDING or stock_out.current_step_id is None:
            raise ApprovalStateError('该申请已不在审批中')
        step = ApprovalStep.objects.get(pk=stock_out.current_step_id)
        signer = StepSigner.objects.filter(step=step, user=user).first()
        if signer is None:
            raise ApprovalStateError('当前节点不需要您签署')
        if signer.status != StepSigner.STATUS_PENDING:
            raise ApprovalStateError(f'您已完成该节点操作：{signer.get_status_display()}')
        if step.status == ApprovalStep.STATUS_BLOCKED:
            raise ApprovalStateError('该节点因审批人回避已阻塞，等待管理员改派')
        if step.status != ApprovalStep.STATUS_ACTIVE:
            raise ApprovalStateError('该节点已结束，无需重复签署')

        new_status = (StepSigner.STATUS_APPROVED if action == 'approve'
                      else StepSigner.STATUS_REJECTED)
        claimed = StepSigner.objects.filter(
            id=signer.id, status=StepSigner.STATUS_PENDING,
        ).update(status=new_status, comment=comment, signed_at=timezone.now())
        if claimed == 0:  # 并发下已被另一请求处理
            raise ApprovalStateError('签署状态已变更，请刷新后重试')

        _record_event(
            stock_out,
            ApprovalEvent.TYPE_APPROVE if action == 'approve' else ApprovalEvent.TYPE_REJECT,
            comment or ('同意' if action == 'approve' else '不同意'),
            actor=user, step=step,
        )
        if action == 'approve':
            _advance_if_satisfied(stock_out, step)
        else:
            _check_rejection(stock_out, step)

        stock_out.refresh_from_db()
        step.refresh_from_db()
        return stock_out, step


def _claim_step(step, from_status, to_status):
    """CAS 认领节点终态，只有一个并发事务能命中"""
    now = timezone.now()
    updated = ApprovalStep.objects.filter(
        id=step.id, status=from_status
    ).update(status=to_status, finished_at=now)
    if updated:
        step.status = to_status
        step.finished_at = now
    return updated


def _advance_if_satisfied(stock_out, step):
    approved = step.signers.filter(status=StepSigner.STATUS_APPROVED).count()
    required = _required_approvals(step)
    if approved < required:
        return  # 会签尚未集齐，节点保持 active，其余签署人继续

    if not _claim_step(step, ApprovalStep.STATUS_ACTIVE, ApprovalStep.STATUS_APPROVED):
        return  # 并发事务已终态化该节点
    step.refresh_from_db(fields=['status', 'finished_at'])
    _settle_remaining_signers(step)

    next_step = stock_out.steps.filter(order_index__gt=step.order_index).first()
    if next_step is None:
        moved = StockOut.objects.filter(
            id=stock_out.id, status=StockOut.STATUS_PENDING, current_step=step,
        ).update(status=StockOut.STATUS_APPROVED, current_step=None)
        if moved:
            stock_out.status = StockOut.STATUS_APPROVED
            stock_out.current_step = None
            _record_event(stock_out, ApprovalEvent.TYPE_COMPLETE,
                          '全部审批节点通过，申请已放行', step=step)
        return

    # 激活下一节点（若其因全员回避在创建时即阻塞，则保持阻塞等待改派）
    ApprovalStep.objects.filter(
        id=next_step.id, status=ApprovalStep.STATUS_PENDING
    ).update(status=ApprovalStep.STATUS_ACTIVE, activated_at=timezone.now())
    next_step.refresh_from_db(fields=['status', 'activated_at'])
    StockOut.objects.filter(id=stock_out.id, current_step=step).update(
        current_step=next_step)
    stock_out.current_step = next_step
    if next_step.status == ApprovalStep.STATUS_ACTIVE:
        _record_event(stock_out, ApprovalEvent.TYPE_STEP_ACTIVATE,
                      f'进入节点：{next_step.name}', step=next_step)


def _check_rejection(stock_out, step):
    """会签下仅当剩余同意票已不可能满足策略时才否决整单"""
    approved = step.signers.filter(status=StepSigner.STATUS_APPROVED).count()
    pending = step.signers.filter(status=StepSigner.STATUS_PENDING).count()
    required = _required_approvals(step)
    if approved + pending >= required:
        return  # 仍存在通过可能，拒绝票记录在案，其他人继续签署

    if not _claim_step(step, ApprovalStep.STATUS_ACTIVE, ApprovalStep.STATUS_REJECTED):
        return
    step.refresh_from_db(fields=['status', 'finished_at'])
    _settle_remaining_signers(step)
    StockOut.objects.filter(
        id=stock_out.id, status=StockOut.STATUS_PENDING,
    ).update(status=StockOut.STATUS_REJECTED, current_step=None)
    stock_out.status = StockOut.STATUS_REJECTED
    stock_out.current_step = None
    # 后续节点确定性终止
    later_steps = list(stock_out.steps.filter(
        order_index__gt=step.order_index, status=ApprovalStep.STATUS_PENDING))
    for later_step in later_steps:
        ApprovalStep.objects.filter(
            id=later_step.id, status=ApprovalStep.STATUS_PENDING
        ).update(status=ApprovalStep.STATUS_SKIPPED, finished_at=timezone.now())
        _record_event(stock_out, ApprovalEvent.TYPE_STEP_SKIP,
                      '前置节点已拒绝，节点终止', step=later_step)


@with_lock_retry()
def recuse(stock_out_id, user, comment=''):
    """当前签署人主动回避；节点因此无法达到通过门槛时阻塞，等待改派。"""
    with transaction.atomic():
        stock_out = StockOut.objects.get(pk=stock_out_id)
        if stock_out.status != StockOut.STATUS_PENDING or stock_out.current_step_id is None:
            raise ApprovalStateError('该申请已不在审批中')
        step = ApprovalStep.objects.get(pk=stock_out.current_step_id)
        signer = StepSigner.objects.filter(
            step=step, user=user, status=StepSigner.STATUS_PENDING).first()
        if signer is None:
            raise ApprovalStateError('当前节点没有待您处理的签署任务')
        if step.status != ApprovalStep.STATUS_ACTIVE:
            raise ApprovalStateError('该节点已结束，无需回避')

        claimed = StepSigner.objects.filter(
            id=signer.id, status=StepSigner.STATUS_PENDING,
        ).update(
            status=StepSigner.STATUS_RECUSED,
            comment=comment, signed_at=timezone.now(),
        )
        if claimed == 0:  # 并发下已被另一请求处理
            raise ApprovalStateError('签署状态已变更，请刷新后重试')
        # 主动回避标记（认领 CAS 已保证只有本事务执行到此）
        StepSigner.objects.filter(id=signer.id, source='normal').update(
            source='active_recused')

        _record_event(stock_out, ApprovalEvent.TYPE_RECUSE,
                      comment or '审批人主动回避', actor=user, step=step)

        blocked = False
        if not _step_has_quorum_potential(step):
            blocked = bool(ApprovalStep.objects.filter(
                id=step.id, status=ApprovalStep.STATUS_ACTIVE
            ).update(status=ApprovalStep.STATUS_BLOCKED))
            if blocked:
                step.status = ApprovalStep.STATUS_BLOCKED
                _record_event(stock_out, ApprovalEvent.TYPE_STEP_BLOCK,
                              '剩余审批人无法满足签署策略，节点阻塞，等待管理员改派', step=step)
        stock_out.refresh_from_db()
        step.refresh_from_db()
        return stock_out, step


@with_lock_retry()
def delegate(stock_out_id, admin_user, new_user, comment=''):
    """管理员对阻塞节点改派审批人，改派后节点恢复签署中。"""
    if not getattr(admin_user, 'is_admin', False):
        raise ApprovalStateError('仅管理员可以改派审批人')
    with transaction.atomic():
        stock_out = StockOut.objects.select_for_update().select_related('goods').get(pk=stock_out_id)
        if stock_out.status != StockOut.STATUS_PENDING or stock_out.current_step_id is None:
            raise ApprovalStateError('该申请已不在审批中')
        step = ApprovalStep.objects.select_for_update().get(pk=stock_out.current_step_id)
        if step.status != ApprovalStep.STATUS_BLOCKED:
            raise ApprovalStateError('仅阻塞中的节点允许改派')
        if not new_user or not new_user.is_active:
            raise ApprovalStateError('改派对象不是有效用户')
        if new_user.id == stock_out.operator_id:
            raise ApprovalStateError('不能改派给申请人本人')
        if step.signers.filter(user=new_user).exists():
            raise ApprovalStateError('该用户已是本节点签署人')

        signer = StepSigner.objects.create(
            step=step, user=new_user, source='delegated',
        )
        step.status = ApprovalStep.STATUS_ACTIVE
        step.save(update_fields=['status'])
        _record_event(
            stock_out, ApprovalEvent.TYPE_DELEGATE,
            f'改派 {new_user.real_name or new_user.username} 参与{step.name}'
            + (f'（{comment}）' if comment else ''),
            actor=admin_user, step=step,
        )
        return stock_out, step, signer


# ==================== 撤销 / 重提 / 条件变化重评 ====================

@with_lock_retry()
def cancel(stock_out_id, user):
    with transaction.atomic():
        stock_out = StockOut.objects.select_for_update().get(pk=stock_out_id)
        if stock_out.status != StockOut.STATUS_PENDING:
            raise ApprovalStateError('仅审批中的申请可以撤销')
        if stock_out.operator_id != user.id and not user.is_admin:
            raise ApprovalStateError('仅申请人或管理员可以撤销申请')
        stock_out.status = StockOut.STATUS_CANCELLED
        stock_out.current_step = None
        stock_out.save(update_fields=['status', 'current_step'])
        active = stock_out.steps.filter(status__in=[
            ApprovalStep.STATUS_ACTIVE, ApprovalStep.STATUS_BLOCKED]).first()
        for step in stock_out.steps.filter(status__in=[
            ApprovalStep.STATUS_PENDING, ApprovalStep.STATUS_ACTIVE,
            ApprovalStep.STATUS_BLOCKED,
        ]):
            step.status = ApprovalStep.STATUS_SKIPPED
            step.finished_at = timezone.now()
            step.save(update_fields=['status', 'finished_at'])
        _record_event(stock_out, ApprovalEvent.TYPE_CANCEL, '申请撤销', actor=user,
                      step=active)
        return stock_out


@with_lock_retry()
def resubmit(rejected, actor, **overrides):
    """
    拒绝后重提：生成新申请（修订号 +1），按当前生效规则重新评估、重新冻结快照。
    原拒绝记录保留作为审计依据。
    """
    with transaction.atomic():
        rejected = StockOut.objects.select_for_update().get(pk=rejected.id)
        if rejected.status != StockOut.STATUS_REJECTED:
            raise ApprovalStateError('仅已拒绝的申请可以重新提交')

        application = StockOut(
            goods=rejected.goods,
            operator=actor,
            receiver=overrides.get('receiver', rejected.receiver),
            receiver_dept=overrides.get('receiver_dept', rejected.receiver_dept),
            quantity=overrides.get('quantity', rejected.quantity),
            purpose_type=overrides.get('purpose_type', rejected.purpose_type),
            purpose=overrides.get('purpose', rejected.purpose),
            remark=overrides.get('remark', rejected.remark),
            status=StockOut.STATUS_DRAFT,
            revised_from=rejected,
            revision_no=rejected.revision_no + 1,
        )
        application.save()
        instantiate_route(application, actor=actor)
        _record_event(
            application, ApprovalEvent.TYPE_RESUBMIT,
            f'第 {application.revision_no} 次提交：基于被拒绝的申请 #{rejected.id} 修订后重提，按最新生效规则重新评估',
            actor=actor,
        )
        return application


@with_lock_retry()
def reevaluate(stock_out, actor, **changes):
    """
    条件变化重新评估：规则版本保持不变（在办路线不受规则调整影响），
    按新条件在同一版本内重新命中节点并刷新人员快照。
    仅允许在尚无任何实际签署/回避发生时进行，否则需拒绝后重提。
    """
    with transaction.atomic():
        stock_out = StockOut.objects.select_for_update().select_related('goods').get(pk=stock_out.id)
        if stock_out.status != StockOut.STATUS_PENDING:
            raise ApprovalStateError('仅审批中的申请可以变更条件')
        decided = StepSigner.objects.filter(
            step__stock_out=stock_out
        ).exclude(status=StepSigner.STATUS_PENDING).exclude(
            status=StepSigner.STATUS_RECUSED, source='auto_recused'
        ).exists()
        if decided:
            raise ApprovalStateError('已产生人工签署或回避，不能直接变更条件，请重新提交申请')

        old_context = build_context(stock_out)
        editable = {'goods', 'quantity', 'receiver_dept', 'purpose_type', 'purpose', 'receiver'}
        for field, value in changes.items():
            if field in editable:
                setattr(stock_out, field, value)
        stock_out.save()
        new_context = build_context(stock_out)

        # 删除旧实例（事件保留，step 置空作为历史）
        stock_out.steps.all().delete()
        stock_out.current_step = None
        stock_out.save(update_fields=['current_step'])

        rule = stock_out.rule_version
        matched = evaluate(rule.nodes, new_context)
        role_members = _snapshot_role_members(matched, new_context)
        missing = [
            ROLE_NAMES.get(s['role'], s['role'])
            for s in matched if not role_members.get(s['role'])
        ]
        if missing:
            raise ApprovalConfigError(f"以下审批角色尚未配置在岗审批人：{'、'.join(dict.fromkeys(missing))}")

        snapshot = {
            **stock_out.route_snapshot,
            'matched_steps': [
                {'code': s['code'], 'name': s['name'], 'role': s['role'],
                 'policy': s.get('policy', 'all'), 'quorum': s.get('quorum'),
                 'reason': s.get('reason', ''), 'facts': s.get('facts', {})}
                for s in matched
            ],
            'inputs': {k: _json_safe(v) for k, v in new_context.items()},
            'role_members': role_members,
            'reevaluated': True,
        }
        stock_out.route_snapshot = snapshot
        stock_out.risk_level = new_context['risk_level']

        steps = []
        applicant_id = new_context['applicant_id']
        for index, spec in enumerate(matched, start=1):
            step = ApprovalStep.objects.create(
                stock_out=stock_out, order_index=index, name=spec['name'],
                role_code=spec['role'], sign_policy=spec.get('policy', 'all'),
                quorum=spec.get('quorum'), reason=spec.get('reason', ''),
            )
            for user in resolve_role_users(spec['role'], new_context['receiver_dept']):
                is_applicant = user.id == applicant_id
                StepSigner.objects.create(
                    step=step, user=user,
                    status=StepSigner.STATUS_RECUSED if is_applicant else StepSigner.STATUS_PENDING,
                    source='auto_recused' if is_applicant else 'normal',
                )
                if is_applicant:
                    _record_event(stock_out, ApprovalEvent.TYPE_AUTO_RECUSE,
                                  f'{spec["name"]}：申请人即审批人，自动回避',
                                  actor=user, step=step)
            if not _step_has_quorum_potential(step):
                step.status = ApprovalStep.STATUS_BLOCKED
                step.activated_at = timezone.now()
                step.save(update_fields=['status', 'activated_at'])
                _record_event(stock_out, ApprovalEvent.TYPE_STEP_BLOCK,
                              f'{spec["name"]}：审批人全部回避，节点阻塞', step=step)
            steps.append(step)

        first = steps[0]
        if first.status == ApprovalStep.STATUS_PENDING:
            first.status = ApprovalStep.STATUS_ACTIVE
            first.activated_at = timezone.now()
            first.save(update_fields=['status', 'activated_at'])
        stock_out.current_step = first
        stock_out.save()

        changed_keys = [
            k for k in ('risk_level', 'quantity', 'receiver_dept', 'purpose_type')
            if _json_safe(old_context.get(k)) != _json_safe(new_context.get(k))
        ]
        _record_event(
            stock_out, ApprovalEvent.TYPE_REEVALUATE,
            f"条件变化（{ '、'.join(changed_keys) if changed_keys else '无'}）后在规则 v{rule.version} 内重新评估，"
            f'路线调整为 {len(steps)} 个节点：' + ' → '.join(s.name for s in steps),
            actor=actor, step=first,
        )
        return stock_out


# ==================== 详情解释 ====================

def policy_text(step):
    if step.sign_policy == 'quorum':
        return f'至少 {step.quorum} 人通过'
    return POLICY_NAMES.get(step.sign_policy, step.sign_policy)


def current_requirement(stock_out):
    """生成“当前为什么需要这些签署”的一句话说明"""
    step = stock_out.current_step
    if stock_out.status == StockOut.STATUS_APPROVED:
        return '全部签署已完成，申请已放行。'
    if stock_out.status == StockOut.STATUS_REJECTED:
        return '申请已被拒绝，可按拒绝意见修订后重新提交。'
    if stock_out.status == StockOut.STATUS_CANCELLED:
        return '申请已撤销。'
    if stock_out.status == StockOut.STATUS_COMPLETED:
        return '申请已完成出库。'
    if step is None:
        return ''
    pending_people = [
        s.user.real_name or s.user.username
        for s in step.signers.filter(status=StepSigner.STATUS_PENDING).select_related('user')
    ]
    approved = step.signers.filter(status=StepSigner.STATUS_APPROVED).count()
    required = _required_approvals(step)
    head = f'当前节点「{step.name}」（{policy_text(step)}）：{step.reason}'
    if step.status == ApprovalStep.STATUS_BLOCKED:
        return head + ' 当前可用审批人均已回避，节点阻塞，等待管理员改派。'
    progress = f'已签署 {approved}/{required}。'
    people = f'待签署人：{"、".join(pending_people)}。' if pending_people else ''
    return f'{head} {progress}{people}'
