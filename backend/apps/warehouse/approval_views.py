"""
出库申请与版本化审批路线视图
"""
import logging

from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from apps.core.response import success_response, error_response
from . import approval
from .approval import ApprovalError
from .models import (
    ApprovalNode, ApprovalNodeApprover, ApprovalRoute, ApprovalRuleVersion,
    ApprovalTask, Goods, StockOut,
)
from .serializers import (
    ApprovalTaskSerializer, DecisionSerializer,
    RuleVersionOutputSerializer, RuleVersionWriteSerializer,
    StockOutCreateSerializer, StockOutReevaluateSerializer,
    StockOutResubmitSerializer, StockOutSerializer,
)

logger = logging.getLogger('apps')


def _client_error(exc):
    return error_response(message=exc.message, code=exc.code)


def _is_admin(user):
    return bool(user.is_authenticated and user.is_admin)


def _first_serializer_error(serializer):
    errors = serializer.errors
    value = list(errors.values())[0]
    if isinstance(value, list):
        value = value[0]
    if isinstance(value, dict):
        value = list(value.values())[0]
        if isinstance(value, list):
            value = value[0]
    return str(value)


# ==================== 出库申请 ====================

class StockOutListView(APIView):
    """出库申请：列表 / 创建（创建即固化规则快照）"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        queryset = (
            StockOut.objects.select_related('goods', 'operator', 'rule_version')
            .order_by('-created_at')
        )
        status = request.query_params.get('status')
        if status:
            queryset = queryset.filter(status=status)
        scope = request.query_params.get('scope')
        if scope == 'mine':
            queryset = queryset.filter(operator=request.user)

        page = max(int(request.query_params.get('page', 1)), 1)
        page_size = max(int(request.query_params.get('page_size', 10)), 1)
        total = queryset.count()
        items = queryset[(page - 1) * page_size:page * page_size]
        data = StockOutSerializer(
            items, many=True, context={'request': request}
        ).data
        return success_response(data={
            'list': data, 'total': total, 'page': page, 'page_size': page_size,
        })

    def post(self, request):
        serializer = StockOutCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(message=_first_serializer_error(serializer))
        data = serializer.validated_data
        goods = Goods.objects.get(pk=data['goods'])
        try:
            stock_out = approval.create_application(
                operator=request.user, goods=goods, quantity=data['quantity'],
                receiver=data['receiver'], receiver_dept=data.get('receiver_dept', ''),
                purpose=data.get('purpose', ''), remark=data.get('remark', ''),
            )
        except ApprovalError as exc:
            return _client_error(exc)

        logger.info(
            'user %s created stock-out %s, rule version %s, route %s',
            request.user.username, stock_out.id,
            stock_out.rule_version_id,
            stock_out.route_snapshot.get('route', {}).get('name'),
        )
        stock_out = StockOut.objects.select_related('goods', 'operator').get(pk=stock_out.id)
        return success_response(
            data=StockOutSerializer(stock_out, context={'request': request}).data,
            message='申请已创建，审批路线已固定',
        )


class StockOutDetailView(APIView):
    """申请详情：含路线命中原因与逐节点签署解释"""
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        stock_out = get_object_or_404(
            StockOut.objects.select_related(
                'goods', 'operator', 'rule_version'), pk=pk
        )
        data = StockOutSerializer(stock_out, context={'request': request}).data
        data['approval'] = approval.explain_application(stock_out)
        return success_response(data=data)


class StockOutDecisionView(APIView):
    """审批人签署：approved / rejected / recused"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        serializer = DecisionSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(message=_first_serializer_error(serializer))
        try:
            stock_out = approval.submit_decision(
                pk, request.user,
                action=serializer.validated_data['action'],
                remark=serializer.validated_data.get('remark', ''),
            )
        except ApprovalError as exc:
            return _client_error(exc)
        except StockOut.DoesNotExist:
            return error_response(message='申请不存在', code=404)

        logger.info(
            'user %s %s stock-out %s',
            request.user.username, serializer.validated_data['action'], pk,
        )
        return success_response(
            data=StockOutSerializer(stock_out, context={'request': request}).data,
            message='签署完成',
        )


