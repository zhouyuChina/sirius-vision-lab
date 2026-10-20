# TASK: image_translate 模板（三段管线：提取→翻译→渲染）

## 纪律（第一行，先读）

1. 只在当前分支 `feat/image-translate` 工作，**不部署、不 push、不碰服务器、不合并 master**。
2. 全部单测不得调用真实外部 API（用 fake adapter / mock HTTP）；真实 key 由验收方注入。
3. 完成标准：`uv run pytest -q` 全绿 + `uv run python scripts/check_golden.py` 全绿 + 全部交付物落盘并 commit。
4. Implement NOW. Do NOT ask clarifying questions, do NOT pause for confirmation — this task is pre-approved. Work silently and commit at the end.

## 背景

sirius-vision-lab 是图像识别服务（FastAPI，端口 8902）：任务模板（prompt+JSON schema 同版本）→ 多 provider vision API → 每次调用落库 + admin 复核后台。现有任务：avatar_tag / garment_attr / brand_risk / brand_risk_style。代码在 `src/sirius_vision/`（main.py 端点、provider.py 适配层、templates/ 模板、storage.py 存储、images.py 图像工具）。**`/v1/*` 接口面已冻结**（有外部部署在用）：允许加字段/加模板，禁止改名/删除/换认证。

本任务新增 `image_translate`：把图上文字在中文↔俄语间翻译，可选把译文重绘回图上。业务场景：义乌电商商品图出海（Ozon）。

## 管线架构（三段，全部同步完成于一次 /v1/analyze 调用内）

```
输入图 ─▶ ① 提取(MiniMax-M3 vision) ─▶ 文字条目[{text, band_bbox, colors, background_type}]
       ─▶ ② 翻译(GLM-5.3 text) ─▶ [{id, ru}]
       ─▶ ③ 渲染(render 选项) ─▶ 输出图(存储) 或 纯文本
```

### ① 提取段（复用现有 vision provider）

新增模板 `src/sirius_vision/templates/image_translate.py`，`Template('image_translate', version='0.1.0')`。
prompt 要求 VLM 输出（schema 严格约束，服务层会按现有方式注入 `prompt + '\nSchema: ' + json.dumps(schema)`）：

```json
{"items": [{"id": 1, "text": "原文", "position": "右上角红标签",
  "band_bbox": [683, 12, 985, 58], "bg_hex": "#DE252D", "text_hex": "#FFFFFF",
  "background_type": "solid_band"}]}
```

- `band_bbox`：归一化 0-1000 四元组 [x1,y1,x2,y2]，含底色块的完整矩形（比文字宽、留边距）；文字直接印在复杂背景（衣服图案/实景）上时为 `null` 且 `background_type="complex"`。
- 已实测该 prompt 在 MiniMax-M3 上提取 3/3 准确（参考 scratch 探活脚本，不要改动核心措辞：明确要求"图上每块文字都在一个纯色底（标签/色带）上…band_bbox 归一化0-1000坐标"）。

### ② 翻译段（text-only chat，扩展 provider 层）

现有 `ChatAdapter` 是 OpenAI 兼容格式；新增 text-only 调用路径（content 不带 image_url），复用 providers 配置里的 glm（`Z_AI_API_KEY` / `GLM_BASE_URL`，模型 env `TRANSLATE_MODEL` 默认 `glm-5.3`）。
prompt 约束（电商质量，全部写进模板常量）：
- 电商促销风格、简洁，译文长度尽量不超过原文长度的 1.3 倍（俄语比中文长，防止渲染溢出）
- 数字/价格/货币符号原样保留（2026、¥59.9）
- 物流品牌词保留英文（顺丰→SF Express）
- 输出严格 JSON：`{"translations": [{"id": 1, "ru": "..."}]}`；解析失败重试 1 次，仍失败则整条记录 status=error（error_code=translate_parse_failed）

### ③ 渲染段（新模块 `src/sirius_vision/renderer/`）

`options.render` ∈ `auto`(默认) | `pil` | `edit` | `none`：

| 模式 | 行为 |
|---|---|
| none | 只返回 segments，不出图 |
| pil | 仅程序化重绘 band 条目；complex 条目跳过（进 skipped_items） |
| edit | 整图交图像编辑 API（处理所有条目，含 complex） |
| auto | 全部条目有 band → 走 pil；任一条目 complex → 走 edit |

**PIL 渲染器**（`renderer/pil_render.py`）——已实测验证的算法：
1. 对每个 band 条目：用 `bg_hex` 填充 `band_bbox` 映射回像素的矩形（完美抹字）
2. 用 `text_hex` 绘制译文，居中；字体 env `FONT_PATH_CYRILLIC`（默认 `/System/Library/Fonts/Supplemental/Arial Bold.ttf`，macOS；Linux 部署时改 env），原生支持西里尔
3. 自适应字号：初始 `int(band高*0.52)`，宽/高超限则 -2 循环，最小 10；仍放不下则允许溢出记录 warning（不许截断）
4. 依赖 Pillow（加进 pyproject dependencies）

