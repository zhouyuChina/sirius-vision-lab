# 任务拆解：识别服务 MVP（API + 管理后台）

> 2026-10-08 紫微拆解。执行：Codex（feat/vision-service 分支）；验收：紫微。

## M1 服务核心（一次交付）

**M1.1 骨架与配置**
- FastAPI + uv，Python 3.12，src/sirius_vision/
- env：`VISION_API_KEYS`（逗号分隔多 key）、`VISION_ADMIN_PASSWORD_HASH`、
  `VISION_SESSION_SECRET`、`Z_AI_API_KEY`、`VISION_MODEL=glm-5.3-flash`、
  `VISION_BASE_URL`（默认智谱 coding paas v4）、`VISION_ROOT`（数据目录）
- base_url+key 从环境读，不硬编码；写 `.env.example`

**M1.2 调用层（多 provider 适配）**
- provider.py：统一适配器接口，三家端点（base_url+key 全走 env，不硬编码）：
  - MiniMax：`api.minimaxi.com/v1`，模型 `MiniMax-M3`（主力，2026-10-08 实测最快 2.3s）
  - Kimi：`api.kimi.com/coding/v1`，模型 `kimi-for-coding`（备胎，13s 但输出最干净）
  - GLM：智谱 coding paas v4，模型 `glm-5.3-flash`（三选，⚠️ glm-5.3/4.7 拒图 code 1210）
- ⚠️ 实测坑：MiniMax-M3 把 `<think>...</think>` 泄漏进 content 字段——解析器必须先剥离
  think 块再取 JSON；Kimi/GLM 输出干净（GLM 走 reasoning_content 分离）
- 超时/重试有界、429 退避；记录 latency_ms + token usage
- 模型选择：env 默认值 + 任务模板可覆盖（`preferred_model` 字段），P0 三方横评后定盘

**M1.3 任务模板库**
- templates/avatar_tag.py + templates/garment_attr.py：prompt + 输出 JSON Schema + 解析器
- 解析失败/不合 schema → 重试一次（prompt 附加"只输出 JSON"）；仍失败 → 记录 status=parse_error
- 模板带 version 字段，进每条记录

**M1.4 API 面**
- POST /v1/analyze {task, image_url | image_base64, options?} → 同步返回结果
- GET /v1/tasks、GET /v1/records（管理 key）、GET /v1/stats
- 认证 X-API-Key；错误响应不泄漏内部信息（白标）

**M1.5 落库**
- SQLite WAL：records 表（README 落库模型）+ api_keys 表
- 输入图内容寻址存 data/（sha256），records 存引用——不重复造图床，但管理后台要能显示图

**M1.6 管理后台（零构建单页，风格贴 image-vault admin）**
- /admin 登录（独立口令 + session cookie，Secure/HttpOnly/SameSite）
- 调用记录流：缩略图 + task + status 筛选；详情大图 + 输入输出 JSON 对照
- 复核：对/错/存疑 三键 + 备注（写入 review 字段）
- 统计：近 7 天调用量、p50/p95 延迟、token 消耗、复核进度

**M1.7 测试**
- pytest：模板解析（合schema/不合schema）、API 认证、记录落库、复核写回、
  provider mock 的 analyze 全链路；真实 API 冒烟单独 -m slow

## M2 验收（紫微）
- 本地 E2E：curl 上传真图 → glm-5.3-flash 真实返回 → 记录出现在后台 → 复核标记写回
- 24 张 wxw 图 + ≥20 张头像图实跑，抽检 10 张人工核对字段
- 安全：无 key 拒绝、错误信息白标 grep（无 GLM/zai/bigmodel 字样）、admin 未登录 307

## M3 部署 101（需周宇授权）
- 端口 8902、nginx /vision/ 命名空间、systemd 专用用户、env 密钥
- deploy/nginx-vision.conf + image-vision.service + deploy/README.md
- 线上验证后合 master（分支纪律）

## 明确不做（MVP）
- 异步批量、多租户计费、Claude 兜底路由、签名 URL、图片编辑
