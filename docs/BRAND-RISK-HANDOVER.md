# 品牌风险筛选模块 — 接手说明

> 面向后续接手本模块的开发者。读完能跑、能改、能评测、不踩已踩过的坑。
> 分支：`feat/brand-risk-template`（未合 master，合并前需过金标门 + 全量回测）。

## 1. 解决什么问题

电商铺货场景的商品图上线前风险审核：识别图中是否出现**避雷清单内的品牌 / IP /
标志性设计元素**，输出 `block / review / pass` 三级判定。当前覆盖女装类目
（避雷清单来自客户侧，每周二更新，可增量重抓）。

关键认知（决定架构）：侵权分两类，检测原理完全不同——

| 侵权类型 | 争议点 | 检测通道 | 本模块覆盖 |
|---|---|---|---|
| 商标类（logo / IP 形象 / 印花） | 图**里**印了什么 | 视觉识别 + 清单比对 | ✅ |
| 图片著作权（盗图） | 图**本身**是谁的 | 图片溯源（比对官方图库） | ❌ 另一模块，正例已备好 |
| 款式类（标志性设计语言） | 版型/构造组合 | 款式特征卡核对 | ✅ v1（卡库待扩） |

## 2. 架构：两通道并联 + 规则引擎

```
商品图 ──┬── 元素通道  brand_risk 模板（v1.1.0）
         │    VLM 只负责"认出"品牌/IP/印花元素，不给风险结论
         │    → 规则引擎：377 清单精确/子串/模糊 + 1284 别名表
         │      命中 → block；认出未命中 → review
         │      档口水印（corner_badge+wordmark+未命中）→ pass
         │
         └── 款式通道  brand_risk_style 模板（v1.0.0）+ 款式特征卡
              VLM 按卡片逐项核对构造特征（束腰/荷叶边/廓形等），数命中数
              → ≥4 项 review；5 项全中且置信度 ≥0.9 → block
```

要点：**VLM 永远不直接给风险结论**。判定权在规则引擎（清单+别名+卡片阈值），
这保证了判定可解释、可审计、改规则秒级生效（改数据文件，不动模型）。

## 3. 数据资产

| 路径 | 内容 | 来源 | 更新 |
|---|---|---|---|
| `testdata/yuque-brand-avoidlist.json` | 397 条侵权记录 / 377 品牌 | 客户避雷清单（语雀表格） | 每周二，抓取流程见 skill `yuque-lakesheet-scraping` |
| `testdata/avoidlist-images/` + `manifest.json` | 877 张侵权案例图（892MB，png 不进 git，manifest 进） | 同上 | 同上 |
| `testdata/brand-aliases.json` | 258 品牌 / 1284 别名（权利人→旗下IP/作品） | `scripts/gen_aliases.py` 生成 | 清单更新后重跑 |
| `testdata/negatives-17zwd/` | 167 张真实货盘图 | 17 网公开商品图 | 按需扩 |
| `testdata/style-cards/*.json` | 款式特征卡（含金标引用与判定阈值） | LLM 起草 + 业务方校对 | 逐卡积累 |
| `testdata/golden/` | 金标样本（业务方亲眼确认答案） | 人工确认 | 评审沉淀，只增不改 |
| `testdata/provenance-positives.json` | 301 条著作权类正例 | 清单拆分 | 留给图片溯源模块 |
| `testdata/eval-brand-risk-run2.json` | 最近一次全量回测原始结果 | `scripts/eval_brand_risk.py` | 每轮回测覆盖 |

## 4. 如何运行

```bash
cd sirius-vision-lab && source .venv/bin/activate

# 单元测试（31 个，模板注册/解析/边界）
python -m pytest tests/ -q

# 金标回归门 —— 改模板/规则/卡片后必跑，必须全绿
python scripts/check_golden.py

# 全量回测（正例 100 商标类分层抽样 + 负例全量，约 5-8 分钟，消耗 API 配额）
python scripts/eval_brand_risk.py
```

**纪律：任何改动 → 单测 + 金标门全绿 → 才 commit；改判定逻辑须跑全量回测对比。**

## 5. 当前指标（run2，2026-10-17）

| 指标 | 数值 | 说明 |
|---|---|---|
| 正例拦截率（商标类 100 张） | 58% | block 44 + review 14 |
| 解析错误率 | 0% | 格式修复重试后（曾 18%） |
| 干净负例放行 | 100%（161/161） | 零误报 |
| 真实货盘意外抓雷 | 13 个 | 17 网/wxw 在售图中的清单品牌（浓度 7.8%） |
| 单图延迟 | 4-8s | 主 provider |

miss 归因（42 张 pass）：36 张图上无视觉证据（大概率标题侵权，需标题数据流），
6 张目标元素未认出（小 logo/图形商标，待 few-shot 参考图）。

## 6. 工程坑位（踩过的，别再踩）

1. **schema 必须注入 prompt**：`模板 prompt + '\nSchema: ' + json.dumps(schema)`，
   服务层 `main.py` 有此拼接；绕过服务直调 API 时漏掉它，模型会自编字段名。
2. **输出先剥 think 块再解析**：`Template.parse()` 已内置；自己写解析会踩。
3. **parse 失败要格式修复重试**：把原输出+校验错误发回模型修一次，错误率 18%→0。
4. **文本 provider（别名生成用）只接受默认采样参数**，传 temperature 直接 400；
   模型名以 `scripts/gen_aliases.py` 内为准，别凭记忆写。
5. **语雀表格抓取**：数据在页面 API 的 zlib 压缩字段里，全流程见 skill
   `yuque-lakesheet-scraping`（含 CDP 5s 超时静默、分块传回等坑）。
6. **图片目录别进 git**：`testdata/avoidlist-images/*.png`（892MB）已 ignore，
   manifest 和其他小图正常进。
7. **KPI 必须分类目算**：商标类和著作权类混在一起算拦截率是失真的（52% vs 真实 58%）。
8. **水印降级是双刃剑**：它消除档口水印噪声，但款式类风险图可能只有水印+款式特征
   （金标 zimmermann-style-01 就是案例）——款式通道独立于水印逻辑，两通道并联就是这个原因。

## 7. 迭代方向（优先级从高到低）

1. **L0 标题文本层**：36 张 miss 大概率是标题侵权（图干净、listing 标题写品牌词）。
   需要业务侧商品标题数据流，标题含清单品牌词 → 直接拦。零模型成本。
2. **款式卡库扩充**：当前 1 张卡（ZIMMERMANN）。从清单高频权利人里挑 10-30 个
   "款式被大量抄袭"的品牌建卡（LLM 起草 → 业务校对 → 金标入库）。
3. **few-shot 参考图**：小 logo/图形商标识别增强，高频品牌标准 logo 图注入 prompt。
4. **服务化**：规则引擎目前只在评测脚本里；正式并入 `/v1/analyze` 的 brand_risk /
   brand_risk_style 端点（改 `main.py`，走分支+验收流程）。
5. **图片溯源模块**：301 条著作权正例已备好，路线=品牌官方图库 embedding 比对。

## 8. 分支与合并

- 当前分支 `feat/brand-risk-template`，基于 `feat/vision-service`。
- 合并顺序：先合 `feat/vision-service`（M1 已验收），再合本分支。
- 合并门：单测 31 绿 + 金标门绿 + 全量回测不低于当前指标。
- master = 绝对正确可回滚版本，先部署验证后合入。