**Edit 渲染器**（`renderer/edit_render.py`）——两个引擎已实测通过，主备链：

主 Seedream（火山 ARK）：
- `POST https://ark.cn-beijing.volces.com/api/v3/images/generations`
- headers: `Authorization: Bearer ${ARK_API_KEY}`
- body: `{"model": "${ARK_SEEDREAM_MODEL 默认 doubao-seedream-5-0-pro-260628}", "prompt": <编辑指令>, "image": ["data:image/jpeg;base64,..."], "response_format": "url", "watermark": false}`
- 响取 `data[0].url` 下载
- 编辑指令模板（英文，实测逐字忠实）：`Edit this e-commerce image: replace ONLY the listed Chinese text with the given Russian text. Render the Russian verbatim in the SAME place, same text color, same badge/band background, centered, never truncate. Do not change anything else (model, clothes, pose, background, lighting).` + 逐条 `N. <position> (<原文>) -> <译文>`

备 Qwen（阿里 dashscope workspace）：
- `POST https://${DASHSCOPE_WORKSPACE_HOST 默认 llm-9ucxkul4g12vzutq.cn-beijing.maas.aliyuncs.com}/api/v1/services/aigc/multimodal-generation/generation`
- headers: `Authorization: Bearer ${DASHSCOPE_API_KEY}`
- body（原生 multimodal 格式）：`{"model": "${DASHSCOPE_IMAGE_MODEL 默认 qwen-image-3.0-pro}", "input": {"messages": [{"role": "user", "content": [{"image": "data:image/jpeg;base64,..."}, {"text": <同上编辑指令>}]}]}}`
- 响取 `output.choices[0].message.content` 中首个 `{"image": url}` 下载

降级链：edit 模式下 Seedream 失败（HTTP 5xx/超时/响应无图）→ 试 Qwen → 仍失败且有 band 条目 → 降级 PIL 渲染（band 条目），`render.fallback_from` 记录降级路径；全灭则 record status=ok 但 render.error 说明渲染失败（segments 文本仍有效）。

### API / 落库 / admin

- `/v1/analyze` 请求：`{"task": "image_translate", "image": <现有格式>, "options": {"target_lang": "ru"|"zh"（默认 ru）, "render": <上述四值，默认 auto>}}`
- 响应 `parsed_fields`（自由 dict，冻结契约允许）：
```json
{"segments": [{"id": 1, "source_text": "...", "translated_text": "...", "position": "...", "background_type": "solid_band"}],
 "render": {"mode": "auto", "engine": "pil|seedream|qwen30|null", "output_image_id": "Ab12Cd34",
            "rendered_items": [1,2], "skipped_items": [3], "fallback_from": null, "error": null}}
```
- 输出图走现有 `storage.save_image()`，得 image_id；record 正常落库（task/image_translate，latency 含三段总和）
- admin 后台：新增 `GET /admin/records/{id}/output_image`（session 保护，复用现有 image 端点写法）；`static/admin/app.js` 详情页在 record 有 `render.output_image_id` 时显示输出图（输入图旁）
- `docs/API.md` 增补 image_translate 章节（对外白标：不得出现内部项目名/供应商名，端点示例用 `<your-image-edit-provider>` 占位风格与现有文档一致；先读现有 API.md 学口径）
- `.env.example` 增补新 env（占位符，不写真实 key）

## 测试要求（全部离线）

- fake vision/text adapter 注入：提取 3 条目、翻译成功/失败、none/pil/edit/auto 四模式
- PIL 渲染：真实 PIL 小图（Pillow 依赖本机可用），验证 band 填充色/居中/字号自适应
- edit 渲染：mock HTTP（seedream 成功 / seedream 失败→qwen 成功 / 全失败→降级 pil）
- API 端到端（TestClient + fake adapter）：image_translate 全链路 + 现有 4 个模板回归不受影响
- `uv run pytest -q` 全绿；`uv run python scripts/check_golden.py` 全绿

## 交付物

新增：templates/image_translate.py、renderer/（pil+edit+__init__）、tests/test_translate.py（或拆多个）、.planning 保留本任务书
修改：main.py（路由/analyze 分支到管线）、provider.py（text-only 路径）、config.py（新 env）、pyproject.toml（Pillow）、.env.example、docs/API.md、static/admin/app.js + index.html（输出图）
commit message：`feat(image-translate): three-stage pipeline (extract->translate->render) with pil/edit renderers`
