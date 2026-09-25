# 公共采购密封投标与评审系统

标准库实现的招标发布、密封投标、开标校验收、规则评分、利益冲突、澄清、废标、投诉重评、投标保证金和授标快照服务。

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
- `POST /api/vendors`、`POST /api/tenders`、`POST /api/tenders/publish`
- `POST /api/bids`、`POST /api/bids/withdraw`、`POST /api/bids/disqualify`
- `POST /api/bonds/pay`：供应商按项目要求缴纳/补缴投标保证金（`amount`、`channel`、`vendor_id`）
- `POST /api/bonds/refund`：开标前申请退回保证金（可部分退回，默认全额）
- `POST /api/tenders/open`：截止后开标并核验承诺哈希，同时锁定参标供应商保证金
- `POST /api/conflicts`、`POST /api/evaluations`
- `POST /api/clarifications`、`POST /api/clarifications/answer`
- `POST /api/complaints`、`POST /api/complaints/resolve`
- `POST /api/tenders/award`：锁定评分轮次并保存排名快照

## 测试

```bash
python3 -m unittest discover -s tests -v
```

测试覆盖完整开标授标、截止前正文隐藏、利益冲突、重复评分覆盖、投诉重评、角色权限和投标保证金全流程。

## 投标保证金规则

- 建项时通过 `bond_amount` 设定保证金要求（默认 0 即不要求）。
- 供应商开标前可分笔缴纳或补缴（`/api/bonds/pay`，需记录缴费渠道 `channel`）；保证金余额不足项目要求时不能递交投标。
- 开标前可申请退回（`/api/bonds/refund`，支持部分退回）：已递交密封标的，退回后余额仍须足额，否则需先撤回投标；未参标供应商开标后仍可退回。
- 开标时所有有效（密封）投标供应商的保证金立即锁定，不能补缴或退回。
- 开标后被废标的供应商，锁定的保证金全额没收。
- 授标结算：中标供应商的保证金转为履约保证金；其余有效投标人按原缴费渠道逐笔原路退回（多渠道缴纳按最早缴费优先冲销）。结算结果写入授标快照与审计时间线。

## 局限

供应商与请求用户没有绑定校验，身份仍依赖请求头；投标正文虽然按接口阶段隐藏，但数据库本身未加密；保证金只做账面台账和渠道记录，不对接真实支付网关；评分规则适合演示，不覆盖复杂资格预审、电子签名和采购法规差异。
