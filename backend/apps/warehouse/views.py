"""
仓库管理视图
"""
import logging
import io
from django.db import transaction
from django.http import HttpResponse
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from apps.core.response import success_response, error_response
from apps.authentication.models import User
from .models import (
    Unit, Category, Variety, Goods, StockIn, StockOut, Warning,
    ApprovalRuleVersion, ApprovalRoleAssignment,
)
from . import approval_engine
from .serializers import (
    UnitSerializer, UnitCreateSerializer,
    CategorySerializer, CategoryCreateSerializer,
    VarietySerializer, VarietyCreateSerializer,
    GoodsSerializer, GoodsCreateSerializer,
    StockInSerializer, StockOutSerializer, StockOutCreateSerializer,
    StockOutDetailSerializer, StockOutResubmitSerializer,
    WarningSerializer,
    ApprovalRuleVersionSerializer, RuleVersionPublishSerializer,
    RoleAssignmentSerializer,
    SignActionSerializer, RecuseSerializer, DelegateSerializer,
)

logger = logging.getLogger('apps')


# ==================== 单位管理 ====================

class UnitListView(APIView):
    """单位列表视图"""
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        queryset = Unit.objects.all().order_by('-created_at')
        
        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 10))
        start = (page - 1) * page_size
        end = start + page_size
        
        total = queryset.count()
        units = queryset[start:end]
        
        serializer = UnitSerializer(units, many=True)
        
        return success_response(data={
            'list': serializer.data,
            'total': total,
            'page': page,
            'page_size': page_size
        })
    
    def post(self, request):
        """创建单位"""
        serializer = UnitCreateSerializer(data=request.data)
        if not serializer.is_valid():
            errors = serializer.errors
            first_error = list(errors.values())[0][0]
            return error_response(message=str(first_error))
        
        unit = Unit.objects.create(
            name=serializer.validated_data['name'],
            created_by=request.user
        )
        
        logger.info(f"User {request.user.username} created unit {unit.name}")
        
        return success_response(data=UnitSerializer(unit).data, message='创建成功')


class UnitDetailView(APIView):
    """单位详情视图"""
    permission_classes = [IsAuthenticated]
    
    def put(self, request, pk):
        """更新单位"""
        try:
            unit = Unit.objects.get(pk=pk)
        except Unit.DoesNotExist:
            return error_response(message='单位不存在', code=404)
        
        serializer = UnitCreateSerializer(data=request.data, context={'instance': unit})
        if not serializer.is_valid():
            errors = serializer.errors
            first_error = list(errors.values())[0][0]
            return error_response(message=str(first_error))
        
        unit.name = serializer.validated_data['name']
        unit.save()
        
        logger.info(f"User {request.user.username} updated unit {unit.name}")
        
        return success_response(data=UnitSerializer(unit).data, message='更新成功')
    
    def delete(self, request, pk):
        """删除单位"""
        try:
            unit = Unit.objects.get(pk=pk)
        except Unit.DoesNotExist:
            return error_response(message='单位不存在', code=404)
        
        if unit.is_linked:
            return error_response(message='该单位已被关联，无法删除')
        
        name = unit.name
        unit.delete()
        
        logger.info(f"User {request.user.username} deleted unit {name}")
        
        return success_response(message='删除成功')


class UnitBatchDeleteView(APIView):
    """单位批量删除视图"""
    permission_classes = [IsAuthenticated]
    
    def post(self, request):
        ids = request.data.get('ids', [])
        if not ids:
            return error_response(message='请选择要删除的单位')
        
        # 只删除未关联的单位
        units = Unit.objects.filter(pk__in=ids)
        deleted_count = 0
        for unit in units:
            if not unit.is_linked:
                unit.delete()
                deleted_count += 1
        
        logger.info(f"User {request.user.username} batch deleted {deleted_count} units")
        
        return success_response(message=f'成功删除 {deleted_count} 个单位')


class UnitAllView(APIView):
    """获取所有单位（用于下拉选择）"""
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        units = Unit.objects.filter(is_active=True).order_by('name')
        serializer = UnitSerializer(units, many=True)
        return success_response(data=serializer.data)


# ==================== 品类管理 ====================

