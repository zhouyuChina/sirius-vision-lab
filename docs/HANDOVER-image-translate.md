# 图上文字翻译服务 — 调用与接手文档

> 服务名：追光 AI 视觉（图像翻译模块） · 版本 image_translate v0.1.0 · 2026-10-21 上线
> 本文档面向接手跟进的开发/运维同学，覆盖调用方式、参数语义、后台复核、服务器运维。

---

## 1. 这是什么

一个 HTTP API 服务：**输入一张带文字的图，输出译文；可选直接输出"翻译后的成品图"**。

当前支持 **中文 ↔ 俄语** 双向（`target_lang: ru / zh`），主场景是电商商品图出海（中文营销图 → 俄语版）。

三种产出形态由 `render` 参数控制：

| render | 干什么 | 延迟预期 | 适用 |
|---|---|---|---|
| `none` | 只出结构化译文（原文/译文/位置） | ~20s | 只要文本，自己排版 |
| `pil` | 程序化重绘成品图：文字在纯色底（角标/色带）上时，底色覆盖+译文重绘，背景商品像素零改动 | ~25s | 电商营销图（约 90% 是这种形态），成本≈0 |
| `edit` | 整图交 AI 编辑模型按对照表逐字替换（复杂背景文字也行），双引擎主备，全灭自动降级 `pil` | 30~130s（2K 出图慢属正常） | 文字印在衣服图案上/实景图里的字 |
| `auto`（默认） | 自动判断：全部文字在纯色底 → 走 `pil`；有复杂背景 → 走 `edit` | 视图而定 | 无脑用这个就行 |

---

## 2. 快速开始

### 2.1 一发 curl

```bash
curl -sk -X POST "https://101.37.175.27/vision/v1/analyze" \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "task": "image_translate",
    "image_base64": "'"$(base64 -w0 test.jpg)"'",
    "options": {"target_lang": "ru", "render": "auto"}
  }'
```

> `image_url` 与 `image_base64` **二选一**（`image_url` 传公网可访问的 https 图链）。
> 浏览器打开 `https://101.37.175.27/vision/...` 会有证书告警（IP 证书），API 调用加 `-k` 或忽略校验即可，与管理后台同口径。

### 2.2 响应示例

```json
{
  "id": "79949b65ee5f461a89a254234480fbd4",
  "task": "image_translate",
  "template_version": "0.1.0",
  "segments": [
    {"id": 1, "source_text": "2026新款", "translated_text": "Новинка 2026",
     "position": "右上角红标签", "background_type": "solid_band"},
    {"id": 2, "source_text": "柔软透气 · 不起球 · 亲肤面料",
     "translated_text": "Мягкая, дышащая · без катышков · приятная коже",
     "position": "中部白条", "background_type": "solid_band"},
    {"id": 3, "source_text": "限时特价 ¥59.9 顺丰包邮",
     "translated_text": "Спеццена ¥59.9 · бесплатная доставка SF Express",
     "position": "底部粉条", "background_type": "solid_band"}
  ],
  "render": {
    "mode": "auto", "engine": "pil",
    "output_image_id": "1dd76ea3092969a9...",
    "output_url": "https://101.37.175.27/vision/v1/outputs/1dd76ea3092969a9...",
    "rendered_items": [1, 2, 3], "skipped_items": [],
    "fallback_from": null, "error": null
  },
  "usage": {"total_tokens": 4027},
  "latency_ms": 25826
}
```

### 2.3 关键字段

| 字段 | 含义 |
|---|---|
| `segments[]` | 逐条译文。`background_type`: `solid_band`（纯色底）/ `complex`（复杂背景） |
| `render.engine` | 实际渲染引擎：`pil`（程序化重绘）/ 两个 AI 编辑引擎代号（主/备）/ `null`（没出图）——代号仅为日志标识，无需关心是哪家 |
| `render.output_image_id` | 成品图的存储 ID，取图见 §4 |
| `render.output_url` | 成品图的绝对 HTTPS 下载地址；下载时仍需带 `X-API-Key` |
| `render.rendered_items` / `skipped_items` | 渲染了/跳过了哪些条目（`pil` 模式会跳过 complex 条目） |
| `render.fallback_from` | 降级路径：`null`=一次成功；`主引擎名`=主挂了备顶上；`主->备`=双引擎全灭降级到了 `pil` |
| `render.error` | 渲染层错误说明（`segments` 译文仍有效） |

**降级语义**：`fallback_from` 非 null 时成品图仍然出了（降级兜底），但复杂背景的文字可能没替换——核对 `skipped_items`。

---

