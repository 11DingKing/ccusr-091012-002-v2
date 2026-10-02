# 监管物资保管服务

该项目为监管仓、证物室和受控物资保管点提供服务端 API，覆盖人员授权、物资分类、批次登记、收发记录、审批、预警、审计日志与统计报表。数据保存在 SQLite，所有测试和接口验收均可在单个 Linux 应用容器内离线完成。

## 运行环境

- Python 3.11
- Django REST Framework
- SQLite

## 安装与初始化

```bash
python -m pip install -r backend/requirements.txt
cd backend
python manage.py migrate --run-syncdb
```

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

## 版本化审批路线

审批规则按版本发布，发布后不可变；调整规则只能新建版本并发布，旧版本自动归档，
在途申请始终引用其创建时的版本与路线快照（`StockOut.rule_version` /
`route_snapshot` / `route_generation`），规则调整不会改变正在办理的路线。

- 选路维度：物资风险等级（普通/较高/高风险）、数量区间、领用部门、用途、优先级、兜底路线。
- 节点签署方式：单人签署 `single`、任一签署 `any`（一人同意即过，一人拒绝不否决）、多人会签 `all`。
- 节点可指定固定审批人，未指定或指定人回避时由在职管理员按确定顺序补位；申请人命中审批人自动回避。
- 确定状态：申请（待审批/已通过/已拒绝/无法流转/已完成），任务（待激活/待签署/已同意/已拒绝/已回避/已取消）。
- 拒绝后重提（`resubmit`）按当前生效版本重新快照；在途条件变化（`reevaluate`）固定版本内重新选路，
  路线变化时代次 +1，旧代签署保留为审计痕迹，兼容的已批准结论可沿用。
- 签署以条件 UPDATE 落库，重复/并发提交确定返回 409。
- 申请详情（`GET /api/stock-out/<id>/`）的 `approval` 字段解释路线命中原因、
  每个节点“为什么是这些签署人”以及完整评估历史。

接口：

| 方法 & 路径 | 说明 |
| --- | --- |
| `POST /api/approval-rules/` | 管理员创建草稿版本（可携带完整路线/节点/审批人） |
| `PUT/DELETE /api/approval-rules/<id>/` | 草稿更新/删除（已发布版本不可改） |
| `POST /api/approval-rules/<id>/publish/` | 校验完整性后发布，旧生效版本自动归档 |
| `GET /api/approval-rules/active/` | 当前生效版本 |
| `POST /api/stock-out/` | 创建领用申请，创建即固定规则快照 |
| `POST /api/stock-out/preview-route/` | 预演将命中的路线，不产生申请 |
| `GET /api/stock-out/<id>/` | 申请详情与签署解释 |
| `POST /api/stock-out/<id>/decision/` | 签署：`approved` / `rejected`（须填意见）/ `recused` |
| `POST /api/stock-out/<id>/reevaluate/` | 条件变化，固定版本内重新评估 |
| `POST /api/stock-out/<id>/resubmit/` | 拒绝后按当前生效版本重提 |
| `POST /api/stock-out/<id>/assign-approver/` | blocked 时管理员指派替补审批人 |
| `POST /api/stock-out/<id>/complete/` | 审批通过后放行并扣减库存 |
| `GET /api/approval-tasks/` | 签署任务（默认当前用户待办） |

## 容器

```bash
docker build -t custody-service .
docker run --rm custody-service
```
