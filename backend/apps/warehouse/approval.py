"""
版本化审批路线引擎
==================

职责：
1. 在生效规则版本内，按 物资风险 / 数量 / 领用部门 / 用途 确定性匹配审批路线；
2. 申请创建时固化规则版本与路线快照——此后规则调整只产生新版本，不影响在途申请；
3. 依据快照实例化签署任务，驱动 单人 / 或签 / 会签 状态机；
4. 处理申请人自动回避、审批人主动回避与替补、拒绝后重提、条件变化重新评估；
5. 输出申请详情所需的“为什么需要当前这些签署”的解释。

所有写操作都在事务内完成，并以条件 UPDATE 兜底并发签署，
同一任务的重复提交会得到确定的 409，而不是重复生效。
"""
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .models import (
    ApprovalNode, ApprovalNodeApprover, ApprovalRoute, ApprovalRouteEvaluation,
    ApprovalRuleVersion, ApprovalTask, Goods, StockOut, User,
)


class ApprovalError(Exception):
    """审批业务错误：message 面向用户，code 为建议的 HTTP 状态码"""

    def __init__(self, message, code=400):
        super().__init__(message)
        self.message = message
        self.code = code


# ==================== 条件与匹配 ====================

def build_conditions(goods, quantity, receiver_dept, purpose):
    """汇总一次路线评估所依据的申请条件"""
    return {
        'goods_id': goods.id,
        'goods_name': goods.name,
        'risk_level': goods.risk_level,
        'risk_level_display': goods.get_risk_level_display(),
        'quantity': str(quantity),
        'receiver_dept': (receiver_dept or '').strip(),
        'purpose': purpose or '',
        'purpose_display': dict(StockOut.PURPOSE_CHOICES).get(purpose or '', ''),
    }


def _route_match_detail(route, conditions):
    """返回 (是否命中, 命中原因列表)。空约束维度视为“不限”，不产生原因。"""
    reasons = []
    if route.risk_level:
        if conditions['risk_level'] != route.risk_level:
            return False, []
        reasons.append({
            'field': 'risk_level',
            'text': f"物资风险等级为「{conditions['risk_level_display']}」，路线要求"
                    f"「{dict(Goods.RISK_CHOICES)[route.risk_level]}」",
        })
    qty = Decimal(str(conditions['quantity']))
    if route.min_quantity is not None:
        if qty < route.min_quantity:
            return False, []
        reasons.append({
            'field': 'min_quantity',
            'text': f"申领数量 {qty} 达到下限 {route.min_quantity}",
        })
    if route.max_quantity is not None:
        if qty > route.max_quantity:
            return False, []
        reasons.append({
            'field': 'max_quantity',
            'text': f"申领数量 {qty} 未超过上限 {route.max_quantity}",
        })
    if route.receiver_dept:
        if conditions['receiver_dept'] != route.receiver_dept.strip():
            return False, []
        reasons.append({
            'field': 'receiver_dept',
            'text': f"领用部门为「{conditions['receiver_dept']}」",
        })
    if route.purpose:
        if conditions['purpose'] != route.purpose:
            return False, []
        reasons.append({
            'field': 'purpose',
            'text': f"用途为「{conditions['purpose_display']}」",
        })
    return True, reasons


def match_route(version, conditions):
    """
    确定性选路：非兜底路线按 (priority, id) 取第一条全部条件命中者；
    均未命中时取版本唯一兜底路线。
    """
    routes = list(
        version.routes.filter(is_active=True).order_by('priority', 'id')
    )
    for route in routes:
        if route.is_fallback:
            continue
        matched, reasons = _route_match_detail(route, conditions)
        if matched:
            reasons.append({
                'field': 'priority',
                'text': f"该路线优先级为 {route.priority}，为所有命中路线中最高",
            })
            return route, reasons
    fallback = next((r for r in routes if r.is_fallback), None)
    if fallback is None:
        raise ApprovalError('当前生效规则未配置兜底路线，请联系管理员完善规则')
    return fallback, [{
        'field': 'fallback',
        'text': '申请条件未命中任何专属路线，按兜底路线审批',
    }]


def get_active_version():
    return (
        ApprovalRuleVersion.objects.filter(status=ApprovalRuleVersion.STATUS_ACTIVE)
        .order_by('-version').first()
    )


# ==================== 快照 ====================

def _user_brief(user):
    return {
        'user_id': user.id,
        'username': user.username,
        'real_name': user.real_name or user.username,
    }


def _node_why(node, designated_users, sign_mode_display):
    parts = [f"本节点为{sign_mode_display}"]
    if designated_users:
        names = '、'.join(u.real_name or u.username for u in designated_users)
        parts.append(f"指定审批人：{names}")
    else:
        parts.append('未指定具体审批人')
    if node.use_active_admins:
        parts.append('指定人不可用时由在职管理员按序兜底')
    return '；'.join(parts)


