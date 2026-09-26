# 移民案件期限与材料管理

纯Python标准库实现的移民案件期限与材料管理原型，使用SQLite持久化，HTTP接口由`http.server`提供。

## 模块结构

- `app.py`：命令行参数、依赖组装和服务启动。
- `src/domain.py`：领域数据类型、错误和基础校验。
- `src/rules.py`：状态转换、法定天数、补件期限和材料完整性和冲突检查。
- `src/family_rules.py`：随行家属名单规则（登记校验、年龄与未成年判断、角色权限）。
- `src/family_materials.py`：按当前名单计算每位家属所需材料与待补缺项。
- `src/family_service.py`：家属台账用例（名单维护、缺项重算、决定前逐人核查）。
- `src/repository.py`：SQLite建表、事务和查询（含家属、家属材料、变动记录表）。
- `src/service.py`：用例编排、权限检查、乐观并发和审计。
- `src/http_api.py`：HTTP路由与统一错误响应。
- `src/audit.py`：事件时间线。
- `static/index.html`：最小演示页面。
- `tests/`：完整流程、规则计算和失败场景测试。

## 启动

```bash
python3 app.py --db ./data.db --port 8329
```

默认端口为`8329`，默认数据库位于项目目录。服务启动时自动建表。

## 主要接口

- `GET /health`：健康检查。
- `GET /`：演示页面。
- `GET /api/records`：记录列表，可带`state`和`limit`参数。
- `GET /api/records/{id}`：记录详情。
- `GET /api/records/{id}/audit`：审计时间线。
- `GET /api/stats`：状态统计。
- `POST /api/records`：创建记录，请求体为`{"reference":"...","data":{...}}`，`data`可带`family_members`列表在收案时登记家属。
- `POST /api/records/{id}/actions/{action}`：执行业务动作，请求体为`{"expected_version":1,"data":{...}}`。
- `GET /api/records/{id}/family`：家属台账（名单、每人待补缺项、变动记录），可带`as_of=YYYY-MM-DD`按指定日期重算，重开即可核对成年等变化。
- `POST /api/records/{id}/family`：登记随行家属，请求体为`{"name":"...","relationship":"spouse/child/parent/sibling/other","birth_date":"YYYY-MM-DD","guardianship_declaration":false}`。
- `POST /api/records/{id}/family/{member_id}/documents`：为对应家属登记材料，请求体为`{"documents":["identity_document",...]}`。
- `POST /api/records/{id}/family/{member_id}/remove`：家属退出，请求体为`{"reason":"..."}`，退出者及其材料保留为历史。

除`/health`和`/`外，请求需提供`X-User-Id`、`X-Role`，可选`X-Org`。

## 随行家属台账

- 收案登记关系与出生日期；未成年子女的`guardianship_declaration`为必需材料，登记时未附则进入待补区。
- 名单新增、退出或年龄变化后，材料按当前人员重算，缺项留在`pending`待补区；材料挂在对应人员名下，互不影响。
- `decide`前逐人核查，任一在家属存在缺项即拒绝并列出每人缺项；案件决定或归档后名单冻结。
- 名单规则（`family_rules`）、材料计算（`family_materials`）与存储（`repository`家属表）分开实现。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

测试覆盖完整流程、规则计算、重复引用、权限拒绝和版本冲突。
