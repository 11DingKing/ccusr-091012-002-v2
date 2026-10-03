"""
仓库管理URL配置
"""
from django.urls import path
from .views import (
    UnitListView, UnitDetailView, UnitBatchDeleteView, UnitAllView,
    CategoryListView, CategoryDetailView, CategoryBatchDeleteView, CategoryAllView,
    VarietyListView, VarietyDetailView, VarietyBatchDeleteView,
    VarietyTemplateView, VarietyImportView,
    GoodsListView, GoodsDetailView, StockInListView,
    StockOutListView, StockOutDetailView,
    StockOutSignView, StockOutRecuseView, StockOutDelegateView,
    StockOutCancelView, StockOutResubmitView, StockOutReevaluateView,
    WarningListView,
    ApprovalRuleVersionListView, ApprovalRuleVersionDetailView,
    RoleAssignmentListView, RoleAssignmentDetailView,
)

urlpatterns = [
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
    path('goods/<int:pk>/', GoodsDetailView.as_view(), name='goods-detail'),

    # 入库管理
    path('stock-in/', StockInListView.as_view(), name='stock-in-list'),

    # 出库申请与审批
    path('stock-out/', StockOutListView.as_view(), name='stock-out-list'),
    path('stock-out/<int:pk>/', StockOutDetailView.as_view(), name='stock-out-detail'),
    path('stock-out/<int:pk>/sign/', StockOutSignView.as_view(), name='stock-out-sign'),
    path('stock-out/<int:pk>/recuse/', StockOutRecuseView.as_view(), name='stock-out-recuse'),
    path('stock-out/<int:pk>/delegate/', StockOutDelegateView.as_view(), name='stock-out-delegate'),
    path('stock-out/<int:pk>/cancel/', StockOutCancelView.as_view(), name='stock-out-cancel'),
    path('stock-out/<int:pk>/resubmit/', StockOutResubmitView.as_view(), name='stock-out-resubmit'),
    path('stock-out/<int:pk>/reevaluate/', StockOutReevaluateView.as_view(), name='stock-out-reevaluate'),

    # 预警管理
    path('warnings/', WarningListView.as_view(), name='warning-list'),

    # 审批规则版本
    path('approval-rules/', ApprovalRuleVersionListView.as_view(), name='approval-rule-list'),
    path('approval-rules/<int:pk>/', ApprovalRuleVersionDetailView.as_view(), name='approval-rule-detail'),

    # 审批角色指派
    path('approval-assignees/', RoleAssignmentListView.as_view(), name='role-assignment-list'),
    path('approval-assignees/<int:pk>/', RoleAssignmentDetailView.as_view(), name='role-assignment-detail'),
]