class CategoryListView(APIView):
    """品类列表视图"""
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        queryset = Category.objects.all().order_by('-created_at')
        
        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 10))
        start = (page - 1) * page_size
        end = start + page_size
        
        total = queryset.count()
        categories = queryset[start:end]
        
        serializer = CategorySerializer(categories, many=True)
        
        return success_response(data={
            'list': serializer.data,
            'total': total,
            'page': page,
            'page_size': page_size
        })
    
    def post(self, request):
        """创建品类"""
        serializer = CategoryCreateSerializer(data=request.data)
        if not serializer.is_valid():
            errors = serializer.errors
            first_error = list(errors.values())[0][0]
            return error_response(message=str(first_error))
        
        unit = Unit.objects.get(pk=serializer.validated_data['unit'])
        category = Category.objects.create(
            name=serializer.validated_data['name'],
            unit=unit,
            created_by=request.user
        )
        
        logger.info(f"User {request.user.username} created category {category.name}")
        
        return success_response(data=CategorySerializer(category).data, message='创建成功')


class CategoryDetailView(APIView):
    """品类详情视图"""
    permission_classes = [IsAuthenticated]
    
    def put(self, request, pk):
        """更新品类"""
        try:
            category = Category.objects.get(pk=pk)
        except Category.DoesNotExist:
            return error_response(message='品类不存在', code=404)
        
        serializer = CategoryCreateSerializer(data=request.data, context={'instance': category})
        if not serializer.is_valid():
            errors = serializer.errors
            first_error = list(errors.values())[0][0]
            return error_response(message=str(first_error))
        
        category.name = serializer.validated_data['name']
        category.unit = Unit.objects.get(pk=serializer.validated_data['unit'])
        category.save()
        
        logger.info(f"User {request.user.username} updated category {category.name}")
        
        return success_response(data=CategorySerializer(category).data, message='更新成功')
    
    def delete(self, request, pk):
        """删除品类"""
        try:
            category = Category.objects.get(pk=pk)
        except Category.DoesNotExist:
            return error_response(message='品类不存在', code=404)
        
        if category.is_linked:
            return error_response(message='该品类已被关联，无法删除')
        
        name = category.name
        category.delete()
        
        logger.info(f"User {request.user.username} deleted category {name}")
        
        return success_response(message='删除成功')


class CategoryBatchDeleteView(APIView):
    """品类批量删除视图"""
    permission_classes = [IsAuthenticated]
    
    def post(self, request):
        ids = request.data.get('ids', [])
        if not ids:
            return error_response(message='请选择要删除的品类')
        
        categories = Category.objects.filter(pk__in=ids)
        deleted_count = 0
        for category in categories:
            if not category.is_linked:
                category.delete()
                deleted_count += 1
        
        logger.info(f"User {request.user.username} batch deleted {deleted_count} categories")
        
        return success_response(message=f'成功删除 {deleted_count} 个品类')


class CategoryAllView(APIView):
    """获取所有品类（用于下拉选择）"""
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        categories = Category.objects.filter(is_active=True).order_by('name')
        serializer = CategorySerializer(categories, many=True)
        return success_response(data=serializer.data)


# ==================== 品种管理 ====================

class VarietyListView(APIView):
    """品种列表视图"""
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        queryset = Variety.objects.all().order_by('-created_at')
        
        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 10))
        start = (page - 1) * page_size
        end = start + page_size
        
        total = queryset.count()
        varieties = queryset[start:end]
        
        serializer = VarietySerializer(varieties, many=True)
        
        return success_response(data={
            'list': serializer.data,
            'total': total,
            'page': page,
            'page_size': page_size
        })
    
    def post(self, request):
        """创建品种"""
        serializer = VarietyCreateSerializer(data=request.data)
        if not serializer.is_valid():
            errors = serializer.errors
            first_error = list(errors.values())[0]
            if isinstance(first_error, list):
                first_error = first_error[0]
            return error_response(message=str(first_error))
        
        category = Category.objects.get(pk=serializer.validated_data['category'])
        variety = Variety.objects.create(
            name=serializer.validated_data['name'],
            category=category,
            created_by=request.user
        )
        
        logger.info(f"User {request.user.username} created variety {variety.name}")
        
        return success_response(data=VarietySerializer(variety).data, message='创建成功')