## 3. 接口清单

| 端点 | 说明 |
|---|---|
| `POST /v1/analyze` | 识别/翻译入口，`task` 可选 `image_translate`（本模块）及 `avatar_tag` / `garment_attr` / `brand_risk` / `brand_risk_style`（原有任务，见 API.md） |
| `GET /v1/outputs/{output_image_id}` | 下载 JPEG/PNG/WebP 成品图（二进制响应，需 `X-API-Key`） |
| `GET /v1/tasks` | 所有任务模板 + 输出 schema |
| `GET /v1/records?task=image_translate` | 调用记录查询（管理 key） |
| `GET /v1/stats` | 用量/延迟统计 |

请求体约束：`image_url` / `image_base64` 二选一必填；`options` 仅接受 `target_lang`（ru/zh）与 `render`（auto/pil/edit/none）；图片 ≤20MB、jpg/png/webp；nginx 层 body 上限 25MB。

**客户端超时建议 ≥200s**（`edit` 模式 2K 出图实测 30~130s）。

---

## 4. 管理后台（复核 & 取成品图）

- 地址：`https://101.37.175.27/vision/admin`（登录口令找周宇）
- 记录流：每次调用的输入图 + 参数 + 输出 JSON 全部落库，按任务/状态/日期筛选
- 详情页：左边输入图、右边**成品输出图**，可一键标记 **对 / 错 / 存疑**（复核标记会沉淀为评测基准，翻译质量会越用越准）
- 成品图直链（程序取图）：直接请求响应中的 `render.output_url`，并携带 `X-API-Key`。下载内容是图片二进制，不是 Base64；后台详情页仍可直接查看。

```bash
curl -sk "$OUTPUT_URL" -H "X-API-Key: $API_KEY" -o translated.jpg
```

---

## 5. 服务器运维（101）

| 项 | 位置 |
|---|---|
| systemd 服务 | `sirius-vision`（`systemctl restart sirius-vision`） |
| 代码目录 | `/opt/sirius-vision` |
| 数据目录 | `/var/lib/sirius-vision`（图片内容寻址存储 + sqlite 记录） |
| 环境变量/密钥 | `/etc/sirius-vision.env`（600 权限，API key、后台口令哈希、字体路径、各引擎配置都在这） |
| 日志 | `journalctl -u sirius-vision -f`（渲染失败会打 `edit_render_failed engine=... reason=...`，带具体原因） |
| nginx | include 片段 `/etc/nginx/sites-available/sirius-vision`，挂在 443 server 内 `/vision/` 前缀 |

**更新流程**（改代码后）：本地 master → rsync 到 `/opt/sirius-vision` → 服务器上 `UV_PYTHON_INSTALL_DIR=/opt/uv-python uv sync` → `chown -R sirius-vision:sirius-vision .venv` → `systemctl restart sirius-vision` → 外部打一发真实调用验证。

⚠️ 101 的两个坑：Python 用的是 `/opt/uv-python` 下的独立 3.12（别用系统 3.10，也别装到 /root 下——服务用户无权访问会起不来）；字体路径在 env 里写死（西里尔=DejaVu、中文=文泉驿正黑），换机器部署要同步改。

---

## 6. 已知边界 & 常见问题

| 现象 | 说明 |
|---|---|
| `edit` 模式偶尔 100s+ | 2K 图生成的正常耗时，不是卡死；等或用 `auto`/`pil` |
| 译文偶有省略（如"限时"没译出） | 翻译层按电商风格做了长度约束（防溢出），个别语义取舍；后台标记"存疑"即可，会用于后续调优 |
| `pil` 模式某条没上图 | 该条是 `complex` 背景，`pil` 只处理纯色底；用 `edit` 或 `auto` |
| 大图被拒 | ≤20MB、jpg/png/webp；超大图先压缩 |
| 证书告警 | IP 证书正常现象，`-k` 跳过 |

---

## 7. 交给你的跟进项

1. **接入义乌链路试用**：挑一批真实商品图（先 20~50 张）走 `auto` 模式小批量跑，重点看 pil 覆盖率和 edit 延迟的现场接受度。
2. **复核沉淀**：管理后台每天花几分钟标记对/错/存疑——这是后续提升翻译质量的基准数据。
3. **反馈通道**：质量差/没翻到/溢出的图，直接在后台标"存疑"并截图给周宇转我，会归因后改进模板。
4. 有部署/配置/凭据需求找周宇（key 与后台口令不写在本文档里）。

---

*本文档对应服务 commit `faa042a`。接口契约冻结：只加字段不改名，客户端可放心长期依赖。*
