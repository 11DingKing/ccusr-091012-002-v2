from decimal import Decimal
from threading import Thread

from django.db import IntegrityError, connections
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from apps.authentication.backends import generate_token
from apps.authentication.models import User
from . import approval_engine
from .models import (
    ApprovalEvent, ApprovalRoleAssignment, ApprovalRuleVersion,
    ApprovalStep, Category, Goods, StepSigner, StockIn, StockOut, Unit, Variety, Warning,
)


def auth_client(user):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {generate_token(user)}")
    return client


class WarehouseFixture(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("admin-user", "testpass123", role="admin")
        self.client = auth_client(self.admin)
        self.unit = Unit.objects.create(name="件", created_by=self.admin)
        self.category = Category.objects.create(name="受控器材", unit=self.unit, created_by=self.admin)
        self.variety = Variety.objects.create(name="记录终端", category=self.category, created_by=self.admin)
        self.goods = Goods.objects.create(
            variety=self.variety,
            name="执法记录终端",
            code="DEV-001",
            quantity=Decimal("100"),
            warning_threshold=Decimal("5"),
        )


class WarehouseModelTest(WarehouseFixture):
    def test_relationship_flags(self):
        self.assertTrue(self.unit.is_linked)
        self.assertTrue(self.category.is_linked)
        self.assertTrue(self.variety.is_in_stock)
        self.assertFalse(self.goods.is_warning)

    def test_unique_unit_name(self):
        with self.assertRaises(IntegrityError):
            Unit.objects.create(name="件", created_by=self.admin)

    def test_stock_in_and_warning(self):
        inbound = StockIn.objects.create(goods=self.goods, operator=self.admin, quantity=Decimal("3"))
        warning = Warning.objects.create(goods=self.goods, type="low_stock", message="库存不足")
        self.assertEqual(inbound.goods_id, self.goods.id)
        self.assertFalse(warning.is_read)
        self.assertIn("执法记录终端", str(warning))


# ==================== 审批流测试夹具 ====================

class ApprovalFixture(TestCase):
    """发布默认规则 v1 并配置齐全的审批人"""

    def setUp(self):
        _build_approval_fixture(self)

    def apply(self, goods, quantity="2", dept="特警大队", purpose_type="use", purpose=""):
        stock_out = StockOut.objects.create(
            goods=goods, operator=self.applicant, receiver="领用员",
            receiver_dept=dept, quantity=Decimal(str(quantity)),
            purpose_type=purpose_type, purpose=purpose,
            status=StockOut.STATUS_DRAFT,
        )
        return approval_engine.instantiate_route(stock_out, actor=self.applicant)

    def step_names(self, stock_out):
        return list(stock_out.steps.order_by('order_index').values_list('name', flat=True))


def _build_approval_fixture(case):
    """共享夹具：TestCase 与 TransactionTestCase 均可使用"""
    # 申请人
    case.applicant = User.objects.create_user("applicant", "testpass123", real_name="张三", role="user")
    # 各角色审批人
    case.keeper = User.objects.create_user("keeper", "testpass123", real_name="库管员", role="admin")
    case.dept_head = User.objects.create_user("depthead", "testpass123", real_name="李队长", role="user")
    case.safety1 = User.objects.create_user("safety1", "testpass123", real_name="王安全", role="user")
    case.safety2 = User.objects.create_user("safety2", "testpass123", real_name="赵安全", role="user")
    case.director = User.objects.create_user("director", "testpass123", real_name="钱局长", role="user")
    case.other_admin = User.objects.create_user("otheradmin", "testpass123", real_name="改派人", role="admin")

    for role, user in [
        (approval_engine.ROLE_KEEPER, case.keeper),
        (approval_engine.ROLE_DEPT_HEAD, case.dept_head),
        (approval_engine.ROLE_SAFETY_OFFICER, case.safety1),
        (approval_engine.ROLE_SAFETY_OFFICER, case.safety2),
        (approval_engine.ROLE_DIRECTOR, case.director),
    ]:
        ApprovalRoleAssignment.objects.create(role_code=role, user=user, created_by=case.other_admin)

    case.rule = approval_engine.publish_version(
        approval_engine.DEFAULT_NODES, remark='初始规则', created_by=case.other_admin)

    unit = Unit.objects.create(name="件", created_by=case.other_admin)
    category = Category.objects.create(name="器材", unit=unit, created_by=case.other_admin)
    variety = Variety.objects.create(name="通用", category=category, created_by=case.other_admin)
    case.normal_goods = Goods.objects.create(
        variety=variety, name="复印纸", code="G-NORMAL",
        quantity=Decimal("100"), risk_level=Goods.RISK_NORMAL)
    case.controlled_goods = Goods.objects.create(
        variety=variety, name="对讲机", code="G-CTRL",
        quantity=Decimal("50"), risk_level=Goods.RISK_CONTROLLED)
    case.high_goods = Goods.objects.create(
        variety=variety, name="精密侦控设备", code="G-HIGH",
        quantity=Decimal("20"), risk_level=Goods.RISK_HIGH)
    return case


class RouteSelectionTest(ApprovalFixture):
    def test_normal_small_quantity_only_keeper(self):
        stock_out = self.apply(self.normal_goods, quantity=2)
        self.assertEqual(self.step_names(stock_out), ['保管员核准'])

    def test_controlled_adds_dept_head(self):
        stock_out = self.apply(self.controlled_goods, quantity=2)
        self.assertEqual(self.step_names(stock_out),
                         ['保管员核准', '领用部门负责人审批'])

    def test_high_risk_small_adds_safety_officer(self):
        stock_out = self.apply(self.high_goods, quantity=2)
        self.assertEqual(self.step_names(stock_out),
                         ['保管员核准', '领用部门负责人审批', '安全负责人会签'])

    def test_high_risk_bulk_adds_director(self):
        stock_out = self.apply(self.high_goods, quantity=10)
        self.assertEqual(self.step_names(stock_out),
                         ['保管员核准', '领用部门负责人审批', '安全负责人会签', '分管领导终审'])

    def test_destruction_adds_dept_head_and_safety_but_not_director_for_normal(self):
        stock_out = self.apply(self.normal_goods, quantity=2, purpose_type="destruction")
        self.assertEqual(self.step_names(stock_out),
                         ['保管员核准', '领用部门负责人审批', '安全负责人会签'])

    def test_quantity_threshold_triggers_dept_head(self):
        stock_out = self.apply(self.normal_goods, quantity=10)
        self.assertEqual(self.step_names(stock_out),
                         ['保管员核准', '领用部门负责人审批'])

    def test_happy_path_full_chain(self):
        stock_out = self.apply(self.high_goods, quantity=10)
        stock_out, _ = approval_engine.sign_approval(stock_out.id, self.keeper, 'approve')
        stock_out, _ = approval_engine.sign_approval(stock_out.id, self.dept_head, 'approve')
        stock_out, _ = approval_engine.sign_approval(stock_out.id, self.safety1, 'approve')
        stock_out, _ = approval_engine.sign_approval(stock_out.id, self.safety2, 'approve')
        stock_out, _ = approval_engine.sign_approval(stock_out.id, self.director, 'approve')
        self.assertEqual(stock_out.status, StockOut.STATUS_APPROVED)
        self.assertIsNone(stock_out.current_step_id)
        self.assertEqual(stock_out.steps.filter(status=ApprovalStep.STATUS_APPROVED).count(), 4)

    def test_signing_out_of_turn_rejected(self):
        stock_out = self.apply(self.controlled_goods, quantity=2)
        with self.assertRaises(approval_engine.ApprovalStateError):
            approval_engine.sign_approval(stock_out.id, self.dept_head, 'approve')

    def test_duplicate_signature_rejected(self):
        stock_out = self.apply(self.normal_goods, quantity=2)
        approval_engine.sign_approval(stock_out.id, self.keeper, 'approve')
        stock_out.refresh_from_db()
        self.assertEqual(stock_out.status, StockOut.STATUS_APPROVED)
        with self.assertRaises(approval_engine.ApprovalStateError):
            approval_engine.sign_approval(stock_out.id, self.keeper, 'approve')


class RuleSnapshotFreezeTest(ApprovalFixture):
    def test_rule_change_does_not_affect_inflight_application(self):
        # 在办：高风险小数量 → 保管员 + 部门负责人 + 安全负责人（v1）
        stock_out = self.apply(self.high_goods, quantity=2)
        self.assertEqual(stock_out.rule_version.version, 1)
        self.assertEqual(stock_out.steps.count(), 3)

        # 发布 v2：移除安全负责人节点，任何物资只需保管员
        v2_nodes = [{
            'code': 'keeper', 'name': '保管员核准', 'role': approval_engine.ROLE_KEEPER,
            'policy': 'all', 'conditions': [], 'reason': '全部仅需保管员',
        }]
        v2 = approval_engine.publish_version(v2_nodes, created_by=self.other_admin)
        self.assertEqual(v2.version, 2)
        stock_out.refresh_from_db()
        # 在办路线不变
        self.assertEqual(stock_out.rule_version.version, 1)
        self.assertEqual(stock_out.steps.count(), 3)
        self.assertEqual(stock_out.route_snapshot['rule_version'], 1)
        self.assertIn('安全负责人会签', self.step_names(stock_out))

        # 旧路线照常走通
        stock_out, _ = approval_engine.sign_approval(stock_out.id, self.keeper, 'approve')
        stock_out, _ = approval_engine.sign_approval(stock_out.id, self.dept_head, 'approve')
        stock_out, _ = approval_engine.sign_approval(stock_out.id, self.safety1, 'approve')
        stock_out, _ = approval_engine.sign_approval(stock_out.id, self.safety2, 'approve')
        self.assertEqual(stock_out.status, StockOut.STATUS_APPROVED)

        # 新申请按 v2 评估
        new_app = self.apply(self.high_goods, quantity=2)
        self.assertEqual(new_app.rule_version.version, 2)
        self.assertEqual(self.step_names(new_app), ['保管员核准'])

    def test_snapshot_freezes_role_membership(self):
        stock_out = self.apply(self.controlled_goods, quantity=2)
        members = stock_out.route_snapshot['role_members']
        self.assertEqual([m['id'] for m in members[approval_engine.ROLE_DEPT_HEAD]],
                         [self.dept_head.id])
        # 事后换人：在办路线仍发给旧人
        ApprovalRoleAssignment.objects.filter(
            role_code=approval_engine.ROLE_DEPT_HEAD).delete()
        new_head = User.objects.create_user("newhead", "testpass123", real_name="新队长")
        ApprovalRoleAssignment.objects.create(
            role_code=approval_engine.ROLE_DEPT_HEAD, user=new_head)
        signers = stock_out.steps.get(order_index=2).signers.all()
        self.assertEqual(list(signers.values_list('user_id', flat=True)), [self.dept_head.id])
        with self.assertRaises(approval_engine.ApprovalStateError):
            approval_engine.sign_approval(stock_out.id, new_head, 'approve')

    def test_missing_approver_blocks_submission(self):
        ApprovalRoleAssignment.objects.filter(
            role_code=approval_engine.ROLE_SAFETY_OFFICER).delete()
        with self.assertRaises(approval_engine.ApprovalConfigError):
            self.apply(self.high_goods, quantity=2)


class RecusalTest(ApprovalFixture):
    def test_auto_recusal_when_applicant_is_approver(self):
        # 申请人成为领用部门负责人的唯一在岗人：受控物资路线的第二节点创建即阻塞
        ApprovalRoleAssignment.objects.filter(
            role_code=approval_engine.ROLE_DEPT_HEAD).delete()
        ApprovalRoleAssignment.objects.create(
            role_code=approval_engine.ROLE_DEPT_HEAD, user=self.applicant)
        stock_out = self.apply(self.controlled_goods, quantity=2)

        blocked_step = stock_out.steps.get(order_index=2)
        self.assertEqual(blocked_step.status, ApprovalStep.STATUS_BLOCKED)
        own_signer = blocked_step.signers.get(user=self.applicant)
        self.assertEqual(own_signer.status, StepSigner.STATUS_RECUSED)
        self.assertEqual(own_signer.source, 'auto_recused')
        self.assertTrue(
            stock_out.events.filter(type=ApprovalEvent.TYPE_AUTO_RECUSE).exists())

        # 第一节点正常签署后进入阻塞节点
        approval_engine.sign_approval(stock_out.id, self.keeper, 'approve')
        stock_out.refresh_from_db()
        self.assertEqual(stock_out.current_step_id, blocked_step.id)

        # 阻塞期间不能签署
        with self.assertRaises(approval_engine.ApprovalStateError):
            approval_engine.sign_approval(stock_out.id, self.applicant, 'approve')

        # 管理员改派
        backup = User.objects.create_user("backup", "testpass123", real_name="备班队长")
        stock_out, _, _ = approval_engine.delegate(stock_out.id, self.other_admin, backup)
        blocked_step.refresh_from_db()
        self.assertEqual(blocked_step.status, ApprovalStep.STATUS_ACTIVE)
        stock_out, _ = approval_engine.sign_approval(stock_out.id, backup, 'approve')
        self.assertEqual(stock_out.status, StockOut.STATUS_APPROVED)

    def test_active_recusal_blocks_and_delegation_resumes(self):
        stock_out = self.apply(self.controlled_goods, quantity=2)
        approval_engine.sign_approval(stock_out.id, self.keeper, 'approve')
        stock_out.refresh_from_db()
        step2 = stock_out.steps.get(order_index=2)
        self.assertEqual(step2.status, ApprovalStep.STATUS_ACTIVE)

        # 唯一部门负责人主动回避 → 节点阻塞
        stock_out, step2 = approval_engine.recuse(stock_out.id, self.dept_head, '与领用人同部门，回避')
        self.assertEqual(step2.status, ApprovalStep.STATUS_BLOCKED)
        self.assertEqual(
            step2.signers.get(user=self.dept_head).source, 'active_recused')
        self.assertTrue(
            stock_out.events.filter(type=ApprovalEvent.TYPE_STEP_BLOCK).exists())

        # 不能改派给申请人
        with self.assertRaises(approval_engine.ApprovalStateError):
            approval_engine.delegate(stock_out.id, self.other_admin, self.applicant)

        # 改派新负责人后流程继续
        new_head = User.objects.create_user("head2", "testpass123", real_name="代队长")
        stock_out, _, _ = approval_engine.delegate(stock_out.id, self.other_admin, new_head)
        stock_out, _ = approval_engine.sign_approval(stock_out.id, new_head, 'approve')
        self.assertEqual(stock_out.status, StockOut.STATUS_APPROVED)

    def test_non_admin_cannot_delegate(self):
        stock_out = self.apply(self.controlled_goods, quantity=2)
        approval_engine.sign_approval(stock_out.id, self.keeper, 'approve')
        approval_engine.recuse(stock_out.id, self.dept_head)
        with self.assertRaises(approval_engine.ApprovalStateError):
            approval_engine.delegate(stock_out.id, self.applicant, self.other_admin)


class RejectResubmitTest(ApprovalFixture):
    def test_rejection_terminates_and_resubmit_creates_revision(self):
        stock_out = self.apply(self.controlled_goods, quantity=2)
        approval_engine.sign_approval(stock_out.id, self.keeper, 'approve')
        stock_out, _ = approval_engine.sign_approval(
            stock_out.id, self.dept_head, 'reject', comment='用途不清')
        self.assertEqual(stock_out.status, StockOut.STATUS_REJECTED)
        self.assertIsNone(stock_out.current_step_id)
        # 未开始的节点确定性终止
        self.assertTrue(stock_out.steps.filter(status=ApprovalStep.STATUS_REJECTED).exists())

        # 拒绝后重提：修订号 +1，新单关联原单
        new_app = approval_engine.resubmit(
            stock_out, self.applicant, quantity=Decimal("3"), purpose='已补充用途说明')
        self.assertEqual(new_app.id != stock_out.id, True)
        self.assertEqual(new_app.revision_no, 2)
        self.assertEqual(new_app.revised_from_id, stock_out.id)
        self.assertEqual(new_app.quantity, Decimal("3"))
        self.assertEqual(new_app.status, StockOut.STATUS_PENDING)
        self.assertTrue(
            new_app.events.filter(type=ApprovalEvent.TYPE_RESUBMIT).exists())
        # 原拒绝单保留
        self.assertEqual(StockOut.objects.get(pk=stock_out.id).status,
                         StockOut.STATUS_REJECTED)

    def test_resubmit_reevaluates_under_latest_rules(self):
        stock_out = self.apply(self.high_goods, quantity=2)  # v1: 保管员+部门+安全
        approval_engine.sign_approval(stock_out.id, self.keeper, 'approve')
        approval_engine.sign_approval(stock_out.id, self.dept_head, 'approve')
        approval_engine.sign_approval(stock_out.id, self.safety1, 'reject')
        stock_out.refresh_from_db()
        self.assertEqual(stock_out.status, StockOut.STATUS_REJECTED)

        # v2 增加规则：全部申请均需分管领导
        nodes = approval_engine.DEFAULT_NODES + [{
            'code': 'director_always', 'name': '分管领导终审',
            'role': approval_engine.ROLE_DIRECTOR, 'policy': 'all',
            'conditions': [], 'reason': '新规要求所有申请领导终审',
        }]
        approval_engine.publish_version(nodes, created_by=self.other_admin)

        new_app = approval_engine.resubmit(stock_out, self.applicant)
        self.assertEqual(new_app.rule_version.version, 2)
        self.assertIn('分管领导终审', self.step_names(new_app))

    def test_only_rejected_can_resubmit(self):
        stock_out = self.apply(self.normal_goods, quantity=2)
        with self.assertRaises(approval_engine.ApprovalStateError):
            approval_engine.resubmit(stock_out, self.applicant)


class ReevaluateTest(ApprovalFixture):
    def test_condition_change_reevaluates_route_before_any_signature(self):
        stock_out = self.apply(self.normal_goods, quantity=2)
        self.assertEqual(self.step_names(stock_out), ['保管员核准'])

        # 用途改为销毁 → 增加部门负责人、安全负责人
        stock_out = approval_engine.reevaluate(
            stock_out, self.applicant, purpose_type='destruction')
        self.assertEqual(self.step_names(stock_out),
                         ['保管员核准', '领用部门负责人审批', '安全负责人会签'])
        self.assertEqual(stock_out.rule_version.version, 1)  # 规则版本不变
        event = stock_out.events.get(type=ApprovalEvent.TYPE_REEVALUATE)
        self.assertIn('重新评估', event.detail)

    def test_reevaluate_stays_on_same_version_even_if_newer_exists(self):
        stock_out = self.apply(self.normal_goods, quantity=2)
        approval_engine.publish_version(
            [{'code': 'keeper', 'name': '保管员核准',
              'role': approval_engine.ROLE_KEEPER, 'policy': 'all',
              'conditions': [], 'reason': 'x'}],
            created_by=self.other_admin)
        stock_out = approval_engine.reevaluate(
            stock_out, self.applicant, quantity=Decimal("20"))
        self.assertEqual(stock_out.rule_version.version, 1)
        # 仍是 v1 规则：数量>=10 → 加部门负责人（v2 只有保管员）
        self.assertEqual(self.step_names(stock_out),
                         ['保管员核准', '领用部门负责人审批'])

    def test_reevaluate_forbidden_after_human_signature(self):
        stock_out = self.apply(self.normal_goods, quantity=2)
        approval_engine.sign_approval(stock_out.id, self.keeper, 'approve')
        stock_out.refresh_from_db()
        with self.assertRaises(approval_engine.ApprovalStateError):
            approval_engine.reevaluate(stock_out, self.applicant, quantity=Decimal("20"))

    def test_reevaluate_forbidden_after_active_recusal(self):
        stock_out = self.apply(self.controlled_goods, quantity=2)
        approval_engine.sign_approval(stock_out.id, self.keeper, 'approve')
        approval_engine.recuse(stock_out.id, self.dept_head)
        stock_out.refresh_from_db()
        with self.assertRaises(approval_engine.ApprovalStateError):
            approval_engine.reevaluate(stock_out, self.applicant, quantity=Decimal("1"))

    def test_reevaluate_drives_route_back_to_first_step(self):
        stock_out = self.apply(self.controlled_goods, quantity=2)
        approval_engine.sign_approval(stock_out.id, self.keeper, 'approve')
        # 尚未到下一节点签署前，数量改回阈值以下（仅保管员）需禁止：
        # 保管员已签署过 → 有人工签署
        stock_out.refresh_from_db()
        with self.assertRaises(approval_engine.ApprovalStateError):
            approval_engine.reevaluate(stock_out, self.applicant, quantity=Decimal("1"))


class CountersignPolicyTest(ApprovalFixture):
    def test_all_policy_requires_every_signer(self):
        stock_out = self.apply(self.high_goods, quantity=2)
        approval_engine.sign_approval(stock_out.id, self.keeper, 'approve')
        approval_engine.sign_approval(stock_out.id, self.dept_head, 'approve')
        stock_out.refresh_from_db()
        # 安全负责人 2 人会签：1 人通过后流程仍在安全节点
        approval_engine.sign_approval(stock_out.id, self.safety1, 'approve')
        stock_out.refresh_from_db()
        self.assertEqual(stock_out.status, StockOut.STATUS_PENDING)
        self.assertEqual(stock_out.current_step.name, '安全负责人会签')
        approval_engine.sign_approval(stock_out.id, self.safety2, 'approve')
        stock_out.refresh_from_db()
        self.assertEqual(stock_out.status, StockOut.STATUS_APPROVED)

    def test_reject_vote_does_not_veto_until_quorum_unreachable(self):
        # 构造 any 策略：任一安全负责人同意即可
        nodes = [n for n in approval_engine.DEFAULT_NODES if n['code'] != 'safety_officer']
        nodes.append({
            'code': 'safety_officer', 'name': '安全负责人会签',
            'role': approval_engine.ROLE_SAFETY_OFFICER, 'policy': 'any',
            'conditions': [{'field': 'risk_level', 'op': 'eq', 'value': 'high_risk'}],
            'reason': '任一安全负责人同意即可',
        })
        approval_engine.publish_version(nodes, created_by=self.other_admin)
        stock_out = self.apply(self.high_goods, quantity=2)
        approval_engine.sign_approval(stock_out.id, self.keeper, 'approve')
        approval_engine.sign_approval(stock_out.id, self.dept_head, 'approve')
        # safety1 拒绝：safety2 仍可同意，整单不否决
        stock_out, _ = approval_engine.sign_approval(
            stock_out.id, self.safety1, 'reject', comment='有疑问')
        self.assertEqual(stock_out.status, StockOut.STATUS_PENDING)
        stock_out, _ = approval_engine.sign_approval(stock_out.id, self.safety2, 'approve')
        self.assertEqual(stock_out.status, StockOut.STATUS_APPROVED)

    def test_any_policy_settles_other_signers_as_skipped(self):
        nodes = [n for n in approval_engine.DEFAULT_NODES if n['code'] != 'safety_officer']
        nodes.append({
            'code': 'safety_officer', 'name': '安全负责人会签',
            'role': approval_engine.ROLE_SAFETY_OFFICER, 'policy': 'any',
            'conditions': [{'field': 'risk_level', 'op': 'eq', 'value': 'high_risk'}],
            'reason': '任一安全负责人同意即可',
        })
        approval_engine.publish_version(nodes, created_by=self.other_admin)
        stock_out = self.apply(self.high_goods, quantity=2)
        approval_engine.sign_approval(stock_out.id, self.keeper, 'approve')
        approval_engine.sign_approval(stock_out.id, self.dept_head, 'approve')
        stock_out, _ = approval_engine.sign_approval(stock_out.id, self.safety1, 'approve')
        # 任一通过即放行：另一人状态确定为“无需签署”，不再滞留待签署
        self.assertEqual(stock_out.status, StockOut.STATUS_APPROVED)
        step = stock_out.steps.get(role_code=approval_engine.ROLE_SAFETY_OFFICER)
        self.assertEqual(
            step.signers.get(user=self.safety1).status, StepSigner.STATUS_APPROVED)
        self.assertEqual(
            step.signers.get(user=self.safety2).status, StepSigner.STATUS_SKIPPED)
        with self.assertRaises(approval_engine.ApprovalStateError):
            approval_engine.sign_approval(stock_out.id, self.safety2, 'approve')


class ConcurrentSigningTest(TransactionTestCase):
    """多人并发签署：两个安全负责人同时批准，结果必须确定且只完成一次"""

    def setUp(self):
        _build_approval_fixture(self)

    def _high_risk_application(self, quantity="2"):
        stock_out = StockOut.objects.create(
            goods=self.high_goods, operator=self.applicant, receiver="领用员",
            receiver_dept="特警大队", quantity=Decimal(quantity),
            purpose_type="use", status=StockOut.STATUS_DRAFT)
        return approval_engine.instantiate_route(stock_out, actor=self.applicant)

    def test_concurrent_approvals_are_deterministic(self):
        stock_out = self._high_risk_application()
        approval_engine.sign_approval(stock_out.id, self.keeper, 'approve')
        approval_engine.sign_approval(stock_out.id, self.dept_head, 'approve')

        errors = []

        def sign(user):
            try:
                approval_engine.sign_approval(stock_out.id, user, 'approve')
            except Exception as exc:  # noqa: BLE001
                errors.append((user.username, str(exc)))
            finally:
                connections.close_all()

        t1 = Thread(target=sign, args=(self.safety1,))
        t2 = Thread(target=sign, args=(self.safety2,))
        t1.start(); t2.start()
        t1.join(timeout=30); t2.join(timeout=30)
        self.assertFalse(t1.is_alive() or t2.is_alive(), '并发签署线程未结束')
        self.assertEqual(errors, [])

        stock_out = StockOut.objects.get(pk=stock_out.id)
        self.assertEqual(stock_out.status, StockOut.STATUS_APPROVED)
        self.assertIsNone(stock_out.current_step_id)
        safety_step = stock_out.steps.get(role_code=approval_engine.ROLE_SAFETY_OFFICER)
        self.assertEqual(safety_step.status, ApprovalStep.STATUS_APPROVED)
        self.assertEqual(
            safety_step.signers.filter(status=StepSigner.STATUS_APPROVED).count(), 2)
        # 完成事件恰好一条
        self.assertEqual(
            stock_out.events.filter(type=ApprovalEvent.TYPE_COMPLETE).count(), 1)

    def test_concurrent_duplicate_sign_by_same_user_counts_once(self):
        normal = StockOut.objects.create(
            goods=self.normal_goods, operator=self.applicant, receiver="领用员",
            receiver_dept="特警大队", quantity=Decimal("2"),
            status=StockOut.STATUS_DRAFT)
        normal = approval_engine.instantiate_route(normal, actor=self.applicant)
        results = []

        def sign():
            try:
                approval_engine.sign_approval(normal.id, self.keeper, 'approve')
                results.append('ok')
            except Exception as exc:  # noqa: BLE001
                results.append(f'err:{exc}')
            finally:
                connections.close_all()

        t1 = Thread(target=sign)
        t2 = Thread(target=sign)
        t1.start(); t2.start()
        t1.join(timeout=30); t2.join(timeout=30)
        self.assertEqual(sorted(results).count('ok'), 1)
        self.assertTrue(any(r.startswith('err:') for r in results))
        normal = StockOut.objects.get(pk=normal.id)
        self.assertEqual(normal.status, StockOut.STATUS_APPROVED)
        self.assertEqual(
            normal.events.filter(type=ApprovalEvent.TYPE_APPROVE).count(), 1)


class ExplanationTest(ApprovalFixture):
    def test_detail_explains_why_signatures_are_needed(self):
        stock_out = self.apply(self.high_goods, quantity=10, purpose_type='destruction')
        text = approval_engine.current_requirement(stock_out)
        self.assertIn('保管员核准', text)
        self.assertIn('待签署人', text)
        self.assertIn('库管员', text)

        snapshot = stock_out.route_snapshot
        reasons = {s['code']: s for s in snapshot['matched_steps']}
        self.assertIn('高风险', reasons['safety_officer']['reason'])
        self.assertTrue(reasons['director']['facts'])  # 命中条件留痕

        # 推进后当前说明依次切换节点
        approval_engine.sign_approval(stock_out.id, self.keeper, 'approve')
        stock_out.refresh_from_db()
        self.assertIn('领用部门负责人审批', approval_engine.current_requirement(stock_out))
        approval_engine.sign_approval(stock_out.id, self.dept_head, 'approve')
        stock_out.refresh_from_db()
        text = approval_engine.current_requirement(stock_out)
        self.assertIn('安全负责人会签', text)
        self.assertIn('王安全', text)
        self.assertIn('赵安全', text)

    def test_event_stream_records_full_history(self):
        stock_out = self.apply(self.controlled_goods, quantity=2)
        approval_engine.sign_approval(stock_out.id, self.keeper, 'approve', comment='核对无误')
        types = list(stock_out.events.order_by('id').values_list('type', flat=True))
        self.assertEqual(types[0], ApprovalEvent.TYPE_SUBMIT)
        self.assertIn(ApprovalEvent.TYPE_STEP_ACTIVATE, types)
        self.assertIn(ApprovalEvent.TYPE_APPROVE, types)


# ==================== API 测试 ====================

class StockOutAPITest(ApprovalFixture):
    def setUp(self):
        super().setUp()
        self.client = auth_client(self.applicant)

    def test_create_returns_instantiated_route_and_explanation(self):
        resp = self.client.post('/api/stock-out/', {
            'goods': self.high_goods.id, 'receiver': '领用员',
            'receiver_dept': '特警大队', 'quantity': '10',
            'purpose_type': 'use', 'purpose': '专项行动',
        }, format='json')
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()['data']
        self.assertEqual(len(data['steps']), 4)
        self.assertEqual(data['rule_version_number'], 1)
        explanation = data['route_explanation']
        self.assertIn('v1', explanation['summary'])
        self.assertEqual(len(explanation['why_these_signatures']), 4)
        self.assertIn('高风险', explanation['why_these_signatures'][1])
        self.assertIn('当前节点', explanation['current_requirement'])

    def test_full_flow_via_api(self):
        resp = self.client.post('/api/stock-out/', {
            'goods': self.controlled_goods.id, 'receiver': '领用员',
            'receiver_dept': '特警大队', 'quantity': '2',
        }, format='json')
        pk = resp.json()['data']['id']

        keeper_client = auth_client(self.keeper)
        resp = keeper_client.post(f'/api/stock-out/{pk}/sign/',
                                  {'action': 'approve'}, format='json')
        self.assertEqual(resp.status_code, 200)
        # 部门负责人拒绝
        head_client = auth_client(self.dept_head)
        resp = head_client.post(f'/api/stock-out/{pk}/sign/',
                                {'action': 'reject', 'comment': '材料不全'}, format='json')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['data']['status'], 'rejected')

        # 拒绝后重提（补用途）
        resp = self.client.post(f'/api/stock-out/{pk}/resubmit/',
                                {'purpose': '补充行动文号'}, format='json')
        self.assertEqual(resp.status_code, 200, resp.content)
        new_pk = resp.json()['data']['id']
        self.assertNotEqual(new_pk, pk)
        self.assertEqual(resp.json()['data']['revision_no'], 2)

    def test_recuse_and_delegate_via_api(self):
        resp = self.client.post('/api/stock-out/', {
            'goods': self.controlled_goods.id, 'receiver': '领用员',
            'receiver_dept': '特警大队', 'quantity': '2',
        }, format='json')
        pk = resp.json()['data']['id']
        auth_client(self.keeper).post(
            f'/api/stock-out/{pk}/sign/', {'action': 'approve'}, format='json')
        resp = auth_client(self.dept_head).post(
            f'/api/stock-out/{pk}/recuse/', {'comment': '回避'}, format='json')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('阻塞', resp.json()['message'])

        # 普通用户不能改派
        resp = self.client.post(f'/api/stock-out/{pk}/delegate/',
                                {'user': self.safety1.id}, format='json')
        self.assertEqual(resp.status_code, 403)

        new_head = User.objects.create_user("api-head", "testpass123", real_name="API代队长")
        ApprovalRoleAssignment.objects.create(
            role_code=approval_engine.ROLE_DEPT_HEAD, user=new_head)
        resp = auth_client(self.other_admin).post(
            f'/api/stock-out/{pk}/delegate/', {'user': new_head.id}, format='json')
        self.assertEqual(resp.status_code, 200)
        resp = auth_client(new_head).post(
            f'/api/stock-out/{pk}/sign/', {'action': 'approve'}, format='json')
        self.assertEqual(resp.json()['data']['status'], 'approved')

    def test_reevaluate_via_api_before_signing(self):
        resp = self.client.post('/api/stock-out/', {
            'goods': self.normal_goods.id, 'receiver': '领用员',
            'receiver_dept': '特警大队', 'quantity': '2',
        }, format='json')
        pk = resp.json()['data']['id']
        resp = self.client.post(f'/api/stock-out/{pk}/reevaluate/',
                                {'purpose_type': 'destruction'}, format='json')
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(len(resp.json()['data']['steps']), 3)

    def test_todo_scope_only_shows_current_signer(self):
        self.client.post('/api/stock-out/', {
            'goods': self.controlled_goods.id, 'receiver': '领用员',
            'receiver_dept': '特警大队', 'quantity': '2',
        }, format='json')
        resp = auth_client(self.dept_head).get('/api/stock-out/?scope=todo')
        # 保管员尚未签署，当前节点不是部门负责人，故无待办
        self.assertEqual(resp.json()['data']['total'], 0)
        auth_client(self.keeper).post(
            f'/api/stock-out/{StockOut.objects.latest("id").id}/sign/',
            {'action': 'approve'}, format='json')
        resp = auth_client(self.dept_head).get('/api/stock-out/?scope=todo')
        self.assertEqual(resp.json()['data']['total'], 1)

    def test_requires_authentication(self):
        resp = APIClient().get('/api/stock-out/')
        self.assertEqual(resp.status_code, 401)


