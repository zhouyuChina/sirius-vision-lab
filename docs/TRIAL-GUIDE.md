# 追光 AI 视觉 · 试用说明

> 内部试用版（2026-10-08）。图像识别服务：上传图片，返回结构化识别结果。
> 当前部署在周宇的 Mac 局域网上，供小范围试用收集反馈。

## 一、你能用到什么

| 能力 | 说明 |
|---|---|
| **商品图属性识别**（garment_attr） | 识别服饰类商品的类目、颜色、版型、袖长、领型、图案、适用场景 |
| **头像打标**（avatar_tag） | 识别头像类型（真人/卡通/风景/logo/文字）、人物属性（性别感/年龄感/正侧脸）、风格调性、技术质检、风险标记 |
| **管理后台** | 查看每次识别的原图与结果对照、标记识别对错、看统计数据 |

## 二、访问方式（试用期间）

服务地址（局域网内直接访问，无需翻墙/VPN）：

```
API 地址：  http://192.168.1.9:8902
管理后台：  http://192.168.1.9:8902/admin/login
交互式文档：http://192.168.1.9:8902/docs
```

⚠️ **前提**：试用期间周宇的 Mac 必须开机且和你在同一网络（同一 WiFi/办公网）。
Mac 合盖或服务停了就访问不了——遇到了直接跟周宇说一声。

**凭据**（试用专用，正式版会换）：

| 用途 | 值 |
|---|---|
| API 调用密钥（请求头 `X-API-Key`） | `testkey-e2e-1` |
| 管理后台登录口令 | `vision-admin-e2e` |

## 三、API 调用方法

### 方式 A：交互式文档页（最简单，不用写代码）

打开 http://192.168.1.9:8902/docs：

1. 右上角 **Authorize** → 填入 API key `testkey-e2e-1` → Authorize
2. 找到 `POST /v1/analyze` → **Try it out**
3. 请求体改成（task 二选一）：

```json
{
  "task": "garment_attr",
  "image_url": "https://任何公网可访问的图片地址.jpg"
}
```

4. **Execute** → 看下方 Response

### 方式 B：命令行 curl

```bash
# 图片在公网（直接给 URL）
curl -s -X POST http://192.168.1.9:8902/v1/analyze \
  -H "X-API-Key: *** \
  -H "Content-Type: application/json" \
  -d '{"task":"avatar_tag","image_url":"https://example.com/avatar.jpg"}' | python3 -m json.tool

# 图片在本地（转 base64）
B64=$(base64 -i /path/to/图片.jpg | tr -d '\n')
curl -s -X POST http://192.168.1.9:8902/v1/analyze \
  -H "X-API-Key: *** \
  -H "Content-Type: application/json" \
  -d "{\"task\":\"garment_attr\",\"image_base64\":\"$B64\"}" | python3 -m json.tool
```

### 返回示例（garment_attr）

```json
{
  "id": "e95c2309...",
  "task": "garment_attr",
  "category": "long cardigan",
  "color": ["charcoal gray", "dark gray"],
  "fit": "oversized",
  "sleeve_length": "long sleeves",
  "neckline": "open front",
  "pattern": "heathered",
  "occasions": ["casual", "loungewear", "autumn", "winter"],
  "usage": {"total_tokens": 1767},
  "latency_ms": 3356
}
```

失败时返回 `{"id": "...", "status": "provider_error", "error": "..."}`，
带上 id 找周宇查日志。

## 四、管理后台怎么用

登录 http://192.168.1.9:8902/admin/login（口令见上表）：

1. **记录流**：每次 API 调用自动落一条记录（缩略图 + 任务 + 状态）
2. **点缩略图看详情**：左边原图、右边识别结果 JSON
3. **复核**：识别对了点「正确」，错了点「错误」，拿不准点「存疑」，可写备注——
   **你的标记直接决定这个产品下一步调什么**，标得越认真，识别越准
4. **统计**：首页卡片看调用量、延迟、复核进度

## 五、限制与注意事项

| 项 | 说明 |
|---|---|
| 图片要求 | jpeg/png/webp/gif/bmp，≤20MB；图里文字也能识别（在结果字段里） |
| 速度 | 单张 3-10 秒（模型推理），别批量轰炸，一次试几张 |
| 隐私 | 试用期间所有上传的图片和结果都会**留存在周宇的机器上**（管理后台可见），别传敏感图片 |
| 稳定性 | 试用版没有 SLA，偶尔的失败重试一次即可 |
| 端口 | 8902，如果公司防火墙拦了局域网端口，换手机热点试试 |

## 六、反馈方式

发现问题/想要的功能，直接找周宇，附上**记录 id**（结果 JSON 里的 `id` 字段）+
一句话描述（比如"把女款风衣认成男款了"）。也可以直接在管理后台那条记录上标「错误」
+ 备注，我们看到会处理。

---
*追光 AI 视觉 · 内部试用 v0.1 · 2026-10-08*