def build_route_snapshot(version, route, conditions, reasons):
    """把路线规则原样复制为 JSON 快照，申请之后只认快照，不再回查可变表数据"""
    nodes = []
    for node in route.nodes.all():
        links = (
            ApprovalNodeApprover.objects
            .filter(node=node, approver__is_active=True)
            .select_related('approver').order_by('order', 'id')
        )
        designated_users = [link.approver for link in links]
        sign_mode_display = node.get_sign_mode_display()
        nodes.append({
            'order': node.order,
            'name': node.name,
            'sign_mode': node.sign_mode,
            'sign_mode_display': sign_mode_display,
            'use_active_admins': node.use_active_admins,
            'designated_approvers': [_user_brief(u) for u in designated_users],
            'why': _node_why(node, designated_users, sign_mode_display),
        })
    return {
        'version': {'id': version.id, 'version': version.version},
        'route': {'id': route.id, 'name': route.name, 'priority': route.priority,
                  'is_fallback': route.is_fallback},
        'conditions': conditions,
        'reasons': reasons,
        'nodes': nodes,
    }


# ==================== 候选人解析与任务实例化 ====================

def _active_admin_ids():
    return list(
        User.objects.filter(is_active=True, role__in=['admin', 'superadmin'])
        .order_by('id').values_list('id', flat=True)
    )


def _dedupe(seq):
    seen = set()
    result = []
    for item in seq:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _node_candidates(node_snap):
    """
    返回 (signer_pool, fallback_admins)：
    - 节点指定了审批人时，签署人只取指定人，管理员不自动混入；
    - 未指定审批人且开启兜底时，签署人取在职管理员；
    - 指定人全部回避后，单人/会签节点再从管理员中补位。
    """
    designated = _dedupe(b['user_id'] for b in node_snap['designated_approvers'])
    admins = _active_admin_ids() if node_snap['use_active_admins'] else []
    signer_pool = designated if designated else list(admins)
    return signer_pool, list(admins)


def _node_chain(node_snap, applicant_id):
    """
    返回 (候选审批人ID有序列表（剔除申请人）, 需自动回避的审批人ID列表)。
    """
    signer_pool, _ = _node_candidates(node_snap)
    auto_recused = [uid for uid in signer_pool if uid == applicant_id]
    chain = [uid for uid in signer_pool if uid != applicant_id]
    return chain, auto_recused


def _node_required(node_snap, applicant_id):
    """节点通过所需同意数：单人/或签 1，会签为候选人数；无候选人为 0"""
    chain, _ = _node_chain(node_snap, applicant_id)
    if not chain:
        return 0
    if node_snap['sign_mode'] == ApprovalNode.SIGN_ALL:
        return len(chain)
    return 1


def _create_tasks_for_node(stock_out, node_snap, status_for_others, applicant_id, now,
                           generation, adopted_approved_ids=None):
    """
    按快照为一个节点创建任务（归属指定路线代次）。
    - 单人节点只实例化首位候选人，其余候选人留作回避替补；
    - 或签/会签为每位候选人生成任务；
    adopted_approved_ids：重评换路线时，可沿用上一代批准结论的审批人，
      直接在当代生成 approved 任务（旧任务保留为审计痕迹）。
    """
    adopted_approved_ids = set(adopted_approved_ids or [])
    chain, auto_recused = _node_chain(node_snap, applicant_id)
    created = []

    # 申请人命中审批人：留一条确定的“自动回避”记录
    for uid in auto_recused:
        created.append(ApprovalTask.objects.create(
            stock_out=stock_out, generation=generation,
            node_order=node_snap['order'],
            node_name=node_snap['name'], sign_mode=node_snap['sign_mode'],
            approver_id=uid, status=ApprovalTask.STATUS_RECUSED,
            remark='申请人本人，按规定自动回避', acted_at=now,
        ))

    if node_snap['sign_mode'] == ApprovalNode.SIGN_SINGLE:
        active_ids = chain[:1]
    else:
        active_ids = chain

    for uid in active_ids:
        if uid in adopted_approved_ids:
            created.append(ApprovalTask.objects.create(
                stock_out=stock_out, generation=generation,
                node_order=node_snap['order'],
                node_name=node_snap['name'], sign_mode=node_snap['sign_mode'],
                approver_id=uid, status=ApprovalTask.STATUS_APPROVED,
                remark=f'沿用第{generation - 1}代路线的已批准结论', acted_at=now,
            ))
            continue
        created.append(ApprovalTask.objects.create(
            stock_out=stock_out, generation=generation,
            node_order=node_snap['order'],
            node_name=node_snap['name'], sign_mode=node_snap['sign_mode'],
            approver_id=uid, status=status_for_others,
        ))

    return created, _node_required(node_snap, applicant_id)


