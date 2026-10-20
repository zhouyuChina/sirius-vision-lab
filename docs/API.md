# Image Analysis API

All `/v1` requests require `X-API-Key`. Missing/invalid keys return 401; insufficient privileges return 403. Errors use `{"error":"..."}` with no upstream details. JSON validation errors return 422, invalid image input/downloads return 400, requests above the body limit return 413, and inference/structured-output failures return 502 with a record `id` and `status`. Unknown resources return 404.

## Analyze

`POST /v1/analyze`

```json
{"task":"avatar_tag","image_url":"https://images.example.com/avatar.png","options":{}}
```

Supply exactly one of `image_url` or `image_base64`. Base64 may be plain or a `data:image/...;base64,...` URL. Image bytes must be at most 20 MiB and have a JPEG, PNG, WebP, GIF or BMP signature. Downloads allow public HTTP(S) on ports 80/443 only, reject redirects/private DNS results, pin the validated address and have a 20-second deadline. Signature validation does not decode or transcode images.

Tasks are `avatar_tag`, `garment_attr`, `brand_risk`, `brand_risk_style`, and `image_translate`. `options` is an optional JSON object (16 KiB maximum) passed as contextual data to the task prompt; it cannot select endpoints or credentials. Responses contain `id`, `task`, `model`, `template_version`, task fields at the top level, `usage` and `latency_ms`. Obtain exact required fields/enums from `GET /v1/tasks` (a JSON array of task/version/schema entries).

Accepted analyses persist the input image, options, raw final output, parsed fields, usage and success/failure. Input/authentication failures have no analysis record. A schema failure triggers one corrective attempt; if both fail, raw final output remains available to administrators. Usage totals include both completed attempts. Latency measures the complete analysis including fetch and retries.

## Records and stats

`GET /v1/records` requires an **admin-role API key**. Filters: `task`, `status` (`success`, `parse_error`, `provider_error`, `error`), `date_from`, `date_to` (ISO 8601; naive timestamps treated as UTC), `limit` (1–100, default 50), and opaque `cursor`. Response: `{"items":[...],"next_cursor":null}`. Records are newest first, with a stable ID tie-breaker. Pass `next_cursor` to retrieve the next page. Invalid cursors return 400. Records include raw outputs and internal routing metadata; grant access only to administrators.

`GET /v1/stats` accepts any valid key. Returns the last seven days' `calls`, `latency_p50_ms`, `latency_p95_ms` (nearest-rank), `tokens`, `per_task`, `reviewed` and `period_days`. Counts include accepted analyses that failed. Statistics are service-wide; this milestone has no tenant billing or tenant isolation.

## Review console

Open `/admin` over HTTPS. Sessions expire after eight hours and use Secure, HttpOnly, SameSite=strict cookies. Logout revokes the session server-side. Same-origin login and session-bound CSRF headers protect writes.

- `POST /admin/login`: `{password}` → `{csrf_token}` plus session cookie. Requires same-origin `Origin`.
- `GET /admin/session`: returns the current CSRF token.
- `GET /admin/records`, `/admin/records/{id}`, `/admin/stats`: session-only data.
- `GET /admin/records/{id}/image`: session-only input image.
- `GET /admin/records/{id}/output_image`: session-only rendered image when available; otherwise 404.
- `PATCH /admin/records/{id}/review`: `{review: "correct"|"wrong"|"uncertain", review_note?: string}`. Note maximum: 4,000 characters.
- `POST /admin/logout`: revoke session and clear cookie.

Review and logout require both same-origin `Origin` and `X-CSRF-Token`. Missing/expired sessions redirect to `/admin/login`; the console returns to login. Review writes replace the record's current review/note and are idempotent. Missing record IDs return 404.


## Image translation

Use `image_translate` to extract text from a product image, translate it to Russian or Chinese, and optionally render the translation onto the image. The three stages complete in one `POST /v1/analyze` call.

```json
{"task":"image_translate","image_url":"https://images.example.com/product.jpg","options":{"target_lang":"ru","render":"auto"}}
```

`target_lang` is `ru` (default) or `zh`. `render` is `auto` (default), `pil`, `edit`, or `none`. `none` returns text only. `pil` repaints text within solid-color bands and lists text over complex backgrounds in `skipped_items`. `edit` uses an image-edit provider for the whole image, including complex backgrounds. `auto` chooses band repainting when every item has a solid band, and whole-image editing otherwise. If whole-image editing fails, the service tries its backup engine and then repaints available bands. If all rendering fails, the analysis remains successful and `render.error` explains that the image could not be produced.

```json
{
  "id":"<record-id>", "task":"image_translate", "model":"<vision-model>",
  "template_version":"0.1.0",
  "segments":[{"id":1,"source_text":"促销","translated_text":"Скидка","position":"top label","background_type":"solid_band"}],
  "render":{"mode":"auto","engine":"pil","output_image_id":"<image-id>","rendered_items":[1],"skipped_items":[],"fallback_from":null,"error":null},
  "usage":{"total_tokens":100}, "latency_ms":1200
}
```

`render.engine` identifies the rendering method (`pil`, a configured edit engine, or `null`). `output_image_id` is `null` when no output image exists. The translated image is available to authenticated console sessions at `GET /admin/records/{id}/output_image`. Image edit service credentials and endpoints are configured by the operator (for example `<your-image-edit-provider>`); clients never supply them in `options`.

Extraction or translation failures return 502 and persist a failed record. An unparseable translation after one retry uses `status: "error"` and `error_code: "translate_parse_failed"`. A rendering failure preserves the translated segments and a successful record. Latency includes all three stages and token usage includes completed extraction and translation calls.