class VarietyDetailView(APIView):
    """品种详情视图"""
    permission_classes = [IsAuthenticated]
    
    def put(self, request, pk):
        """更新品种"""
        try:
            variety = Variety.objects.get(pk=pk)
        except Variety.DoesNotExist:
            return error_response(message='品种不存在', code=404)
        
        serializer = VarietyCreateSerializer(data=request.data, context={'instance': variety})
        if not serializer.is_valid():
            errors = serializer.errors
            first_error = list(errors.values())[0]
            if isinstance(first_error, list):
                first_error = first_error[0]
            return error_response(message=str(first_error))
        
        variety.name = serializer.validated_data['name']
        variety.category = Category.objects.get(pk=serializer.validated_data['category'])
        variety.save()
        
        logger.info(f"User {request.user.username} updated variety {variety.name}")
        
        return success_response(data=VarietySerializer(variety).data, message='更新成功')
    
    def delete(self, request, pk):
        """删除品种"""
        try:
            variety = Variety.objects.get(pk=pk)
        except Variety.DoesNotExist:
            return error_response(message='品种不存在', code=404)
        
        if variety.is_in_stock:
            return error_response(message='该品种已入库，无法删除')
        
        name = variety.name
        variety.delete()
        
        logger.info(f"User {request.user.username} deleted variety {name}")
        
        return success_response(message='删除成功')


class VarietyBatchDeleteView(APIView):
    """品种批量删除视图"""
    permission_classes = [IsAuthenticated]
    
    def post(self, request):
        ids = request.data.get('ids', [])
        if not ids:
            return error_response(message='请选择要删除的品种')
        
        varieties = Variety.objects.filter(pk__in=ids)
        deleted_count = 0
        for variety in varieties:
            if not variety.is_in_stock:
                variety.delete()
                deleted_count += 1
        
        logger.info(f"User {request.user.username} batch deleted {deleted_count} varieties")
        
        return success_response(message=f'成功删除 {deleted_count} 个品种')