class StockOutReevaluateView(APIView):
    """条件变化：固定版本内重新评估路线"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        serializer = StockOutReevaluateSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(message=_first_serializer_error(serializer))
        changes = serializer.validated_data
        if 'goods' in changes:
            changes['goods'] = Goods.objects.get(pk=changes['goods'])
        try:
            stock_out = approval.reevaluate_application(
                pk, user=request.user, changes=changes,
            )
        except ApprovalError as exc:
            return _client_error(exc)
        except StockOut.DoesNotExist:
            return error_response(message='申请不存在', code=404)

        logger.info('user %s reevaluated stock-out %s', request.user.username, pk)
        return success_response(
            data=StockOutSerializer(stock_out, context={'request': request}).data,
            message='已按新条件重新评估审批路线',
        )


class StockOutResubmitView(APIView):
    """拒绝后重提：按当前生效版本全新快照"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        serializer = StockOutResubmitSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(message=_first_serializer_error(serializer))
        overrides = dict(serializer.validated_data)
        if 'goods' in overrides:
            overrides['goods'] = Goods.objects.get(pk=overrides['goods'])
        try:
            new_app = approval.resubmit_application(
                pk, user=request.user, overrides=overrides,
            )
        except ApprovalError as exc:
            return _client_error(exc)
        except StockOut.DoesNotExist:
            return error_response(message='原申请不存在', code=404)

        logger.info(
            'user %s resubmitted stock-out %s as %s',
            request.user.username, pk, new_app.id,
        )
        return success_response(
            data=StockOutSerializer(new_app, context={'request': request}).data,
            message='已按当前生效规则重新提交申请',
        )


class StockOutAssignApproverView(APIView):
    """blocked 恢复：管理员为阻塞节点指派替补审批人"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        approver_id = request.data.get('approver')
        if not approver_id:
            return error_response(message='请选择审批人')
        try:
            stock_out = approval.assign_blocked_approver(
                pk, request.user, int(approver_id),
            )
        except ApprovalError as exc:
            return _client_error(exc)
        except StockOut.DoesNotExist:
            return error_response(message='申请不存在', code=404)
        logger.info('admin %s assigned approver for stock-out %s',
                    request.user.username, pk)
        return success_response(
            data=StockOutSerializer(stock_out, context={'request': request}).data,
            message='已指派审批人，流程恢复',
        )


class StockOutCompleteView(APIView):
    """审批通过后放行出库并扣减库存"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        try:
            stock_out = approval.complete_application(pk, request.user)
        except ApprovalError as exc:
            return _client_error(exc)
        except StockOut.DoesNotExist:
            return error_response(message='申请不存在', code=404)
        logger.info('stock-out %s completed, quantity deducted', pk)
        return success_response(
            data=StockOutSerializer(stock_out, context={'request': request}).data,
            message='出库完成',
        )


class StockOutPreviewRouteView(APIView):
    """预演：不创建申请，仅展示当前生效版本会命中哪条路线"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = StockOutCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(message=_first_serializer_error(serializer))
        data = serializer.validated_data
        goods = Goods.objects.get(pk=data['goods'])
        version = approval.get_active_version()
        if version is None:
            return error_response(message='当前没有生效的审批规则版本')
        conditions = approval.build_conditions(
            goods, data['quantity'],
            data.get('receiver_dept', ''), data.get('purpose', ''),
        )
        route, reasons = approval.match_route(version, conditions)
        return success_response(data={
            'version': version.version,
            'route': {'id': route.id, 'name': route.name},
            'conditions': conditions,
            'reasons': reasons,
        })


# ==================== 待办与签署任务 ====================

class ApprovalTaskListView(APIView):
    """签署任务：默认返回待当前登录人处理的任务"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        queryset = (
            ApprovalTask.objects.select_related('approver', 'stock_out', 'stock_out__goods')
            .order_by('node_order', '-created_at')
        )
        scope = request.query_params.get('scope', 'todo')
        if scope == 'todo':
            queryset = queryset.filter(
                approver=request.user, status=ApprovalTask.STATUS_PENDING,
            )
        elif scope == 'mine':
            queryset = queryset.filter(approver=request.user)
        stock_out_id = request.query_params.get('stock_out')
        if stock_out_id:
            queryset = queryset.filter(stock_out_id=stock_out_id)

        page = max(int(request.query_params.get('page', 1)), 1)
        page_size = max(int(request.query_params.get('page_size', 10)), 1)
        total = queryset.count()
        items = queryset[(page - 1) * page_size:page * page_size]
        return success_response(data={
            'list': ApprovalTaskSerializer(items, many=True).data,
            'total': total, 'page': page, 'page_size': page_size,
        })


# ==================== 审批规则版本管理 ====================

