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

### brand_risk 品牌风险元素识别（第三个任务，v1.1.0）

电商铺货防侵权：识别商品图中的品牌 logo / IP 形象 / 印花元素。
**两段式**——VLM 只负责"认出"，判定权在规则引擎（避雷清单 + 别名表），
输出 block / review / pass。配套评测框架与真实货盘回测。

### brand_risk_style 款式特征核对（第四个任务，v1.0.0）

款式级侵权检测：按品牌"款式特征卡"（5 项标志性构造特征）逐项核对，
命中 ≥4 项 review、5 项全中且置信度 ≥0.9 block。卡库见 testdata/style-cards/。

输出统一形状：`{task, model, template_version, fields…, usage, latency_ms}`。

## 品牌风险筛选线（2026-10-17 v1 收口，已合 master）

义乌客户实战需求的第一个完整落地场景，**接手文档：[docs/BRAND-RISK-HANDOVER.md](docs/BRAND-RISK-HANDOVER.md)（必读）**。

| 项 | 状态 |
|---|---|
| 数据资产 | 避雷清单 377 品牌/397 记录（语雀，每周二更新）、877 张案例图、别名表 1284 条、167 张真实货盘负例、款式卡（ZIMMERMANN 首卡）、金标样本 |
| 回测指标（run2） | 商标类拦截 58%、解析错误 0%、干净负例 100% 放行（零误报）、17 网在售图真实抓雷 13 个（浓度 7.8%） |
| 评测资产 | `scripts/eval_brand_risk.py`（全量回测）、`scripts/gen_aliases.py`（别名生成） |
| **金标门** | `scripts/check_golden.py` —— **改模板/规则/卡片后必跑，全绿才 commit** |

架构与坑位详见接手文档第 2、6 节。著作权类（盗图）301 条正例已拆分至
`testdata/provenance-positives.json`，留给未来的图片溯源模块。

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
testdata/    # 评测数据（wxw/ 24张、avoidlist-images/ 案例图索引、negatives-17zwd/、
             #   style-cards/ 款式卡、golden/ 金标、brand-aliases.json 等，见接手文档§3）
src/         # 识别服务代码（FastAPI + 模板库 + 管理后台）
scripts/     # 评测与工具（eval_brand_risk / gen_aliases / check_golden）
bench/       # 评测脚本（P0 横评预留）
docs/        # 结论、接入规范、接手文档
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

## M1 本地开发

`uv sync` → `uv run pytest -q` → `uv run uvicorn sirius_vision.main:app --host 127.0.0.1 --port 8902`。
启动前按 `.env.example` 设置进程环境；管理后台使用 HTTPS。
详见 [开发与运行说明](docs/DEVELOPMENT.md) 和 [API 接入契约](docs/API.md)。

## 纪律（接手者必读）

1. 任何改动 → 单测 + 金标门（`scripts/check_golden.py`）全绿 → 才 commit
2. master = 绝对正确可回滚版本；功能开发拉分支，验收后合并
3. 金标样本只增不改；避雷清单每周二更新，抓取流程见 skill `yuque-lakesheet-scraping`
