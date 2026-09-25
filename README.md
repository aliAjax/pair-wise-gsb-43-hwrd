# 公共采购密封投标与评审系统

标准库实现的招标发布、密封投标、开标校验收、规则评分、利益冲突、澄清、废标、投诉重评和授标快照服务。

## 运行

要求 Python 3.11+（当前 Python 3.9 环境亦可）。

```bash
python3 app.py --init --seed
python3 app.py
```

默认地址 `http://127.0.0.1:8209`，数据库默认 `public_procurement.db`。

## 主要接口

使用 `X-User`、`X-Role` 请求头。角色有 `procurement`、`vendor`、`evaluator`、`supervisor`、`auditor`、`public`。

- `GET /health`、`GET /api/state`、`GET /api/tenders/{id}`
- `POST /api/vendors`、`POST /api/tenders`（可传 `bond_required` 设定投标保证金）、`POST /api/tenders/publish`
- `POST /api/bonds/deposit`：开标前缴纳/补缴投标保证金；`POST /api/bonds/refund`：开标前申请退回（有未撤回投标时拒绝）
- `GET /api/tenders/{id}/bonds`：查询项目保证金账户与流水（供应商仅见本人）
- `POST /api/bids`、`POST /api/bids/withdraw`、`POST /api/bids/disqualify`
- `POST /api/tenders/open`：截止后开标并核验承诺哈希
- `POST /api/conflicts`、`POST /api/evaluations`
- `POST /api/clarifications`、`POST /api/clarifications/answer`
- `POST /api/complaints`、`POST /api/complaints/resolve`
- `POST /api/tenders/award`：锁定评分轮次并保存排名快照

## 投标保证金

- 建项目时通过 `bond_required` 规定金额；供应商在投标前用 `/api/bonds/deposit` 缴纳，可多次补缴。
- 递标时校验实缴金额，不足直接拒绝（HTTP 402）；开标前可补缴，也可 `/api/bonds/refund` 退回（仍有未撤回投标时必须先撤回）。
- 开标瞬间，所有开标供应商的保证金从 `active` 锁为 `locked`，之后不能缴、不能退；开标校验失败则整批回滚。
- 授标时按结果一次性结算：中标供应商 `converted`（转为履约保证金）、被废标供应商 `forfeited`（没收）、其余供应商 `returned`（原路退回），结果写入授标快照 `bond_settlement`。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

测试覆盖完整开标授标、截止前正文隐藏、利益冲突、重复评分覆盖、投诉重评、角色权限，以及投标保证金足额校验、开标前退回、开标锁死和授标结算。

## 局限

供应商与请求用户没有绑定校验，身份仍依赖请求头；投标正文虽然按接口阶段隐藏，但数据库本身未加密；评分规则适合演示，不覆盖复杂资格预审、电子签名和采购法规差异。保证金为账面记账，未对接真实支付通道。
