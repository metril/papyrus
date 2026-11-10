# Outgoing webhooks

Admins register webhooks (name, URL, optional secret, event list) via the Settings UI or the
`webhooks` API. Delivery is fire-and-forget on a background task: one attempt, 10 s timeout, failures
are logged and never retried. Payload schemas are in `/openapi.json` under `webhooks` (one entry per
event).

## Request

`POST <url>` with `Content-Type: application/json` and:

- `X-Papyrus-Event`: the event name
- `X-Papyrus-Signature`: only if the webhook has a secret; lowercase hex HMAC-SHA256 of the **raw
  request body** keyed with the secret (no prefix)

Body envelope:

```json
{"event": "print.release", "timestamp": "2026-11-10T12:00:00+00:00", "data": {"id": 42, "title": "a.pdf", "copies": 1}}
```

## Events

| Event | `data` |
|---|---|
| `print.release` | `id`, `title`, `copies` |
| `print.delete` | `id`, `title` |
| `print.upload` | `id`, `title`, `user_id`, `source_type` (every upload, share-target and inbound-email job) |
| `print.held` | `id`, `title`, `user_id` or `username` (network ingest), `source_type` (job entered the hold queue) |
| `print.test_page` | `printer_id`, `display_name`, `job_id` |
| `scan.complete` | `scan_id`, `format`, `pages` (batch/duplex only) |
| `scan.delete` | `scan_id` |
| `settings.update` | `keys` |
| `printer.supply_low` | `printer_id`, `display_name`, `cups_name`, `resolved`, `message`, `markers[{name, level}]` |
| `printer.error` | alert fields plus `reason` (`state_reasons` or `offline`), `state_reasons` |

Alert events fire on onset and again with `resolved: true` on recovery. Model names:
`PrintReleaseWebhook`, `PrinterSupplyLowWebhook`, etc.

## Verifying the signature

Compute the HMAC over the bytes you received, before any JSON parsing, and compare in constant time.

```python
import hashlib, hmac

def verify(raw_body: bytes, header: str, secret: str) -> bool:
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)
```

```sh
# body.json = raw request body, $SIG = X-Papyrus-Signature header value
expected=$(openssl dgst -sha256 -hmac "$SECRET" -hex < body.json | awk '{print $NF}')
[ "$expected" = "$SIG" ] && echo valid || echo invalid
```