class VarietyTemplateView(APIView):
    """品种导入模板下载"""
    permission_classes = []  # 允许匿名访问，通过token参数验证
    
    def get(self, request):
        # 从URL参数获取token进行验证
        from apps.authentication.backends import decode_token

        token = request.query_params.get('token')
        if not token:
            return error_response(message='缺少认证信息', code=401)
        
        payload = decode_token(token)
        if not payload:
            return error_response(message='认证信息无效或已过期', code=401)
        
        try:
            User.objects.get(pk=payload['user_id'])
        except User.DoesNotExist:
            return error_response(message='用户不存在', code=401)
        
        wb = Workbook()
        
        # 第一个表格 - 导入模板
        ws1 = wb.active
        ws1.title = '品种导入'
        
        # 设置表头样式
        header_font = Font(bold=True, color='FFFFFF')
        header_fill = PatternFill(start_color='4F46E5', end_color='4F46E5', fill_type='solid')
        header_alignment = Alignment(horizontal='center', vertical='center')
        thin_border = Border(
            left=Side(style='thin'),
            right=Side(style='thin'),
            top=Side(style='thin'),
            bottom=Side(style='thin')
        )
        
        headers = ['品种', '品类', '单位']
        for col, header in enumerate(headers, 1):
            cell = ws1.cell(row=1, column=col, value=header)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = header_alignment
            cell.border = thin_border
        
        # 设置列宽
        ws1.column_dimensions['A'].width = 25
        ws1.column_dimensions['B'].width = 20
        ws1.column_dimensions['C'].width = 15
        
        # 第二个表格 - 品类参考
        ws2 = wb.create_sheet(title='品类参考')
        
        headers2 = ['品类', '单位']
        for col, header in enumerate(headers2, 1):
            cell = ws2.cell(row=1, column=col, value=header)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = header_alignment
            cell.border = thin_border
        
        # 填充品类数据
        categories = Category.objects.filter(is_active=True).select_related('unit')
        for row, category in enumerate(categories, 2):
            ws2.cell(row=row, column=1, value=category.name).border = thin_border
            ws2.cell(row=row, column=2, value=category.unit.name).border = thin_border
        
        ws2.column_dimensions['A'].width = 20
        ws2.column_dimensions['B'].width = 15
        
        # 返回Excel文件
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        
        response = HttpResponse(
            output.read(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = 'attachment; filename=variety_import_template.xlsx'
        
        return response


class VarietyImportView(APIView):
    """品种导入视图"""
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    
    def post(self, request):
        if 'file' not in request.FILES:
            return error_response(message='请上传文件')
        
        file = request.FILES['file']
        
        try:
            wb = load_workbook(file)
            ws = wb.active
        except Exception:
            return error_response(message='文件格式错误，请上传Excel文件')
        
        # 获取所有品类及其单位
        categories = {c.name: c for c in Category.objects.filter(is_active=True).select_related('unit')}
        
        can_import = []
        cannot_import = []
        
        for row in range(2, ws.max_row + 1):
            variety_name = ws.cell(row=row, column=1).value
            category_name = ws.cell(row=row, column=2).value
            unit_name = ws.cell(row=row, column=3).value
            
            if not variety_name:
                continue
            
            variety_name = str(variety_name).strip()
            category_name = str(category_name).strip() if category_name else ''
            unit_name = str(unit_name).strip() if unit_name else ''
            
            # 验证
            error_msg = None
            
            if not variety_name:
                error_msg = '品种名称不能为空'
            elif len(variety_name) > 20:
                error_msg = '品种名称最多20个字'
            elif not category_name:
                error_msg = '品类不能为空'
            elif category_name not in categories:
                error_msg = f'品类"{category_name}"不存在'
            elif not unit_name:
                error_msg = '单位不能为空'
            elif categories.get(category_name) and categories[category_name].unit.name != unit_name:
                error_msg = f'单位与品类不匹配，应为"{categories[category_name].unit.name}"'
            elif Variety.objects.filter(name=variety_name, category__name=category_name).exists():
                error_msg = '该品种已存在'
            
            if error_msg:
                cannot_import.append({
                    'row': row,
                    'variety': variety_name,
                    'category': category_name,
                    'unit': unit_name,
                    'reason': error_msg
                })
            else:
                can_import.append({
                    'row': row,
                    'variety': variety_name,
                    'category': category_name,
                    'unit': unit_name
                })
        
        # 如果是预览请求
        if request.data.get('preview') == 'true':
            return success_response(data={
                'can_import': can_import,
                'cannot_import': cannot_import,
                'can_import_count': len(can_import),
                'cannot_import_count': len(cannot_import)
            })
        
        # 执行导入
        imported_count = 0
        for item in can_import:
            category = categories[item['category']]
            Variety.objects.create(
                name=item['variety'],
                category=category,
                created_by=request.user
            )
            imported_count += 1
        
        logger.info(f"User {request.user.username} imported {imported_count} varieties")
        
        return success_response(
            data={
                'imported_count': imported_count,
                'failed_count': len(cannot_import),
                'failed_items': cannot_import
            },
            message=f'成功导入 {imported_count} 个品种'
        )


# ==================== 货物管理 ====================

class GoodsListView(APIView):
    """货物列表/创建"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        queryset = Goods.objects.select_related(
            'variety__category__unit'
        ).all().order_by('-created_at')

        keyword = request.query_params.get('keyword')
        if keyword:
            queryset = queryset.filter(name__icontains=keyword)
        risk_level = request.query_params.get('risk_level')
        if risk_level:
            queryset = queryset.filter(risk_level=risk_level)
        variety_id = request.query_params.get('variety')
        if variety_id:
            queryset = queryset.filter(variety_id=variety_id)

        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 10))
        start = (page - 1) * page_size
        end = start + page_size

        total = queryset.count()
        goods = queryset[start:end]
        serializer = GoodsSerializer(goods, many=True)
        return success_response(data={
            'list': serializer.data, 'total': total,
            'page': page, 'page_size': page_size
        })

    def post(self, request):
        serializer = GoodsCreateSerializer(data=request.data)
        if not serializer.is_valid():
            first_error = list(serializer.errors.values())[0]
            if isinstance(first_error, list):
                first_error = first_error[0]
            return error_response(message=str(first_error))
        data = serializer.validated_data
        goods = Goods.objects.create(**data)
        logger.info(f"User {request.user.username} created goods {goods.code}")
        return success_response(data=GoodsSerializer(goods).data, message='创建成功')


class GoodsDetailView(APIView):
    """货物详情/更新/删除"""
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            goods = Goods.objects.select_related('variety__category__unit').get(pk=pk)
        except Goods.DoesNotExist:
            return error_response(message='货物不存在', code=404)
        return success_response(data=GoodsSerializer(goods).data)

    def put(self, request, pk):
        try:
            goods = Goods.objects.get(pk=pk)
        except Goods.DoesNotExist:
            return error_response(message='货物不存在', code=404)
        serializer = GoodsCreateSerializer(data=request.data, context={'instance': goods})
        if not serializer.is_valid():
            first_error = list(serializer.errors.values())[0]
            if isinstance(first_error, list):
                first_error = first_error[0]
            return error_response(message=str(first_error))
        for field, value in serializer.validated_data.items():
            setattr(goods, field, value)
        goods.save()
        return success_response(data=GoodsSerializer(goods).data, message='更新成功')

    def delete(self, request, pk):
        try:
            goods = Goods.objects.get(pk=pk)
        except Goods.DoesNotExist:
            return error_response(message='货物不存在', code=404)
        if goods.stock_ins.exists() or goods.stock_outs.exists():
            return error_response(message='货物存在出入库记录，无法删除')
        goods.delete()
        return success_response(message='删除成功')


# ==================== 入库记录 ====================

class StockInListView(APIView):
    """入库记录列表/登记"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        queryset = StockIn.objects.select_related('goods', 'operator').all().order_by('-stock_in_time')
        goods_id = request.query_params.get('goods_id')
        if goods_id:
            queryset = queryset.filter(goods_id=goods_id)

        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 10))
        start = (page - 1) * page_size
        end = start + page_size
        total = queryset.count()
        serializer = StockInSerializer(queryset[start:end], many=True)
        return success_response(data={
            'list': serializer.data, 'total': total,
            'page': page, 'page_size': page_size
        })

    def post(self, request):
        goods_id = request.data.get('goods')
        try:
            goods = Goods.objects.get(pk=goods_id)
        except (Goods.DoesNotExist, ValueError, TypeError):
            return error_response(message='货物不存在')

        from decimal import Decimal
        try:
            quantity = Decimal(str(request.data.get('quantity')))
            assert quantity > 0
        except Exception:
            return error_response(message='入库数量必须大于 0')

        with transaction.atomic():
            record = StockIn.objects.create(
                goods=goods, operator=request.user, quantity=quantity,
                batch_no=request.data.get('batch_no', ''),
                supplier=request.data.get('supplier', ''),
                remark=request.data.get('remark', ''),
            )
            goods.quantity += quantity
            goods.save(update_fields=['quantity'])
        logger.info(f"User {request.user.username} stocked in {quantity} of goods {goods.code}")
        return success_response(data=StockInSerializer(record).data, message='入库成功')


