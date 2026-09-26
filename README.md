# 移民案件期限与材料管理

纯Python标准库实现的移民案件期限与材料管理原型，使用SQLite持久化，HTTP接口由`http.server`提供。

## 模块结构

- `app.py`：命令行参数、依赖组装和服务启动。
- `src/domain.py`：领域数据类型、错误和基础校验。
- `src/rules.py`：状态转换、法定天数、补件期限和材料完整性和冲突检查。
- `src/roster.py`：随行家属名单规则——收案登记关系与出生日期、新增/退出、未成年/成年判定与变动记录。
- `src/documents.py`：逐人材料计算——按当前名单重算要求与缺项（未成年子女附监护声明）、材料挂到人员名下、决定前逐人核对。
- `src/repository.py`：SQLite建表、事务和查询（名单、材料与变动记录随案件payload持久化）。
- `src/service.py`：用例编排、权限检查、乐观并发和审计。
- `src/http_api.py`：HTTP路由与统一错误响应。
- `src/audit.py`：事件时间线。
- `static/index.html`：演示页面（名单、待补区、变动记录、逐人操作）。
- `tests/`：完整流程、规则计算、家属台账和失败场景测试。

名单规则（`roster.py`）、材料计算（`documents.py`）与存储（`repository.py`）分开实现，互不依赖内部细节。

## 启动

```bash
python3 app.py --db ./data.db --port 8329
```

默认端口为`8329`，默认数据库位于项目目录。服务启动时自动建表。

## 主要接口

- `GET /health`：健康检查。
- `GET /`：演示页面。
- `GET /api/records`：记录列表，可带`state`和`limit`参数。
- `GET /api/records/{id}`：记录详情（含随行名单、逐人缺项、待补区和名单变动记录）。
- `GET /api/records/{id}/audit`：审计时间线。
- `GET /api/stats`：状态统计。
- `POST /api/records`：创建记录，请求体为`{"reference":"...","data":{...}}`，`data.members`可登记随行家属`[{"name":"...","relationship":"child","birth_day":50}]`。
- `POST /api/records/{id}/actions/{action}`：执行业务动作，请求体为`{"expected_version":1,"data":{...}}`。

除`/health`和`/`外，请求需提供`X-User-Id`、`X-Role`，可选`X-Org`。

## 随行家属台账

- 收案登记每位家属的关系（`spouse/child/parent/sibling/other`）与出生日期（日偏移），未成年子女自动要求监护声明。
- 名单动作（不改变案件状态，决定后不可再变动）：
  - `add_member`：`{"name","relationship","birth_day","current_day"?}`，登记后立即按当前人员重算材料，缺项进入待补区。
  - `withdraw_member`：`{"member_id","reason"?,"current_day"?}`，退出者不再产生新要求，名下材料与变动记录保留。
  - `attach_document`：`{"person_id","documents":[...]}`，材料挂到对应人员名下。
  - `verify_person`：`{"person_id"}`，逐人核对，缺项未清不可核对。
- 子女成年后（按`current_day`判定）监护声明要求自动移除，并写入变动记录。
- `decide`前逐人核完：任一在册人员有缺项或未核对即被拦截（`supervisor_waiver`可豁免）。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

测试覆盖完整流程、规则计算、家属名单变动与材料重算、决定前逐人核对、重复引用、权限拒绝和版本冲突。
