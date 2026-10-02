"""
仓库管理URL配置
"""
from django.urls import path
from .views import (
    UnitListView, UnitDetailView, UnitBatchDeleteView, UnitAllView,
    CategoryListView, CategoryDetailView, CategoryBatchDeleteView, CategoryAllView,
    VarietyListView, VarietyDetailView, VarietyBatchDeleteView,
    VarietyTemplateView, VarietyImportView,
    DashboardView, GoodsListView, StockInListView,
    WarningListView,
)
from .approval_views import (
    ApprovalTaskListView,
    RuleVersionActiveView, RuleVersionDetailView, RuleVersionListCreateView,
    RuleVersionPublishView,
    StockOutAssignApproverView, StockOutCompleteView, StockOutDecisionView,
    StockOutDetailView, StockOutListView, StockOutPreviewRouteView,
    StockOutReevaluateView, StockOutResubmitView,
)

urlpatterns = [
    # 仪表盘
    path('dashboard/', DashboardView.as_view(), name='dashboard'),

    # 单位管理
    path('units/', UnitListView.as_view(), name='unit-list'),
    path('units/all/', UnitAllView.as_view(), name='unit-all'),
    path('units/batch-delete/', UnitBatchDeleteView.as_view(), name='unit-batch-delete'),
    path('units/<int:pk>/', UnitDetailView.as_view(), name='unit-detail'),

    # 品类管理
    path('categories/', CategoryListView.as_view(), name='category-list'),
    path('categories/all/', CategoryAllView.as_view(), name='category-all'),
    path('categories/batch-delete/', CategoryBatchDeleteView.as_view(), name='category-batch-delete'),
    path('categories/<int:pk>/', CategoryDetailView.as_view(), name='category-detail'),

    # 品种管理
    path('varieties/', VarietyListView.as_view(), name='variety-list'),
    path('varieties/batch-delete/', VarietyBatchDeleteView.as_view(), name='variety-batch-delete'),
    path('varieties/template/', VarietyTemplateView.as_view(), name='variety-template'),
    path('varieties/import/', VarietyImportView.as_view(), name='variety-import'),
    path('varieties/<int:pk>/', VarietyDetailView.as_view(), name='variety-detail'),

    # 货物管理
    path('goods/', GoodsListView.as_view(), name='goods-list'),

    # 入库管理
    path('stock-in/', StockInListView.as_view(), name='stock-in-list'),

    # 出库申请与版本化审批
    path('stock-out/', StockOutListView.as_view(), name='stock-out-list'),
    path('stock-out/preview-route/', StockOutPreviewRouteView.as_view(), name='stock-out-preview-route'),
    path('stock-out/<int:pk>/', StockOutDetailView.as_view(), name='stock-out-detail'),
    path('stock-out/<int:pk>/decision/', StockOutDecisionView.as_view(), name='stock-out-decision'),
    path('stock-out/<int:pk>/assign-approver/', StockOutAssignApproverView.as_view(), name='stock-out-assign-approver'),
    path('stock-out/<int:pk>/reevaluate/', StockOutReevaluateView.as_view(), name='stock-out-reevaluate'),
    path('stock-out/<int:pk>/resubmit/', StockOutResubmitView.as_view(), name='stock-out-resubmit'),
    path('stock-out/<int:pk>/complete/', StockOutCompleteView.as_view(), name='stock-out-complete'),

    # 签署任务（待办）
    path('approval-tasks/', ApprovalTaskListView.as_view(), name='approval-task-list'),

    # 审批规则版本
    path('approval-rules/active/', RuleVersionActiveView.as_view(), name='approval-rule-active'),
    path('approval-rules/', RuleVersionListCreateView.as_view(), name='approval-rule-list'),
    path('approval-rules/<int:pk>/', RuleVersionDetailView.as_view(), name='approval-rule-detail'),
    path('approval-rules/<int:pk>/publish/', RuleVersionPublishView.as_view(), name='approval-rule-publish'),

    # 预警管理
    path('warnings/', WarningListView.as_view(), name='warning-list'),
]