def instantiate_application(stock_out, snapshot, applicant_id):
    """首次实例化（第1代）：激活第一个节点，后续节点 waiting；无候选人则 blocked"""
    now = timezone.now()
    required_map = {}
    for idx, node_snap in enumerate(snapshot['nodes']):
        status = ApprovalTask.STATUS_PENDING if idx == 0 else ApprovalTask.STATUS_WAITING
        _, required = _create_tasks_for_node(
            stock_out, node_snap, status, applicant_id, now, generation=1
        )
        required_map[node_snap['order']] = required
    snapshot['required_approvals'] = {str(k): v for k, v in required_map.items()}
    stock_out.route_snapshot = snapshot
    first_order = snapshot['nodes'][0]['order'] if snapshot['nodes'] else None
    if first_order is not None and required_map.get(first_order, 0) > 0:
        stock_out.current_node_order = first_order
        stock_out.status = StockOut.STATUS_PENDING
    else:
        stock_out.current_node_order = first_order
        stock_out.status = StockOut.STATUS_BLOCKED


# ==================== 状态机 ====================

def _node_tasks(stock_out, order):
    return stock_out.approval_tasks.filter(
        node_order=order, generation=stock_out.route_generation
    )


def _node_state(stock_out, node_snap):
    order = node_snap['order']
    required = int(stock_out.route_snapshot.get('required_approvals', {}).get(str(order), 0))
    tasks = _node_tasks(stock_out, order)
    approved_count = tasks.filter(status=ApprovalTask.STATUS_APPROVED).count()
    if required > 0 and approved_count >= required:
        return 'approved'

    has_active = tasks.filter(status__in=ApprovalTask.ACTIVE_STATUSES).exists()
    has_rejected = tasks.filter(status=ApprovalTask.STATUS_REJECTED).exists()

    if node_snap['sign_mode'] == ApprovalNode.SIGN_ANY:
        # 或签：一人同意即通过；单人拒绝不终结，其余人仍可放行；
        # 全员处理完且无人同意时，有拒绝才判拒绝，否则阻塞
        if has_active:
            return 'in_progress'
        return 'rejected' if has_rejected else 'blocked'

    # 单人 / 会签：拒绝即否决（会签要求全员同意）
    if has_rejected:
        return 'rejected'
    if not has_active:
        return 'blocked'
    return 'in_progress'


def _cancel_active_tasks(stock_out, orders=None, remark=''):
    qs = stock_out.approval_tasks.filter(
        status__in=ApprovalTask.ACTIVE_STATUSES,
        generation=stock_out.route_generation,
    )
    if orders is not None:
        qs = qs.filter(node_order__in=orders)
    qs.update(status=ApprovalTask.STATUS_CANCELLED, remark=remark)


def _activate_node(stock_out, node_snap):
    """把节点的 waiting 任务激活；单人节点只激活最早的一条"""
    qs = _node_tasks(stock_out, node_snap['order']).filter(
        status=ApprovalTask.STATUS_WAITING
    ).order_by('id')
    if node_snap['sign_mode'] == ApprovalNode.SIGN_SINGLE:
        qs = qs[:1]
        for task in qs:
            task.status = ApprovalTask.STATUS_PENDING
            task.save(update_fields=['status', 'updated_at'])
    else:
        qs.update(status=ApprovalTask.STATUS_PENDING)


def advance(stock_out):
    """从当前节点开始推进状态机，直到停住或终结。调用方须持有事务。"""
    snapshot = stock_out.route_snapshot
    nodes = snapshot['nodes']
    node_by_order = {n['order']: n for n in nodes}
    current = stock_out.current_node_order

    while current is not None:
        node_snap = node_by_order[current]
        state = _node_state(stock_out, node_snap)
        if state == 'rejected':
            _cancel_active_tasks(
                stock_out,
                remark=f"节点 {current} 已拒绝，后续签署自动取消",
            )
            stock_out.status = StockOut.STATUS_REJECTED
            stock_out.current_node_order = current
            stock_out.save()
            return
        if state == 'blocked':
            _cancel_active_tasks(
                stock_out, orders=[current],
                remark='该节点已无可用审批人',
            )
            stock_out.status = StockOut.STATUS_BLOCKED
            stock_out.current_node_order = current
            stock_out.save()
            return
        if state == 'in_progress':
            stock_out.status = StockOut.STATUS_PENDING
            stock_out.current_node_order = current
            stock_out.save()
            return

        # state == approved：或签通过时取消同节点其他人，再推进下一节点
        _cancel_active_tasks(
            stock_out, orders=[current],
            remark=f"节点 {current} 已满足签署要求",
        )
        next_node = next(
            (n for n in nodes if n['order'] > current), None
        )
        if next_node is None:
            stock_out.status = StockOut.STATUS_APPROVED
            stock_out.save()
            return
        _activate_node(stock_out, next_node)
        required = int(snapshot.get('required_approvals', {}).get(
            str(next_node['order']), 0))
        if required == 0:
            # 新节点没有任何可签署人（如唯一候选人是申请人）：立即阻塞
            stock_out.status = StockOut.STATUS_BLOCKED
            stock_out.current_node_order = next_node['order']
            stock_out.save()
            return
        current = next_node['order']


