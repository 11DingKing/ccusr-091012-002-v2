# 监管物资保管服务

该项目为监管仓、证物室和受控物资保管点提供服务端 API，覆盖人员授权、物资分类、批次登记、收发记录、版本化审批、预警、审计日志与统计报表。数据保存在 SQLite，所有测试和接口验收均可在单个 Linux 应用容器内离线完成。

## 运行环境

- Python 3.11
- Django REST Framework
- SQLite（自定义引擎启用 WAL + `BEGIN IMMEDIATE`，保证并发签署结果确定）

## 安装与初始化

```bash
python -m pip install -r backend/requirements.txt
cd backend
python manage.py migrate --run-syncdb
```

## 版本化审批路线

出库申请按**物资风险等级、数量、领用部门、用途**评估签署节点（保管员 → 领用部门负责人 → 安全负责人会签 → 分管领导终审）。

- **规则版本化**：`ApprovalRuleVersion` 发布即不可变，旧版本自动归档。
- **创建即冻结**：申请创建时把规则定义、角色→审批人映射、评估输入整体写入 `route_snapshot` 并实例化节点；后续规则调整、审批人变动均不影响在办路线。拒绝后重提按当前最新版本重新评估。
- **回避**：申请人即审批人时创建即自动回避；审批人也可主动回避。可用审批人无法满足签署策略时节点阻塞（`blocked`），由管理员改派后恢复。
- **拒绝后重提**：生成修订号 +1 的新申请并关联原单，原拒绝记录保留审计。
- **条件变化重评**：无人签署前可在同一规则版本内按新条件重算路线；已产生人工签署/回避则须重提。
- **多人并发签署**：支持全员会签（`all`）、任一签署（`any`）、人数门槛（`quorum`）。节点/签署行状态以条件更新（CAS）推进，每个签署人状态确定，未决者在节点终态时收敛为「无需签署」。
- **详情解释**：`GET /api/stock-out/<id>/` 返回节点命中条件、触发原因、签署进度与完整事件流。

### 主要接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/stock-out/` | 创建申请（创建即提交并冻结路线） |
| GET | `/api/stock-out/?scope=mine\|todo\|blocked` | 我的申请 / 待我签署 / 阻塞中 |
| GET | `/api/stock-out/<id>/` | 申请详情（路线、签署、事件、理由） |
| POST | `/api/stock-out/<id>/sign/` | `{action: approve\|reject, comment}` |
| POST | `/api/stock-out/<id>/recuse/` | 当前签署人回避 |
| POST | `/api/stock-out/<id>/delegate/` | 管理员改派（仅阻塞节点） |
| POST | `/api/stock-out/<id>/cancel/` | 撤销审批中申请 |
| POST | `/api/stock-out/<id>/resubmit/` | 拒绝后修订重提 |
| POST | `/api/stock-out/<id>/reevaluate/` | 条件变化、同版本重评 |
| GET/POST | `/api/approval-rules/` | 规则版本列表 / 发布新版本（管理员） |
| GET/POST | `/api/approval-assignees/` | 审批角色指派列表 / 配置（管理员） |

## 测试

```bash
cd backend
pytest -q
```

## 编译检查

```bash
python -m compileall -q backend
```

## API 验收

```bash
cd backend
python manage.py migrate --run-syncdb
python manage.py shell -c "from rest_framework.test import APIClient; from apps.authentication.models import User; u=User.objects.create_user('smoke','safe-pass',role='admin'); c=APIClient(); r=c.post('/api/auth/login/',{'username':'smoke','password':'safe-pass'},format='json'); print(r.status_code, bool(r.json()['data']['token']))"
```

## 容器

```bash
docker build -t custody-service .
docker run --rm custody-service
```