class RuleVersionAPITest(ApprovalFixture):
    def test_non_admin_cannot_publish(self):
        resp = auth_client(self.applicant).post('/api/approval-rules/', {
            'nodes': approval_engine.DEFAULT_NODES,
        }, format='json')
        self.assertEqual(resp.status_code, 403)

    def test_admin_publish_validates_and_archives(self):
        resp = auth_client(self.other_admin).post('/api/approval-rules/', {
            'nodes': [{'code': 'bad', 'name': 'x', 'role': 'ghost', 'policy': 'all'}],
        }, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('审批角色非法', resp.json()['message'])

        resp = auth_client(self.other_admin).post('/api/approval-rules/', {
            'nodes': approval_engine.DEFAULT_NODES, 'remark': '收紧',
        }, format='json')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['data']['version'], 2)
        active = ApprovalRuleVersion.objects.get(version=1)
        self.assertEqual(active.status, ApprovalRuleVersion.STATUS_ARCHIVED)

    def test_role_assignment_crud(self):
        client = auth_client(self.other_admin)
        new_user = User.objects.create_user("rookie", "testpass123", real_name="新人")
        resp = client.post('/api/approval-assignees/', {
            'role_code': approval_engine.ROLE_DIRECTOR,
            'scope_dept': '特警大队', 'user': new_user.id,
        }, format='json')
        self.assertEqual(resp.status_code, 200)
        assignment_id = resp.json()['data']['id']
        resp = client.get('/api/approval-assignees/?role_code=director')
        self.assertGreaterEqual(resp.json()['data']['list'].__len__(), 1)
        resp = client.delete(f'/api/approval-assignees/{assignment_id}/')
        self.assertEqual(resp.status_code, 200)