def _conditional_finish_task(task, status, remark, now):
    """条件 UPDATE：只有仍处于 pending 的任务才能落签，防并发重复签署"""
    updated = ApprovalTask.objects.filter(
        id=task.id, status=ApprovalTask.STATUS_PENDING
    ).update(status=status, remark=remark, acted_at=now)
    if updated == 0:
        raise ApprovalError('该签署任务已被处理，请勿重复提交', code=409)
    task.status = status
    task.remark = remark
    task.acted_at = now


def _recuse_replacement(stock_out, task, node_snap, now):
    """
    回避后的确定处理：
    - 或签：不补人，其余签署人满足其一即可；
    - 单人/会签：按 指定人 → 在职管理员 的固定顺序补一名未参与过的审批人；
    - 会签无人可补时，应到人数减 1（回避人不计入会签 quorum）。
    """
    signer_pool, admins = _node_candidates(node_snap)
    full_pool = _dedupe(signer_pool + admins)

    used = set(
        _node_tasks(stock_out, node_snap['order'])
        .exclude(status=ApprovalTask.STATUS_CANCELLED)
        .values_list('approver_id', flat=True)
    )
    replacement_id = next(
        (uid for uid in full_pool
         if uid not in used and uid != stock_out.operator_id),
        None,
    )

    if node_snap['sign_mode'] == ApprovalNode.SIGN_ANY:
        return

    if replacement_id is not None:
        ApprovalTask.objects.create(
            stock_out=stock_out, generation=stock_out.route_generation,
            node_order=node_snap['order'],
            node_name=node_snap['name'], sign_mode=node_snap['sign_mode'],
            approver_id=replacement_id, status=ApprovalTask.STATUS_PENDING,
            replacement_for=task,
        )
        return

    if node_snap['sign_mode'] == ApprovalNode.SIGN_ALL:
        key = str(node_snap['order'])
        required_map = stock_out.route_snapshot.setdefault('required_approvals', {})
        required_map[key] = max(0, int(required_map.get(key, 0)) - 1)
        stock_out.route_snapshot = stock_out.route_snapshot  # 标记 JSONField 变更


# ==================== 对外服务 ====================

@transaction.atomic
def create_application(*, operator, goods, quantity, receiver, receiver_dept,
                       purpose, remark=''):
    """创建申请：选当前生效版本、固化快照、实例化任务，一步完成"""
    if quantity is None or quantity <= 0:
        raise ApprovalError('出库数量必须大于0')
    if quantity > goods.quantity:
        raise ApprovalError(f"库存不足，当前库存 {goods.quantity}")

    version = get_active_version()
    if version is None:
        raise ApprovalError('当前没有生效的审批规则版本，无法提交申请')

    conditions = build_conditions(goods, quantity, receiver_dept, purpose)
    route, reasons = match_route(version, conditions)
    snapshot = build_route_snapshot(version, route, conditions, reasons)

    stock_out = StockOut.objects.create(
        goods=goods, operator=operator, receiver=receiver,
        receiver_dept=(receiver_dept or '').strip(), purpose=purpose or '',
        quantity=quantity, remark=remark or '',
        rule_version=version,
    )
    instantiate_application(stock_out, snapshot, operator.id)
    stock_out.save()

    ApprovalRouteEvaluation.objects.create(
        stock_out=stock_out, type=ApprovalRouteEvaluation.TYPE_INITIAL,
        rule_version=version, route=route,
        conditions=conditions, matched_reasons=reasons, created_by=operator,
    )
    return stock_out


