# sirius-vision-lab — 图像识别服务（API + 管理后台）

> 内部代号 sirius-vision-lab（客户可见白标：追光 AI 视觉）。
> **2026-10-08 周宇定方向：全部产品走 API 形态，推理与识别交给大模型（不自训），
> 带管理后台（管理每次 API 输入/输出），后台逐步完善；先完成再看效果。**

## 定位与边界

- **做什么**：图像识别服务——头像打标/质检、商品图属性识别。prompt+schema 模板化，
  对外统一 API + 自带管理后台（调用记录、输入图、输出结果全落库可查）。
- **不做什么**：不做业务逻辑（业务归属各项目）；不自训模型（训练提案降级为远期备胎）；
  不重复造轮子（生成→image-forge，存储→image-vault）。
- **分工**：紫微负责规划、拆解、评测、验收；coding 类实现给 Codex 等过验收门。

## 产品形态（2026-10-08 定）

**所有产品全部走 API 接口**（卖给有开发能力的客户）；B 线（精品账号运营）是
第一个调用方；义乌商品图识别复用同一服务。管理后台是产品的一部分：每次调用的
输入图 + 输出结构化结果都落库，可查、可筛、可人工复核。

```
调用方（B线/客户/义乌管线） ──HTTPS──▶ 识别服务（FastAPI）
  API 面：
  ├─ POST /v1/analyze            识别入口 {task, image, options}
  ├─ GET  /v1/tasks              任务模板列表 + schema
  ├─ GET  /v1/records            调用记录查询（管理 key）
  └─ GET  /v1/stats              用量/延迟/成本统计
  管理后台 /admin：
  ├─ 调用记录流：缩略图 + 输入参数 + 输出 JSON，按任务/时间/状态筛选
  ├─ 详情复核：大图 + 字段级结果，一键标记 对/错/存疑（自然沉淀金标集）
  └─ 统计看板：调用量、延迟分布、token 消耗、复核进度
  底层：
  └─ 任务模板库（每任务 prompt+schema 一版本） ──▶ MiniMax / Kimi / GLM vision API
```

## 模型底座（2026-10-08 实测，多 provider 可切换）

| Provider | 端点 | 模型 | 实测（19店白底图单图快测） |
|---|---|---|---|
| **MiniMax**（主力） | `api.minimaxi.com/v1` | `MiniMax-M3` | 2.3-6.7s 最快；⚠️ `<think>` 泄漏进 content 需剥离 |
| **Kimi**（备胎） | `api.kimi.com/coding/v1` | `kimi-for-coding` | 13.1s；纯 JSON 直出最干净 |
| GLM（三选） | 智谱 coding paas v4 | `glm-5.3-flash` | 4.3s；⚠️ glm-5.3/glm-4.7 拒图（code 1210），flashx 套餐无权限 |

选型依据（周宇 2026-10-08）：MiniMax/Kimi 多模态更强，GLM/DeepSeek 推理更强——
识别任务取多模态强项。单图快测三家识别结果都对，差距在速度/工程顺手度；
**正式定盘等 P0 三方横评**（24 图 × 3 次 × 3 模型，字段级准确率+幻觉率）。
key 全在 env（~/.hermes/.env 有 MiniMax/KIMI/Z_AI 三套），不进仓库。

⚠️ 合规备注：三家均走 Coding Plan 类个人订阅，MVP/内部用没问题；**对外收费卖之前**
需评估切换商用按量 API（换 base_url+key 即可，代码不用动）。

## 任务模板（种子，跑通前可调）

### avatar_tag 头像打标（MVP 首个任务）

| 维度 | 字段 |
|---|---|
| 类型 | avatar_type: real_person / cartoon / scenery / logo / text |
| 人物属性 | gender_feel: female/male/unclear；age_feel: teen/young/middle/senior/unclear；face_view: frontal/side/unclear |
| 风格调性 | style_tags: sweet/cool/elegant/professional/casual/artistic/…（多选） |
| 技术质检 | face_ratio: 0-1；clarity: high/mid/low；occlusion: none/partial/heavy |
| 风险 | risk_flags: revealing/sensitive_symbol/none（多选） |
| 置信度 | 字段级 confidence: 0-1 |

### garment_attr 商品图属性（第二个任务，字段沿用 P0 评测设计）

类目/颜色/版型/袖长/领型/图案/适用场景。

输出统一形状：`{task, model, template_version, fields…, usage, latency_ms}`。

## 落库模型（管理后台的数据基础）

每次 /v1/analyze 调用一条记录：`id, task, template_version, model, status,
input_image(内容寻址存储), input_options, raw_output, parsed_fields, error,
latency_ms, tokens, created_at, review(对/错/存疑/null), review_note`。
复核标记天然积累金标集——管理后台用着用着，评测数据就有了。

## 评测（P0，服务跑通后做）

- testdata/wxw/ 24 张商品图（garment_attr）+ 头像样本 ≥20 张（avatar_tag；
  无业务数据阶段可先用公开头像样图占位）
- 每任务 × 3 次重复拍一致性；指标：字段级准确率（后台复核即金标）、幻觉率、延迟、成本
- 跑法：bench/ 脚本 + results/ JSON，结论写 docs/，禁止只跑不存

## 目录约定

```
testdata/    # 评测图（wxw/ 已有 24 张；avatars/ 占位）
src/         # 识别服务代码（FastAPI + 模板库 + 管理后台）
bench/       # 评测脚本
docs/        # 结论、接入规范
results/     # 评测原始 JSON + 汇总
deploy/      # systemd + nginx 片段（部署 101 时用）
```

## 部署（验收后）

- 目标 101，模式复用 image-vault：systemd 专用用户 + nginx HTTPS 反代 + env 密钥
- **端口 8902**（8900=vault、8901=追光后台已占）；nginx 命名空间 `/vision/`
  （避开 vault 的 /v1 /img /admin 和追光后台的 /api /assets /downloads /screenshot）
- 生产写操作先报方案获周宇同意再执行

## 接入规范

- 统一入口：各项目调图像识别一律走本服务，不直接绑死某家 API
- Prompt/Schema 版本化：prompt 与输出 schema 同版本管理，回滚可追溯
- 对外白标：追光 AI 视觉；文档/报错/界面不出现内部项目名、底层模型名、通知通道
- 认证：调用方 `X-API-Key`（多 key，按客户分发）；管理后台独立口令

## 远期备胎（挂起，不投入）

- 训练提案（蒸馏式：API 标注→小模型→自有推理）：docs/PROPOSAL-training.md
  触发条件：月推理量 >5 万张且 API 成本/SLA 扛不住时再评审
