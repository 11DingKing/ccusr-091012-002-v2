from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework.test import APIClient

from apps.authentication.backends import generate_token
from apps.authentication.models import User
from . import approval
from .approval import ApprovalError
from .models import (
    ApprovalNode, ApprovalNodeApprover, ApprovalRoute, ApprovalRouteEvaluation,
    ApprovalRuleVersion, ApprovalTask, Category, Goods, StockIn, StockOut,
    Unit, Variety, Warning,
)


class WarehouseFixture(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("warehouse-user", "testpass123", role="admin")
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {generate_token(self.user)}")
        self.unit = Unit.objects.create(name="件", created_by=self.user)
        self.category = Category.objects.create(name="受控器材", unit=self.unit, created_by=self.user)
        self.variety = Variety.objects.create(name="记录终端", category=self.category, created_by=self.user)
        self.goods = Goods.objects.create(
            variety=self.variety,
            name="执法记录终端",
            code="DEV-001",
            quantity=Decimal("12"),
            warning_threshold=Decimal("5"),
        )

    def create_version_with_routes(self, fallback_nodes=('主管',), active=True):
        """生成一个含 普通/高风险 两条路线 + 兜底路线 的规则版本"""
        version = ApprovalRuleVersion.objects.create(version=1, created_by=self.user)
        common = dict(version=version)
        route_normal = ApprovalRoute.objects.create(
            name='普通耗材路线', risk_level=Goods.RISK_NORMAL,
            max_quantity=Decimal('10'), priority=10, **common,
        )
        route_high = ApprovalRoute.objects.create(
            name='高风险路线', risk_level=Goods.RISK_HIGH, priority=5, **common,
        )
        route_fallback = ApprovalRoute.objects.create(
            name='兜底路线', is_fallback=True, priority=999, **common,
        )
        for route, names in (
            (route_normal, ('主管',)),
            (route_high, ('安全负责人', '分管领导')),
            (route_fallback, fallback_nodes),
        ):
            for order, name in enumerate(names, start=1):
                ApprovalNode.objects.create(
                    route=route, name=name, order=order,
                    sign_mode=ApprovalNode.SIGN_SINGLE,
                )
        if active:
            approval.publish_version(version.id, self.user)
        return version, route_normal, route_high, route_fallback


class WarehouseModelTest(WarehouseFixture):
    def test_relationship_flags(self):
        self.assertTrue(self.unit.is_linked)
        self.assertTrue(self.category.is_linked)
        self.assertTrue(self.variety.is_in_stock)
        self.assertFalse(self.goods.is_warning)

    def test_unique_unit_name(self):
        with self.assertRaises(IntegrityError):
            Unit.objects.create(name="件", created_by=self.user)

    def test_stock_records(self):
        inbound = StockIn.objects.create(goods=self.goods, operator=self.user, quantity=Decimal("3"))
        outbound = StockOut.objects.create(
            goods=self.goods, operator=self.user, receiver="保管员", quantity=Decimal("2")
        )
        self.assertEqual(inbound.goods_id, self.goods.id)
        self.assertEqual(outbound.status, "pending")

    def test_goods_risk_level_default(self):
        self.assertEqual(self.goods.risk_level, Goods.RISK_NORMAL)

    def test_warning_record(self):
        warning = Warning.objects.create(goods=self.goods, type="low_stock", message="库存不足")
        self.assertFalse(warning.is_read)
        self.assertIn("执法记录终端", str(warning))

    def test_fallback_route_unique_per_version(self):
        version, _, _, fallback = self.create_version_with_routes(active=False)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ApprovalRoute.objects.create(
                    version=version, name='第二个兜底', is_fallback=True, priority=1,
                )


class WarehouseAPITest(WarehouseFixture):
    def test_list_units(self):
        response = self.client.get("/api/units/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["total"], 1)

    def test_create_unit_and_reject_duplicate(self):
        created = self.client.post("/api/units/", {"name": "箱"}, format="json")
        duplicate = self.client.post("/api/units/", {"name": "箱"}, format="json")
        self.assertEqual(created.status_code, 200)
        self.assertEqual(duplicate.status_code, 400)

    def test_update_linked_unit(self):
        response = self.client.put(f"/api/units/{self.unit.id}/", {"name": "台"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.name, "台")

    def test_refuse_delete_linked_unit(self):
        response = self.client.delete(f"/api/units/{self.unit.id}/")
        self.assertEqual(response.status_code, 400)
        self.assertTrue(Unit.objects.filter(pk=self.unit.id).exists())

    def test_create_category_validates_unit(self):
        ok = self.client.post("/api/categories/", {"name": "封存介质", "unit": self.unit.id}, format="json")
        bad = self.client.post("/api/categories/", {"name": "无效分类", "unit": 99999}, format="json")
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(bad.status_code, 400)

    def test_create_variety_and_duplicate_boundary(self):
        ok = self.client.post("/api/varieties/", {"name": "封存硬盘", "category": self.category.id}, format="json")
        duplicate = self.client.post("/api/varieties/", {"name": "封存硬盘", "category": self.category.id}, format="json")
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(duplicate.status_code, 400)

    def test_requires_authentication(self):
        anonymous = APIClient().get("/api/units/")
        self.assertEqual(anonymous.status_code, 401)


class ApprovalRuleVersionTest(WarehouseFixture):
    def _payload(self):
        return {
            'remark': '首版',
            'routes': [
                {
                    'name': '高风险双线',
                    'risk_level': 'high',
                    'priority': 1,
                    'nodes': [
                        {'name': '安全负责人', 'order': 1,
                         'approvers': [{'approver': self.user.id}]},
                    ],
                },
                {
                    'name': '兜底',
                    'is_fallback': True,
                    'priority': 999,
                    'nodes': [{'name': '管理员', 'order': 1}],
                },
            ],
        }

    def test_create_draft_publish_and_archive(self):
        resp = self.client.post('/api/approval-rules/', self._payload(), format='json')
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()['data']['version'], 1)

        # 发布
        pub = self.client.post('/api/approval-rules/1/publish/', format='json')
        self.assertEqual(pub.status_code, 200)
        self.assertEqual(pub.json()['data']['status'], 'active')

        # 发布新版本，旧版本自动归档
        payload = self._payload()
        payload['routes'][0]['name'] = '高风险三线'
        second = self.client.post('/api/approval-rules/', payload, format='json')
        self.client.post(f"/api/approval-rules/{second.json()['data']['id']}/publish/", format='json')
        self.assertEqual(
            ApprovalRuleVersion.objects.get(version=1).status,
            ApprovalRuleVersion.STATUS_ARCHIVED,
        )

    def test_published_version_is_immutable(self):
        self.client.post('/api/approval-rules/', self._payload(), format='json')
        self.client.post('/api/approval-rules/1/publish/', format='json')
        resp = self.client.put('/api/approval-rules/1/', {'remark': 'x'}, format='json')
        self.assertEqual(resp.status_code, 409)

    def test_publish_requires_exactly_one_fallback(self):
        payload = self._payload()
        payload['routes'] = [payload['routes'][0]]  # 去掉兜底
        resp = self.client.post('/api/approval-rules/', payload, format='json')
        version_id = resp.json()['data']['id']
        pub = self.client.post(f'/api/approval-rules/{version_id}/publish/', format='json')
        self.assertEqual(pub.status_code, 400)
        self.assertIn('兜底', pub.json()['message'])

    def test_non_admin_cannot_manage_rules(self):
        someone = User.objects.create_user('plain', 'pw', role='user')
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {generate_token(someone)}")
        resp = client.post('/api/approval-rules/', self._payload(), format='json')
        self.assertEqual(resp.status_code, 403)


class ApprovalFlowTest(WarehouseFixture):
    def setUp(self):
        super().setUp()
        self.chief = User.objects.create_user('chief', 'pw', real_name='王主管', role='admin')
        self.safety = User.objects.create_user('safety', 'pw', real_name='赵安全', role='admin')
        self.deputy = User.objects.create_user('deputy', 'pw', real_name='钱领导', role='admin')
        self.applicant = User.objects.create_user('applicant', 'pw', real_name='孙领用', role='user')
        self.create_version_with_routes()

        # 绑定指定审批人：普通线→主管；高风险→安全负责人 + 分管领导
        routes = ApprovalRoute.objects.filter(version__status='active')
        normal = routes.get(name='普通耗材路线')
        high = routes.get(name='高风险路线')
        ApprovalNodeApprover.objects.create(
            node=normal.nodes.get(order=1), approver=self.chief, order=1)
        ApprovalNodeApprover.objects.create(
            node=high.nodes.get(order=1), approver=self.safety, order=1)
        ApprovalNodeApprover.objects.create(
            node=high.nodes.get(order=2), approver=self.deputy, order=1)

    def _apply(self, risk=Goods.RISK_NORMAL, quantity='2', dept='', purpose='',
               user=None):
        self.goods.risk_level = risk
        self.goods.save(update_fields=['risk_level'])
        return approval.create_application(
            operator=user or self.applicant, goods=self.goods,
            quantity=Decimal(quantity), receiver='李四',
            receiver_dept=dept, purpose=purpose, remark='',
        )

    def test_route_selected_by_risk_and_snapshot_fixed(self):
        app = self._apply(risk=Goods.RISK_HIGH, quantity='2')
        self.assertEqual(app.rule_version.version, 1)
        self.assertEqual(app.route_snapshot['route']['name'], '高风险路线')
        self.assertEqual(app.current_node_order, 1)

        pending = app.approval_tasks.filter(status=ApprovalTask.STATUS_PENDING)
        self.assertEqual(set(pending.values_list('approver_id', flat=True)), {self.safety.id})
        # 后续节点处于 waiting
        self.assertTrue(app.approval_tasks.filter(
            node_order=2, status=ApprovalTask.STATUS_WAITING).exists())

        # 发布 v2 把高风险改为单人节点；在途申请仍按 v1 快照走两节点
        self._publish_v2_single_approver()
        self.assertEqual(app.rule_version.version, 1)
        approval.submit_decision(app.id, self.safety, 'approved')
        app.refresh_from_db()
        self.assertEqual(app.current_node_order, 2)
        self.assertTrue(app.approval_tasks.filter(
            node_order=2, approver=self.deputy, status='pending').exists())

    def _publish_v2_single_approver(self):
        v2 = ApprovalRuleVersion.objects.create(version=2, created_by=self.user)
        route = ApprovalRoute.objects.create(
            version=v2, name='高风险单线', risk_level=Goods.RISK_HIGH, priority=1)
        node = ApprovalNode.objects.create(route=route, name='安全负责人', order=1)
        ApprovalNodeApprover.objects.create(node=node, approver=self.safety, order=1)
        fallback = ApprovalRoute.objects.create(
            version=v2, name='兜底', is_fallback=True, priority=999)
        ApprovalNode.objects.create(route=fallback, name='主管', order=1)
        approval.publish_version(v2.id, self.user)

    def test_full_approval_then_complete_deducts_stock(self):
        app = self._apply(risk=Goods.RISK_HIGH)
        approval.submit_decision(app.id, self.safety, 'approved', '同意')
        approval.submit_decision(app.id, self.deputy, 'approved', '同意')
        app.refresh_from_db()
        self.assertEqual(app.status, StockOut.STATUS_APPROVED)

        approval.complete_application(app.id, self.user)
        self.goods.refresh_from_db()
        app.refresh_from_db()
        self.assertEqual(app.status, StockOut.STATUS_COMPLETED)
        self.assertEqual(self.goods.quantity, Decimal('10'))
        self.assertIsNotNone(app.stock_out_time)

    def test_rejection_blocks_remaining_nodes(self):
        app = self._apply(risk=Goods.RISK_HIGH)
        approval.submit_decision(app.id, self.safety, 'rejected', '材料不全')
        app.refresh_from_db()
        self.assertEqual(app.status, StockOut.STATUS_REJECTED)
        # 第二节点任务被取消
        self.assertTrue(app.approval_tasks.filter(
            node_order=2, status=ApprovalTask.STATUS_CANCELLED).exists())

        # 已结束申请不能再签
        with self.assertRaises(ApprovalError) as ctx:
            approval.submit_decision(app.id, self.deputy, 'approved')
        self.assertEqual(ctx.exception.code, 409)

    def test_reject_then_resubmit_uses_current_version(self):
        app = self._apply(risk=Goods.RISK_HIGH)
        approval.submit_decision(app.id, self.safety, 'rejected', '缺批件')
        new_app = approval.resubmit_application(app.id, user=self.applicant)
        self.assertEqual(new_app.resubmitted_from_id, app.id)
        self.assertEqual(new_app.status, StockOut.STATUS_PENDING)
        latest_eval = new_app.evaluations.latest('created_at')
        self.assertEqual(latest_eval.type, ApprovalRouteEvaluation.TYPE_RESUBMIT)

        # v2 生效后重提 → 新申请按 v2，旧申请仍是 v1
        self._publish_v2_single_approver()
        new_app2 = approval.resubmit_application(app.id, user=self.applicant)
        self.assertEqual(new_app2.rule_version.version, 2)
        app.refresh_from_db()
        self.assertEqual(app.rule_version.version, 1)

    def test_manual_recuse_falls_back_to_designated_then_admin(self):
        # 高风险首节点指定两人：安全负责人优先，再增一名备用审批人
        routes = ApprovalRoute.objects.get(name='高风险路线')
        first_node = routes.nodes.get(order=1)
        backup = User.objects.create_user('backup', 'pw', real_name='周备份', role='admin')
        ApprovalNodeApprover.objects.create(node=first_node, approver=backup, order=2)

        app = self._apply(risk=Goods.RISK_HIGH)
        pending = app.approval_tasks.filter(status='pending')
        self.assertEqual(pending.count(), 1)
        self.assertEqual(pending.first().approver_id, self.safety.id)

        # 安全负责人主动回避 → 指定顺序的下一位补位（不是管理员）
        approval.submit_decision(app.id, self.safety, 'recused', '与领用部门有利害关系')
        app.refresh_from_db()
        new_task = app.approval_tasks.get(status='pending', node_order=1)
        self.assertEqual(new_task.approver_id, backup.id)
        self.assertTrue(new_task.replacement_for_id)
        self.assertEqual(app.approval_tasks.get(approver=self.safety).status,
                         ApprovalTask.STATUS_RECUSED)

    def test_auto_recusal_when_applicant_is_approver(self):
        # 兜底节点关闭管理员兜底会导致 blocked；这里让申请人恰好是管理员兜底人
        # 用普通路线（指定主管），再让申请人本人作为唯一指定审批人
        normal = ApprovalRoute.objects.get(name='普通耗材路线')
        node = normal.nodes.get(order=1)
        node.approvers.all().delete()
        ApprovalNodeApprover.objects.create(node=node, approver=self.applicant, order=1)
        node.use_active_admins = False
        node.save(update_fields=['use_active_admins'])

        app = self._apply(risk=Goods.RISK_NORMAL)
        self.assertEqual(app.status, StockOut.STATUS_BLOCKED)
        recused = app.approval_tasks.get(approver=self.applicant)
        self.assertEqual(recused.status, ApprovalTask.STATUS_RECUSED)
        self.assertIn('自动回避', recused.remark)

    def test_concurrent_all_sign_mode_is_deterministic(self):
        high = ApprovalRoute.objects.get(name='高风险路线')
        first = high.nodes.get(order=1)
        first.sign_mode = ApprovalNode.SIGN_ALL
        first.save(update_fields=['sign_mode'])
        extra = User.objects.create_user('extra', 'pw', real_name='吴委员', role='admin')
        ApprovalNodeApprover.objects.create(node=first, approver=extra, order=2)

        app = self._apply(risk=Goods.RISK_HIGH)
        tasks = list(app.approval_tasks.filter(node_order=1, status='pending'))
        self.assertEqual({t.approver_id for t in tasks}, {self.safety.id, extra.id})

        approval.submit_decision(app.id, self.safety, 'approved')
        app.refresh_from_db()
        # 一人会签尚未通过，第二节点不激活
        self.assertEqual(app.current_node_order, 1)
        self.assertFalse(app.approval_tasks.filter(node_order=2, status='pending').exists())

        # 重复签署 → 409
        with self.assertRaises(ApprovalError) as ctx:
            approval.submit_decision(app.id, self.safety, 'approved')
        self.assertEqual(ctx.exception.code, 409)

        approval.submit_decision(app.id, extra, 'approved')
        app.refresh_from_db()
        self.assertEqual(app.current_node_order, 2)

    def test_any_sign_mode_passes_with_one_approval(self):
        high = ApprovalRoute.objects.get(name='高风险路线')
        first = high.nodes.get(order=1)
        first.sign_mode = ApprovalNode.SIGN_ANY
        first.save(update_fields=['sign_mode'])
        extra = User.objects.create_user('any2', 'pw', real_name='郑或签', role='admin')
        ApprovalNodeApprover.objects.create(node=first, approver=extra, order=2)

        app = self._apply(risk=Goods.RISK_HIGH)
        approval.submit_decision(app.id, extra, 'approved')
        app.refresh_from_db()
        self.assertEqual(app.current_node_order, 2)
        # 同节点其他人任务被取消
        self.assertTrue(app.approval_tasks.filter(
            node_order=1, approver=self.safety, status='cancelled').exists())

    def test_any_sign_rejection_does_not_veto_others(self):
        high = ApprovalRoute.objects.get(name='高风险路线')
        first = high.nodes.get(order=1)
        first.sign_mode = ApprovalNode.SIGN_ANY
        first.save(update_fields=['sign_mode'])
        extra = User.objects.create_user('any3', 'pw', real_name='冯或签', role='admin')
        ApprovalNodeApprover.objects.create(node=first, approver=extra, order=2)

        app = self._apply(risk=Goods.RISK_HIGH)
        # 一人拒绝：申请继续，第二节点仍未激活
        approval.submit_decision(app.id, self.safety, 'rejected', '我有异议')
        app.refresh_from_db()
        self.assertEqual(app.status, StockOut.STATUS_PENDING)
        self.assertEqual(app.current_node_order, 1)
        # 另一人同意 → 节点通过，流程继续
        approval.submit_decision(app.id, extra, 'approved')
        app.refresh_from_db()
        self.assertEqual(app.current_node_order, 2)

        # 全员拒绝/处理完且无人同意 → 申请拒绝
        app2 = self._apply(risk=Goods.RISK_HIGH)
        approval.submit_decision(app2.id, self.safety, 'rejected', '否')
        approval.submit_decision(app2.id, extra, 'rejected', '否')
        app2.refresh_from_db()
        self.assertEqual(app2.status, StockOut.STATUS_REJECTED)

    def test_quantity_and_dept_and_purpose_matching(self):
        version = ApprovalRuleVersion.objects.create(version=2, created_by=self.user)
        special = ApprovalRoute.objects.create(
            version=version, name='刑侦大批量报废', risk_level=Goods.RISK_ELEVATED,
            min_quantity=Decimal('5'), receiver_dept='刑侦支队', purpose='scrap',
            priority=1,
        )
        ApprovalNode.objects.create(route=special, name='支队长', order=1)
        fallback = ApprovalRoute.objects.create(
            version=version, name='兜底', is_fallback=True, priority=999)
        ApprovalNode.objects.create(route=fallback, name='主管', order=1)
        approval.publish_version(version.id, self.user)

        self.goods.risk_level = Goods.RISK_ELEVATED
        self.goods.save(update_fields=['risk_level'])

        matched = approval.create_application(
            operator=self.applicant, goods=self.goods, quantity=Decimal('6'),
            receiver='李四', receiver_dept='刑侦支队', purpose='scrap',
        )
        self.assertEqual(matched.route_snapshot['route']['name'], '刑侦大批量报废')

        # 数量不达门槛 → 不命中
        fallback_app = approval.create_application(
            operator=self.applicant, goods=self.goods, quantity=Decimal('4'),
            receiver='李四', receiver_dept='刑侦支队', purpose='scrap',
        )
        self.assertEqual(fallback_app.route_snapshot['route']['name'], '兜底')

        # 部门不符 → 不命中
        other_dept = approval.create_application(
            operator=self.applicant, goods=self.goods, quantity=Decimal('6'),
            receiver='李四', receiver_dept='交警支队', purpose='scrap',
        )
        self.assertEqual(other_dept.route_snapshot['route']['name'], '兜底')

    def test_reevaluate_same_route_keeps_tasks(self):
        app = self._apply(risk=Goods.RISK_NORMAL, quantity='2')
        task_id = app.approval_tasks.get(status='pending').id
        approval.reevaluate_application(app.id, user=self.applicant,
                                        changes={'quantity': Decimal('3')})
        app.refresh_from_db()
        self.assertEqual(app.route_generation, 1)
        self.assertTrue(ApprovalTask.objects.filter(id=task_id, status='pending').exists())
        self.assertEqual(app.evaluations.filter(
            type=ApprovalRouteEvaluation.TYPE_REEVALUATE).count(), 1)

    def test_reevaluate_different_route_increments_generation(self):
        app = self._apply(risk=Goods.RISK_NORMAL, quantity='2')
        approval.submit_decision(app.id, self.chief, 'approved')
        app.refresh_from_db()
        self.assertEqual(app.status, StockOut.STATUS_APPROVED)

        # 已结束不能改
        with self.assertRaises(ApprovalError):
            approval.reevaluate_application(app.id, user=self.applicant,
                                            changes={'quantity': Decimal('20')})

        # 新申请：普通线已批，改数量到阈值外（普通线 max=10）→ 落兜底（单节点，沿用批准）
        app2 = self._apply(risk=Goods.RISK_NORMAL, quantity='2')
        approval.submit_decision(app2.id, self.chief, 'approved')
        # 此时已全部通过；直接验证在途场景：改高风险物资换线
        app3 = self._apply(risk=Goods.RISK_NORMAL, quantity='2')
        self.assertEqual(app3.route_generation, 1)
        self.goods.risk_level = Goods.RISK_HIGH
        self.goods.save(update_fields=['risk_level'])
        approval.reevaluate_application(
            app3.id, user=self.applicant,
            changes={'goods': self.goods, 'quantity': Decimal('2')},
        )
        app3.refresh_from_db()
        self.assertEqual(app3.route_generation, 2)
        self.assertEqual(app3.route_snapshot['route']['name'], '高风险路线')
        self.assertEqual(app3.current_node_order, 1)
        self.assertTrue(app3.approval_tasks.filter(
            generation=2, approver=self.safety, status='pending').exists())
        # 第一代任务保留为审计痕迹
        self.assertTrue(app3.approval_tasks.filter(
            generation=1, approver=self.chief, status='cancelled').exists())

    def test_blocked_node_can_be_rescued_by_admin_assignment(self):
        normal = ApprovalRoute.objects.get(name='普通耗材路线')
        node = normal.nodes.get(order=1)
        node.approvers.all().delete()
        node.use_active_admins = False
        node.save(update_fields=['use_active_admins'])

        app = self._apply(risk=Goods.RISK_NORMAL)
        self.assertEqual(app.status, StockOut.STATUS_BLOCKED)

        # 普通用户无权指派
        with self.assertRaises(ApprovalError) as ctx:
            approval.assign_blocked_approver(app.id, self.applicant, self.chief.id)
        self.assertEqual(ctx.exception.code, 403)

        approval.assign_blocked_approver(app.id, self.user, self.chief.id)
        app.refresh_from_db()
        self.assertEqual(app.status, StockOut.STATUS_PENDING)
        self.assertEqual(app.approval_tasks.filter(
            status='pending', approver=self.chief).count(), 1)
        approval.submit_decision(app.id, self.chief, 'approved')
        app.refresh_from_db()
        self.assertEqual(app.status, StockOut.STATUS_APPROVED)

    def test_explain_application_lists_reasons_and_tasks(self):
        app = self._apply(risk=Goods.RISK_HIGH)
        detail = approval.explain_application(app)
        self.assertEqual(detail['rule_version']['version'], 1)
        self.assertTrue(detail['matched_reasons'])
        self.assertTrue(any(r['field'] == 'risk_level' for r in detail['matched_reasons']))
        self.assertEqual(len(detail['nodes']), 2)
        current = next(n for n in detail['nodes'] if n['status'] == 'current')
        self.assertEqual(current['name'], '安全负责人')
        self.assertIn('单人签署', current['why'])
        task = current['tasks'][0]
        self.assertEqual(task['source'], '节点指定审批人')
        self.assertEqual(task['approver_name'], '赵安全')

        # 评估历史可解释每次选路
        self.assertEqual(len(detail['evaluation_history']), 1)


class ApprovalAPIFlowTest(WarehouseFixture):
    def setUp(self):
        super().setUp()
        self.chief = User.objects.create_user('chief2', 'pw', role='admin')
        self.applicant = User.objects.create_user('applicant2', 'pw', role='user')
        self.create_version_with_routes()
        normal = ApprovalRoute.objects.get(name='普通耗材路线')
        ApprovalNodeApprover.objects.create(
            node=normal.nodes.get(order=1), approver=self.chief, order=1)
        self.goods.risk_level = Goods.RISK_NORMAL
        self.goods.quantity = Decimal('100')
        self.goods.save()

    def _auth(self, user):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {generate_token(user)}")
        return client

    def test_create_detail_decision_complete_flow(self):
        client = self._auth(self.applicant)
        resp = client.post('/api/stock-out/', {
            'goods': self.goods.id, 'quantity': '5', 'receiver': '李四',
            'receiver_dept': '一中队', 'purpose': 'daily_duty',
        }, format='json')
        self.assertEqual(resp.status_code, 200, resp.content)
        app_id = resp.json()['data']['id']
        self.assertEqual(resp.json()['data']['route_name'], '普通耗材路线')

        detail = client.get(f'/api/stock-out/{app_id}/')
        self.assertEqual(detail.status_code, 200)
        self.assertIn('approval', detail.json()['data'])

        # 非审批人不能签
        forbidden = client.post(
            f'/api/stock-out/{app_id}/decision/',
            {'action': 'approved'}, format='json')
        self.assertEqual(forbidden.status_code, 403)

        chief_client = self._auth(self.chief)
        # 拒绝必须填意见
        no_remark = chief_client.post(
            f'/api/stock-out/{app_id}/decision/',
            {'action': 'rejected'}, format='json')
        self.assertEqual(no_remark.status_code, 400)

        ok = chief_client.post(
            f'/api/stock-out/{app_id}/decision/',
            {'action': 'approved', 'remark': '同意'}, format='json')
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()['data']['status'], 'approved')

        complete = chief_client.post(
            f'/api/stock-out/{app_id}/complete/', format='json')
        self.assertEqual(complete.status_code, 200)

    def test_todo_list_only_shows_pending_tasks(self):
        client = self._auth(self.applicant)
        resp = client.post('/api/stock-out/', {
            'goods': self.goods.id, 'quantity': '1', 'receiver': '李四',
        }, format='json')
        app_id = resp.json()['data']['id']

        chief_client = self._auth(self.chief)
        todo = chief_client.get('/api/approval-tasks/')
        self.assertEqual(todo.json()['data']['total'], 1)
        self.assertEqual(todo.json()['data']['list'][0]['stock_out'], app_id)

        other = self._auth(
            User.objects.create_user('bystander', 'pw', role='admin'))
        self.assertEqual(other.get('/api/approval-tasks/').json()['data']['total'], 0)

    def test_preview_route(self):
        client = self._auth(self.applicant)
        resp = client.post('/api/stock-out/preview-route/', {
            'goods': self.goods.id, 'quantity': '1', 'receiver': '李四',
        }, format='json')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['data']['route']['name'], '普通耗材路线')