# ==================== 出库申请与审批 ====================

def _first_error(serializer):
    first_error = list(serializer.errors.values())[0]
    if isinstance(first_error, list):
        first_error = first_error[0]
    return str(first_error)


class StockOutListView(APIView):
    """出库申请列表 / 创建（创建即提交，冻结规则快照）"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        queryset = StockOut.objects.select_related(
            'goods', 'operator', 'current_step', 'rule_version'
        ).all().order_by('-created_at')

        status = request.query_params.get('status')
        if status:
            queryset = queryset.filter(status=status)
        goods_id = request.query_params.get('goods_id')
        if goods_id:
            queryset = queryset.filter(goods_id=goods_id)
        # 默认只看本人申请；管理员可看全部
        scope = request.query_params.get('scope', 'mine')
        if scope == 'mine':
            queryset = queryset.filter(operator=request.user)
        elif scope == 'todo':
            # 待当前用户签署
            queryset = queryset.filter(
                status=StockOut.STATUS_PENDING,
                current_step__signers__user=request.user,
                current_step__signers__status='pending',
            ).distinct()
        elif scope == 'blocked':
            # 节点阻塞、等待管理员改派
            if not request.user.is_admin:
                return error_response(message='仅管理员可查看阻塞中的申请', code=403)
            queryset = queryset.filter(
                status=StockOut.STATUS_PENDING,
                current_step__status='blocked',
            )

        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 10))
        start = (page - 1) * page_size
        end = start + page_size
        total = queryset.count()
        serializer = StockOutSerializer(queryset[start:end], many=True)
        return success_response(data={
            'list': serializer.data, 'total': total,
            'page': page, 'page_size': page_size
        })

    def post(self, request):
        serializer = StockOutCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(message=_first_error(serializer))
        data = serializer.validated_data
        try:
            stock_out = StockOut(
                goods=data['goods'],
                operator=request.user,
                receiver=data['receiver'],
                receiver_dept=data.get('receiver_dept', ''),
                quantity=data['quantity'],
                purpose_type=data.get('purpose_type', StockOut.PURPOSE_USE),
                purpose=data.get('purpose', ''),
                remark=data.get('remark', ''),
                status=StockOut.STATUS_DRAFT,
            )
            approval_engine.instantiate_route(stock_out, actor=request.user)
        except approval_engine.ApprovalConfigError as exc:
            return error_response(message=str(exc))
        stock_out = StockOut.objects.select_related(
            'goods', 'operator', 'current_step', 'rule_version').get(pk=stock_out.pk)
        logger.info(f"User {request.user.username} submitted stock-out {stock_out.id}")
        return success_response(data=StockOutDetailSerializer(stock_out).data, message='提交成功，审批路线已生成')


class StockOutDetailView(APIView):
    """申请详情：含完整路线、签署状态、事件流与签署理由解释"""
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        stock_out = self._get_object(pk)
        if stock_out is None:
            return error_response(message='申请不存在', code=404)
        return success_response(data=StockOutDetailSerializer(stock_out).data)

    def _get_object(self, pk):
        try:
            return (
                StockOut.objects
                .select_related('goods', 'operator', 'current_step', 'rule_version')
                .prefetch_related(
                    'steps__signers__user', 'events__actor', 'events__step')
                .get(pk=pk)
            )
        except StockOut.DoesNotExist:
            return None


class StockOutSignView(APIView):
    """当前节点签署：通过 / 拒绝（多人并发安全）"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        serializer = SignActionSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(message=_first_error(serializer))
        try:
            stock_out, _ = approval_engine.sign_approval(
                pk, request.user,
                serializer.validated_data['action'],
                serializer.validated_data.get('comment', ''),
            )
        except approval_engine.ApprovalStateError as exc:
            return error_response(message=str(exc))
        except StockOut.DoesNotExist:
            return error_response(message='申请不存在', code=404)
        stock_out = StockOut.objects.select_related(
            'goods', 'operator', 'current_step', 'rule_version').get(pk=stock_out.id)
        message = '已通过' if serializer.validated_data['action'] == 'approve' else '已拒绝，申请流程终止'
        return success_response(data=StockOutDetailSerializer(stock_out).data, message=message)