@transaction.atomic
def submit_decision(stock_out_id, user, action, remark=''):
    """同意 / 拒绝 / 回避。action ∈ approved / rejected / recused"""
    stock_out = StockOut.objects.select_for_update().select_related('operator').get(pk=stock_out_id)
    if stock_out.status not in (StockOut.STATUS_PENDING, StockOut.STATUS_BLOCKED):
        raise ApprovalError('该申请已结束，不能再签署', code=409)

    task = (
        ApprovalTask.objects
        .filter(
            stock_out=stock_out, approver=user,
            status=ApprovalTask.STATUS_PENDING,
            generation=stock_out.route_generation,
        )
        .order_by('node_order', 'id')
        .first()
    )
    if task is None:
        acted_on_current = ApprovalTask.objects.filter(
            stock_out=stock_out, approver=user,
            generation=stock_out.route_generation,
            node_order=stock_out.current_node_order,
            status__in=[ApprovalTask.STATUS_APPROVED, ApprovalTask.STATUS_REJECTED,
                        ApprovalTask.STATUS_RECUSED],
        ).exists()
        if acted_on_current:
            raise ApprovalError('您已处理过当前节点的签署，请勿重复提交', code=409)
        raise ApprovalError('当前没有待您签署的任务', code=403)
    if task.node_order != stock_out.current_node_order:
        raise ApprovalError('当前节点尚未轮到您签署', code=409)
    if not user.is_active:
        raise ApprovalError('账号已停用，不能签署', code=403)

    now = timezone.now()
    node_snap = next(
        n for n in stock_out.route_snapshot['nodes']
        if n['order'] == task.node_order
    )

    if action == 'approved':
        _conditional_finish_task(task, ApprovalTask.STATUS_APPROVED, remark, now)
        advance(stock_out)
    elif action == 'rejected':
        _conditional_finish_task(task, ApprovalTask.STATUS_REJECTED, remark, now)
        advance(stock_out)
    elif action == 'recused':
        _conditional_finish_task(task, ApprovalTask.STATUS_RECUSED, remark, now)
        _recuse_replacement(stock_out, task, node_snap, now)
        advance(stock_out)
    else:
        raise ApprovalError('未知的签署动作')
    stock_out.refresh_from_db()
    return stock_out