def _replace_version_routes(version, routes_data):
    """草稿版本的整单路线替换（发布后不可调用）"""
    version.routes.all().delete()
    for route_data in routes_data:
        route = ApprovalRoute.objects.create(
            version=version, name=route_data['name'],
            risk_level=route_data.get('risk_level', ''),
            min_quantity=route_data.get('min_quantity'),
            max_quantity=route_data.get('max_quantity'),
            receiver_dept=route_data.get('receiver_dept', ''),
            purpose=route_data.get('purpose', ''),
            priority=route_data.get('priority', 100),
            is_fallback=route_data.get('is_fallback', False),
            is_active=route_data.get('is_active', True),
        )
        for node_data in route_data['nodes']:
            node = ApprovalNode.objects.create(
                route=route, name=node_data['name'], order=node_data['order'],
                sign_mode=node_data.get('sign_mode', 'single'),
                use_active_admins=node_data.get('use_active_admins', True),
            )
            for idx, approver_data in enumerate(node_data.get('approvers', []), start=1):
                ApprovalNodeApprover.objects.create(
                    node=node, approver_id=approver_data['approver'],
                    order=approver_data.get('order', idx),
                )


class RuleVersionListCreateView(APIView):
    """规则版本：列表 / 新建草稿（可携带完整路线定义）"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        queryset = ApprovalRuleVersion.objects.prefetch_related(
            'routes__nodes__approvers__approver'
        ).order_by('-version')
        page = max(int(request.query_params.get('page', 1)), 1)
        page_size = max(int(request.query_params.get('page_size', 10)), 1)
        total = queryset.count()
        items = queryset[(page - 1) * page_size:page * page_size]
        return success_response(data={
            'list': RuleVersionOutputSerializer(items, many=True).data,
            'total': total, 'page': page, 'page_size': page_size,
        })

    def post(self, request):
        if not _is_admin(request.user):
            return error_response(message='仅管理员可维护审批规则', code=403)
        serializer = RuleVersionWriteSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(message=_first_serializer_error(serializer))

        with transaction.atomic():
            last = ApprovalRuleVersion.objects.select_for_update().order_by('-version').first()
            version = ApprovalRuleVersion.objects.create(
                version=(last.version + 1) if last else 1,
                remark=serializer.validated_data.get('remark', ''),
                created_by=request.user,
            )
            _replace_version_routes(version, serializer.validated_data.get('routes', []))
        logger.info(
            'admin %s created draft rule version %s',
            request.user.username, version.version,
        )
        return success_response(
            data=RuleVersionOutputSerializer(version).data, message='草稿版本已创建',
        )


class RuleVersionDetailView(APIView):
    """版本详情 / 草稿整单更新路线 / 删除草稿"""
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        version = get_object_or_404(ApprovalRuleVersion, pk=pk)
        return success_response(data=RuleVersionOutputSerializer(version).data)

    def put(self, request, pk):
        if not _is_admin(request.user):
            return error_response(message='仅管理员可维护审批规则', code=403)
        version = get_object_or_404(ApprovalRuleVersion, pk=pk)
        try:
            approval.ensure_editable(version)
        except ApprovalError as exc:
            return _client_error(exc)
        serializer = RuleVersionWriteSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(message=_first_serializer_error(serializer))
        with transaction.atomic():
            version.remark = serializer.validated_data.get('remark', '')
            version.save(update_fields=['remark', 'updated_at'])
            _replace_version_routes(version, serializer.validated_data.get('routes', []))
        return success_response(
            data=RuleVersionOutputSerializer(version).data, message='草稿已更新',
        )

    def delete(self, request, pk):
        if not _is_admin(request.user):
            return error_response(message='仅管理员可维护审批规则', code=403)
        version = get_object_or_404(ApprovalRuleVersion, pk=pk)
        if version.status != ApprovalRuleVersion.STATUS_DRAFT:
            return error_response(message='只能删除草稿版本', code=409)
        version.delete()
        return success_response(message='草稿版本已删除')


class RuleVersionPublishView(APIView):
    """发布草稿：校验完整性后生效，旧生效版本自动归档（不影响在途申请）"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        if not _is_admin(request.user):
            return error_response(message='仅管理员可发布审批规则', code=403)
        try:
            version = approval.publish_version(pk, request.user)
        except ApprovalError as exc:
            return _client_error(exc)
        except ApprovalRuleVersion.DoesNotExist:
            return error_response(message='版本不存在', code=404)
        logger.info(
            'admin %s published rule version %s',
            request.user.username, version.version,
        )
        return success_response(
            data=RuleVersionOutputSerializer(version).data,
            message=f'规则 v{version.version} 已生效，在途申请沿用其固定版本',
        )


class RuleVersionActiveView(APIView):
    """当前生效版本"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        version = approval.get_active_version()
        if version is None:
            return success_response(data=None, message='暂无生效规则版本')
        return success_response(data=RuleVersionOutputSerializer(version).data)