class StockOutRecuseView(APIView):
    """当前签署人主动回避"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        serializer = RecuseSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(message=_first_error(serializer))
        try:
            stock_out, step = approval_engine.recuse(
                pk, request.user, serializer.validated_data.get('comment', ''))
        except approval_engine.ApprovalStateError as exc:
            return error_response(message=str(exc))
        except StockOut.DoesNotExist:
            return error_response(message='申请不存在', code=404)
        if step.status == 'blocked':
            message = '已回避，当前节点无可用审批人，已阻塞等待管理员改派'
        else:
            message = '已回避，等待同节点其他审批人签署'
        stock_out = StockOut.objects.select_related(
            'goods', 'operator', 'current_step', 'rule_version').get(pk=stock_out.id)
        return success_response(data=StockOutDetailSerializer(stock_out).data, message=message)


class StockOutDelegateView(APIView):
    """管理员对阻塞节点改派审批人"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        if not request.user.is_admin:
            return error_response(message='仅管理员可以改派审批人', code=403)
        serializer = DelegateSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(message=_first_error(serializer))
        try:
            stock_out, _, _ = approval_engine.delegate(
                pk, request.user, serializer.validated_data['user'],
                serializer.validated_data.get('comment', ''))
        except approval_engine.ApprovalStateError as exc:
            return error_response(message=str(exc))
        except StockOut.DoesNotExist:
            return error_response(message='申请不存在', code=404)
        stock_out = StockOut.objects.select_related(
            'goods', 'operator', 'current_step', 'rule_version').get(pk=stock_out.id)
        return success_response(data=StockOutDetailSerializer(stock_out).data, message='改派成功，节点恢复签署')