@transaction.atomic
def reevaluate_application(stock_out_id, *, user, changes):
    """
    条件变化后在【固定版本内】重新评估：
    - 路线不变：签署任务全部保留，仅追加评估记录；
    - 路线改变：路线代次 +1。旧代任务（含已批准/拒绝/回避）原样保留为审计痕迹；
      当代任务中，(节点顺序、签署方式、审批人) 一致的已批准结论直接沿用为 approved，
      其余候选人按新路线补建 pending/waiting 任务，流程从第一个未满足节点继续。
    """
    stock_out = StockOut.objects.select_for_update().get(pk=stock_out_id)
    if stock_out.status not in (StockOut.STATUS_PENDING, StockOut.STATUS_BLOCKED):
        raise ApprovalError('申请已结束，不能再修改条件', code=409)
    if stock_out.operator_id != user.id and not user.is_admin:
        raise ApprovalError('只能由申请人本人修改申请条件', code=403)

    goods = changes.get('goods', stock_out.goods)
    quantity = changes.get('quantity', stock_out.quantity)
    receiver_dept = changes.get('receiver_dept', stock_out.receiver_dept)
    purpose = changes.get('purpose', stock_out.purpose)
    if quantity is None or quantity <= 0:
        raise ApprovalError('出库数量必须大于0')
    if quantity > goods.quantity:
        raise ApprovalError(f"库存不足，当前库存 {goods.quantity}")

    stock_out.goods = goods
    stock_out.quantity = quantity
    stock_out.receiver_dept = (receiver_dept or '').strip()
    stock_out.purpose = purpose or ''

    conditions = build_conditions(goods, quantity, receiver_dept, purpose)
    route, reasons = match_route(stock_out.rule_version, conditions)

    ApprovalRouteEvaluation.objects.create(
        stock_out=stock_out, type=ApprovalRouteEvaluation.TYPE_REEVALUATE,
        rule_version=stock_out.rule_version, route=route,
        conditions=conditions, matched_reasons=reasons, created_by=user,
    )

    old_route_id = stock_out.route_snapshot.get('route', {}).get('id')
    if old_route_id == route.id:
        # 版本不可变 → 同路线的节点定义不变，任务继续有效；仅刷新快照中的条件与原因
        stock_out.route_snapshot['conditions'] = conditions
        stock_out.route_snapshot['reasons'] = reasons
        stock_out.save()
        stock_out.refresh_from_db()
        return stock_out

    old_generation = stock_out.route_generation
    new_generation = old_generation + 1
    snapshot = build_route_snapshot(stock_out.rule_version, route, conditions, reasons)
    now = timezone.now()

    # 上一代已批准结论：仅当 顺序+签署方式+审批人 在新节点中仍然成立时沿用
    prev_approved = {}
    for t in stock_out.approval_tasks.filter(
        generation=old_generation, status=ApprovalTask.STATUS_APPROVED
    ):
        prev_approved.setdefault((t.node_order, t.sign_mode), []).append(t.approver_id)

    # 旧代在办任务一律取消留痕（不删除，保证签署历史可追溯）
    stock_out.approval_tasks.filter(
        generation=old_generation,
        status__in=ApprovalTask.ACTIVE_STATUSES,
    ).update(status=ApprovalTask.STATUS_CANCELLED,
             remark=f'申请条件变化，切换至第{new_generation}代审批路线')

    required_map = {}
    first_unsatisfied = None
    for node_snap in snapshot['nodes']:
        order = node_snap['order']
        chain, auto_recused = _node_chain(node_snap, stock_out.operator_id)
        required = _node_required(node_snap, stock_out.operator_id)
        required_map[order] = required

        same_key = (order, node_snap['sign_mode'])
        adopted = [uid for uid in prev_approved.get(same_key, []) if uid in chain]
        if node_snap['sign_mode'] == ApprovalNode.SIGN_ALL:
            adopted = adopted[:required]
        else:
            adopted = adopted[:1]
        adopted_set = set(adopted)
        satisfied = required > 0 and len(adopted_set) >= required

        if not satisfied and first_unsatisfied is None:
            first_unsatisfied = order

        for uid in auto_recused:
            ApprovalTask.objects.create(
                stock_out=stock_out, generation=new_generation,
                node_order=order, node_name=node_snap['name'],
                sign_mode=node_snap['sign_mode'],
                approver_id=uid, status=ApprovalTask.STATUS_RECUSED,
                remark='申请人本人，按规定自动回避', acted_at=now,
            )
        for uid in chain:
            if uid in adopted_set:
                ApprovalTask.objects.create(
                    stock_out=stock_out, generation=new_generation,
                    node_order=order, node_name=node_snap['name'],
                    sign_mode=node_snap['sign_mode'],
                    approver_id=uid, status=ApprovalTask.STATUS_APPROVED,
                    remark=f'沿用第{old_generation}代路线的已批准结论', acted_at=now,
                )
            elif satisfied:
                ApprovalTask.objects.create(
                    stock_out=stock_out, generation=new_generation,
                    node_order=order, node_name=node_snap['name'],
                    sign_mode=node_snap['sign_mode'],
                    approver_id=uid, status=ApprovalTask.STATUS_CANCELLED,
                    remark='该节点已被上一代批准结论满足',
                )
            else:
                status = (ApprovalTask.STATUS_PENDING
                          if order == first_unsatisfied
                          else ApprovalTask.STATUS_WAITING)
                ApprovalTask.objects.create(
                    stock_out=stock_out, generation=new_generation,
                    node_order=order, node_name=node_snap['name'],
                    sign_mode=node_snap['sign_mode'],
                    approver_id=uid, status=status,
                )

    snapshot['required_approvals'] = {str(k): v for k, v in required_map.items()}
    stock_out.route_snapshot = snapshot
    stock_out.route_generation = new_generation
    if first_unsatisfied is None:
        stock_out.current_node_order = None
        stock_out.status = StockOut.STATUS_APPROVED
    else:
        stock_out.current_node_order = first_unsatisfied
        node_required = required_map.get(first_unsatisfied, 0)
        stock_out.status = (StockOut.STATUS_BLOCKED if node_required == 0
                            else StockOut.STATUS_PENDING)
    stock_out.save()
    return stock_out


@transaction.atomic
def resubmit_application(old_id, *, user, overrides=None):
    """拒绝后重提：复制原申请，按【当前生效版本】全新快照与签署"""
    old = StockOut.objects.get(pk=old_id)
    if old.status != StockOut.STATUS_REJECTED:
        raise ApprovalError('只有已拒绝的申请才能重新提交', code=409)

    overrides = overrides or {}
    new = create_application(
        operator=user,
        goods=overrides.get('goods', old.goods),
        quantity=overrides.get('quantity', old.quantity),
        receiver=overrides.get('receiver', old.receiver),
        receiver_dept=overrides.get('receiver_dept', old.receiver_dept),
        purpose=overrides.get('purpose', old.purpose),
        remark=overrides.get('remark', old.remark),
    )
    new.resubmitted_from = old
    new.save(update_fields=['resubmitted_from'])
    latest = new.evaluations.order_by('-created_at').first()
    if latest:
        latest.type = ApprovalRouteEvaluation.TYPE_RESUBMIT
        latest.save(update_fields=['type'])
    return new


@transaction.atomic
def assign_blocked_approver(stock_out_id, admin, approver_id):
    """
    blocked 恢复入口：管理员为当前阻塞节点指派一名在职审批人。
    指派不修改规则版本与快照，只在当代新增一条 pending 任务，然后继续推进。
    """
    stock_out = StockOut.objects.select_for_update().get(pk=stock_out_id)
    if stock_out.status != StockOut.STATUS_BLOCKED:
        raise ApprovalError('只有处于无法流转状态的申请才需要指派审批人', code=409)
    if not admin.is_admin:
        raise ApprovalError('仅管理员可指派替补审批人', code=403)
    approver = User.objects.filter(id=approver_id, is_active=True).first()
    if approver is None:
        raise ApprovalError('审批人不存在或已停用')
    if approver.id == stock_out.operator_id:
        raise ApprovalError('申请人不能作为本申请的审批人')

    order = stock_out.current_node_order
    node_snap = next(
        (n for n in stock_out.route_snapshot['nodes'] if n['order'] == order), None
    )
    if node_snap is None:
        raise ApprovalError('快照中找不到当前节点，无法指派')
    if stock_out.approval_tasks.filter(
        generation=stock_out.route_generation, node_order=order,
        approver_id=approver.id,
    ).exclude(status=ApprovalTask.STATUS_CANCELLED).exists():
        raise ApprovalError('该审批人已在此节点上，请选择其他人')

    ApprovalTask.objects.create(
        stock_out=stock_out, generation=stock_out.route_generation,
        node_order=order, node_name=node_snap['name'],
        sign_mode=node_snap['sign_mode'],
        approver=approver, status=ApprovalTask.STATUS_PENDING,
        remark='管理员在节点无人可签时指派',
    )
    # 阻塞节点原本 required=0；指派后按节点签署方式恢复应到人数
    required_map = stock_out.route_snapshot.setdefault('required_approvals', {})
    required_map[str(order)] = 1
    stock_out.save(update_fields=['route_snapshot', 'updated_at'])
    advance(stock_out)
    stock_out.refresh_from_db()
    return stock_out


@transaction.atomic
def complete_application(stock_out_id, user):
    """审批通过后放行出库：扣减库存并落出库时间"""
    stock_out = StockOut.objects.select_for_update().select_related('goods').get(pk=stock_out_id)
    if stock_out.status != StockOut.STATUS_APPROVED:
        raise ApprovalError('只有审批通过的申请才能完成出库', code=409)
    goods = Goods.objects.select_for_update().get(pk=stock_out.goods_id)
    if goods.quantity < stock_out.quantity:
        raise ApprovalError(f"库存不足，当前库存 {goods.quantity}")
    goods.quantity -= stock_out.quantity
    goods.save(update_fields=['quantity', 'updated_at'])
    stock_out.status = StockOut.STATUS_COMPLETED
    stock_out.stock_out_time = timezone.now()
    stock_out.save(update_fields=['status', 'stock_out_time', 'updated_at'])
    return stock_out


# ==================== 规则版本管理 ====================

def _validate_version_publishable(version):
    routes = list(version.routes.filter(is_active=True).prefetch_related('nodes'))
    if not routes:
        raise ApprovalError('版本至少需要一条启用的路线')
    fallbacks = [r for r in routes if r.is_fallback]
    if len(fallbacks) != 1:
        raise ApprovalError('每个版本必须配置且只能配置一条兜底路线')
    for route in routes:
        nodes = list(route.nodes.all())
        if not nodes:
            raise ApprovalError(f"路线「{route.name}」至少需要一个审批节点")
        orders = [n.order for n in nodes]
        if len(orders) != len(set(orders)):
            raise ApprovalError(f"路线「{route.name}」节点顺序重复")
        for node in nodes:
            has_designated = ApprovalNodeApprover.objects.filter(
                node=node, approver__is_active=True
            ).exists()
            if not has_designated and not node.use_active_admins:
                raise ApprovalError(
                    f"节点「{route.name}/{node.name}」既无指定审批人也未开启管理员兜底，无人可签"
                )
        if route.min_quantity is not None and route.max_quantity is not None:
            if route.min_quantity > route.max_quantity:
                raise ApprovalError(f"路线「{route.name}」数量下限不能大于上限")


@transaction.atomic
def publish_version(version_id, user):
    version = ApprovalRuleVersion.objects.select_for_update().get(pk=version_id)
    if version.status != ApprovalRuleVersion.STATUS_DRAFT:
        raise ApprovalError('只有草稿版本才能发布', code=409)
    _validate_version_publishable(version)
    ApprovalRuleVersion.objects.filter(
        status=ApprovalRuleVersion.STATUS_ACTIVE
    ).update(status=ApprovalRuleVersion.STATUS_ARCHIVED)
    version.status = ApprovalRuleVersion.STATUS_ACTIVE
    version.published_at = timezone.now()
    version.published_by = user
    version.save()
    return version