class StockOutCancelView(APIView):
    """撤销审批中的申请"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        try:
            stock_out = approval_engine.cancel(pk, request.user)
        except approval_engine.ApprovalStateError as exc:
            return error_response(message=str(exc))
        except StockOut.DoesNotExist:
            return error_response(message='申请不存在', code=404)
        stock_out = StockOut.objects.select_related(
            'goods', 'operator', 'current_step', 'rule_version').get(pk=stock_out.id)
        return success_response(data=StockOutDetailSerializer(stock_out).data, message='申请已撤销')


class StockOutResubmitView(APIView):
    """拒绝后重提：新申请、修订号 +1、按当前生效规则重新评估冻结"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        try:
            rejected = StockOut.objects.get(pk=pk)
        except StockOut.DoesNotExist:
            return error_response(message='申请不存在', code=404)
        if rejected.operator_id != request.user.id and not request.user.is_admin:
            return error_response(message='仅申请人本人可以重新提交', code=403)
        data = request.data or {}
        serializer = StockOutResubmitSerializer(data={
            'goods': data.get('goods', rejected.goods_id),
            'receiver': data.get('receiver', rejected.receiver),
            'receiver_dept': data.get('receiver_dept', rejected.receiver_dept),
            'quantity': data.get('quantity', rejected.quantity),
            'purpose_type': data.get('purpose_type', rejected.purpose_type),
            'purpose': data.get('purpose', rejected.purpose),
            'remark': data.get('remark', rejected.remark),
        })
        if not serializer.is_valid():
            return error_response(message=_first_error(serializer))
        overrides = serializer.validated_data
        try:
            application = approval_engine.resubmit(rejected, request.user, **overrides)
        except (approval_engine.ApprovalStateError, approval_engine.ApprovalConfigError) as exc:
            return error_response(message=str(exc))
        application = StockOut.objects.select_related(
            'goods', 'operator', 'current_step', 'rule_version').get(pk=application.id)
        return success_response(
            data=StockOutDetailSerializer(application).data,
            message=f'已第 {application.revision_no} 次提交并按最新规则生成审批路线')


class StockOutReevaluateView(APIView):
    """条件变化重新评估：规则版本不变、按新条件重算路线（仅限尚无人工签署时）"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        try:
            stock_out = StockOut.objects.get(pk=pk)
        except StockOut.DoesNotExist:
            return error_response(message='申请不存在', code=404)
        if stock_out.operator_id != request.user.id and not request.user.is_admin:
            return error_response(message='仅申请人本人可以变更申请条件', code=403)

        allowed = {'quantity', 'receiver_dept', 'purpose_type', 'purpose', 'receiver', 'goods'}
        changes = {k: v for k, v in request.data.items() if k in allowed}
        if not changes:
            return error_response(message='未提供任何可变更字段（goods/quantity/receiver_dept/purpose_type/purpose/receiver）')

        from decimal import Decimal
        if 'goods' in changes:
            try:
                changes['goods'] = Goods.objects.get(pk=changes['goods'])
            except (Goods.DoesNotExist, ValueError, TypeError):
                return error_response(message='货物不存在')
        if 'quantity' in changes:
            try:
                changes['quantity'] = Decimal(str(changes['quantity']))
                assert changes['quantity'] > 0
                target_goods = changes.get('goods', stock_out.goods)
                if changes['quantity'] > target_goods.quantity:
                    return error_response(message=f"库存不足，当前库存 {target_goods.quantity}")
            except Exception:
                return error_response(message='出库数量非法')
        try:
            stock_out = approval_engine.reevaluate(stock_out, request.user, **changes)
        except (approval_engine.ApprovalStateError, approval_engine.ApprovalConfigError) as exc:
            return error_response(message=str(exc))
        stock_out = StockOut.objects.select_related(
            'goods', 'operator', 'current_step', 'rule_version').get(pk=stock_out.id)
        return success_response(data=StockOutDetailSerializer(stock_out).data,
                               message='条件已变更，审批路线已按同一规则版本重新评估')


# ==================== 审批规则版本 ====================

def _require_admin(request):
    return request.user.is_admin


class ApprovalRuleVersionListView(APIView):
    """规则版本列表 / 发布新版本（旧版本自动归档，不影响在办路线）"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        versions = ApprovalRuleVersion.objects.all().order_by('-version')
        serializer = ApprovalRuleVersionSerializer(versions, many=True)
        active = versions.filter(status=ApprovalRuleVersion.STATUS_ACTIVE).first()
        return success_response(data={
            'list': serializer.data,
            'active_version': active.version if active else None,
            'roles': [
                {'code': code, 'name': name} for code, name in approval_engine.ROLE_CHOICES
            ],
            'default_nodes': approval_engine.DEFAULT_NODES,
        })

    def post(self, request):
        if not _require_admin(request):
            return error_response(message='仅管理员可以发布审批规则', code=403)
        serializer = RuleVersionPublishSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(message=_first_error(serializer))
        try:
            rule = approval_engine.publish_version(
                serializer.validated_data['nodes'],
                serializer.validated_data.get('remark', ''),
                created_by=request.user,
            )
        except approval_engine.ApprovalConfigError as exc:
            return error_response(message=str(exc))
        logger.info(f"User {request.user.username} published approval rule v{rule.version}")
        return success_response(data=ApprovalRuleVersionSerializer(rule).data,
                               message=f'规则 v{rule.version} 已发布生效')


class ApprovalRuleVersionDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            rule = ApprovalRuleVersion.objects.get(pk=pk)
        except ApprovalRuleVersion.DoesNotExist:
            return error_response(message='规则版本不存在', code=404)
        return success_response(data=ApprovalRuleVersionSerializer(rule).data)


# ==================== 审批角色指派 ====================

class RoleAssignmentListView(APIView):
    """审批角色指派列表 / 新增指派"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = ApprovalRoleAssignment.objects.select_related('user').all().order_by('id')
        role_code = request.query_params.get('role_code')
        if role_code:
            qs = qs.filter(role_code=role_code)
        scope_dept = request.query_params.get('scope_dept')
        if scope_dept is not None:
            qs = qs.filter(scope_dept=scope_dept)
        serializer = RoleAssignmentSerializer(qs, many=True)
        return success_response(data={
            'list': serializer.data,
            'roles': [{'code': code, 'name': name} for code, name in approval_engine.ROLE_CHOICES],
        })

    def post(self, request):
        if not _require_admin(request):
            return error_response(message='仅管理员可以配置审批人', code=403)
        serializer = RoleAssignmentSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(message=_first_error(serializer))
        assignment = serializer.save(created_by=request.user)
        return success_response(data=RoleAssignmentSerializer(assignment).data, message='指派成功')


class RoleAssignmentDetailView(APIView):
    """更新/删除单条指派"""
    permission_classes = [IsAuthenticated]

    def put(self, request, pk):
        if not _require_admin(request):
            return error_response(message='仅管理员可以配置审批人', code=403)
        try:
            assignment = ApprovalRoleAssignment.objects.get(pk=pk)
        except ApprovalRoleAssignment.DoesNotExist:
            return error_response(message='指派不存在', code=404)
        serializer = RoleAssignmentSerializer(assignment, data=request.data, partial=True)
        if not serializer.is_valid():
            return error_response(message=_first_error(serializer))
        serializer.save()
        return success_response(data=RoleAssignmentSerializer(assignment).data, message='更新成功')

    def delete(self, request, pk):
        if not _require_admin(request):
            return error_response(message='仅管理员可以配置审批人', code=403)
        try:
            assignment = ApprovalRoleAssignment.objects.get(pk=pk)
        except ApprovalRoleAssignment.DoesNotExist:
            return error_response(message='指派不存在', code=404)
        assignment.delete()
        return success_response(message='已移除指派')


# ==================== 预警记录 ====================

class WarningListView(APIView):
    """预警记录列表"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        queryset = Warning.objects.select_related('goods').all().order_by('-created_at')
        warning_type = request.query_params.get('type')
        if warning_type:
            queryset = queryset.filter(type=warning_type)
        is_read = request.query_params.get('is_read')
        if is_read in ('true', 'false'):
            queryset = queryset.filter(is_read=is_read == 'true')

        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 10))
        start = (page - 1) * page_size
        end = start + page_size
        total = queryset.count()
        serializer = WarningSerializer(queryset[start:end], many=True)
        return success_response(data={
            'list': serializer.data, 'total': total,
            'page': page, 'page_size': page_size
        })