def ensure_editable(version):
    """已发布版本及其路线/节点一律不可变，规则调整请新建版本"""
    if version.status != ApprovalRuleVersion.STATUS_DRAFT:
        raise ApprovalError('已发布的规则版本不可修改，请新建版本后调整', code=409)


# ==================== 详情解释 ====================

def explain_application(stock_out):
    """构造申请详情中的审批解释：为什么命中这条路线、为什么需要这些人签"""
    snapshot = stock_out.route_snapshot or {}
    designated_map = {}
    for node in snapshot.get('nodes', []):
        designated_map[node['order']] = {
            a['user_id'] for a in node.get('designated_approvers', [])
        }

    nodes_view = []
    for node in snapshot.get('nodes', []):
        order = node['order']
        required = int(snapshot.get('required_approvals', {}).get(str(order), 0))
        tasks = list(
            stock_out.approval_tasks.filter(
                node_order=order, generation=stock_out.route_generation
            )
            .select_related('approver').order_by('id')
        )
        approved_count = sum(1 for t in tasks if t.status == ApprovalTask.STATUS_APPROVED)
        rejected_count = sum(1 for t in tasks if t.status == ApprovalTask.STATUS_REJECTED)
        has_active = any(t.status in ApprovalTask.ACTIVE_STATUSES for t in tasks)
        is_any = node['sign_mode'] == ApprovalNode.SIGN_ANY

        if required > 0 and approved_count >= required:
            node_status = 'approved'
        elif is_any:
            # 或签：仍有在办任务时保持 current；全员处理完且无人同意才判拒绝/阻塞
            if order == stock_out.current_node_order and has_active:
                node_status = 'current'
            elif order == stock_out.current_node_order:
                node_status = 'rejected' if rejected_count else 'blocked'
            elif (stock_out.current_node_order is not None
                  and order < stock_out.current_node_order):
                node_status = 'invalidated'
            else:
                node_status = 'waiting'
        elif rejected_count:
            node_status = 'rejected'
        elif order == stock_out.current_node_order:
            node_status = 'blocked' if not has_active else 'current'
        elif (stock_out.current_node_order is not None
              and order < stock_out.current_node_order):
            node_status = 'invalidated'
        else:
            node_status = 'waiting'

        task_view = []
        for t in tasks:
            if t.approver_id in designated_map.get(order, set()):
                source = '节点指定审批人'
            else:
                source = '在职管理员兜底'
            if t.replacement_for_id:
                source = f'{source}（回避替补）'
            if t.remark == '申请人本人，按规定自动回避':
                source = '申请人自动回避'
            elif t.remark == '管理员在节点无人可签时指派':
                source = '管理员指派'
            task_view.append({
                'task_id': t.id,
                'approver_id': t.approver_id,
                'approver_name': t.approver.real_name or t.approver.username,
                'username': t.approver.username,
                'status': t.status,
                'status_display': t.get_status_display(),
                'source': source,
                'remark': t.remark,
                'acted_at': t.acted_at,
            })

        nodes_view.append({
            'order': order,
            'name': node['name'],
            'sign_mode': node['sign_mode'],
            'sign_mode_display': node['sign_mode_display'],
            'status': node_status,
            'required_approvals': required,
            'approved_count': approved_count,
            'why': node['why'],
            'tasks': task_view,
        })

    evaluations = [
        {
            'type': e.type,
            'type_display': e.get_type_display(),
            'version': e.rule_version.version,
            'route_name': e.route.name,
            'conditions': e.conditions,
            'reasons': e.matched_reasons,
            'created_at': e.created_at,
        }
        for e in stock_out.evaluations.select_related('rule_version', 'route').order_by('created_at')
    ]

    superseded_generations = []
    if stock_out.route_generation > 1:
        for gen in range(1, stock_out.route_generation):
            gen_tasks = [
                {
                    'node_order': t.node_order,
                    'node_name': t.node_name,
                    'approver_name': t.approver.real_name or t.approver.username,
                    'status': t.status,
                    'status_display': t.get_status_display(),
                    'remark': t.remark,
                    'acted_at': t.acted_at,
                }
                for t in stock_out.approval_tasks.filter(generation=gen)
                .select_related('approver').order_by('node_order', 'id')
            ]
            superseded_generations.append({'generation': gen, 'tasks': gen_tasks})

    return {
        'rule_version': snapshot.get('version'),
        'route': snapshot.get('route'),
        'conditions': snapshot.get('conditions'),
        'matched_reasons': snapshot.get('reasons', []),
        'current_node_order': stock_out.current_node_order,
        'route_generation': stock_out.route_generation,
        'application_status': stock_out.status,
        'application_status_display': stock_out.get_status_display(),
        'nodes': nodes_view,
        'superseded_generations': superseded_generations,
        'evaluation_history': evaluations,
    }
