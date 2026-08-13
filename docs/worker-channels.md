# Worker Channels & Prefetch Configuration

This document describes how the message bus worker (`jararaca worker`) allocates AMQP channels
to consumers and how to tune prefetch per channel.

Introduced in `0.5.0` by the *parametrized shared channels* feature.

## Why channels matter

Every consumer in the worker runs on top of an AMQP channel. The channel is where:

- **QoS / prefetch** is applied (`basic.qos` is per-channel, not per-queue);
- message delivery is **serialized** by the broker;
- a protocol error (e.g. a double-ack) closes *everything* attached to it.

Before `0.5.0` the worker always opened **one channel per queue** and applied the same global
`prefetch_count` to all of them. With hundreds of handlers this meant hundreds of channels
against the broker, and no way to tune throughput per workload.

Now the channel a consumer attaches to is identified by a **`channel_id`**, which you can:

- leave implicit (one channel per queue — the previous behavior, still the default);
- collapse into a single shared channel for the whole worker;
- assign explicitly per handler, creating named channel *pools*;
- give a distinct prefetch value per `channel_id`.

## How `channel_id` is resolved

For each message handler and each scheduled action, the worker resolves the channel like this:

```mermaid
flowchart TD
    A[Handler / Scheduled Action] --> B{channel_id set on the decorator?}
    B -- yes --> C[use that channel_id]
    B -- no --> D{shared_default_channel enabled<br/>on the broker URL?}
    D -- yes --> E[use DEFAULT_CHANNEL_ID<br/>JARARACA_MESSAGEBUS_CHANNEL_ID or 'DEFAULT']
    D -- no --> F[use the queue name<br/>one dedicated channel per queue]
```

In code (`jararaca/messagebus/worker.py`):

```python
channel_id = handler.spec.channel_id or (
    DEFAULT_CHANNEL_ID if self.config.shared_default_channel else queue_name
)
```

Channels are created lazily and cached: the first consumer that asks for a `channel_id` opens
the channel and sets its QoS; every later consumer with the same `channel_id` reuses it.

### Implicit channel ids (queue names)

When nothing is configured, `channel_id == queue_name`, and the queue name is derived from:

| Kind | Queue / implicit channel id |
| --- | --- |
| Message handler | `{MESSAGE_TOPIC}.{module}.{qualname}` |
| Scheduled action | `{module}.{qualname}` |

e.g. a handler `UserController.on_created` in `app.users` for a message with
`MESSAGE_TOPIC = "user.created"` gets:

```
user.created.app.users.UserController.on_created
```

These are the exact strings you would use as keys in `prefetch_count_by_channel_id` when
running in the default (non-shared) mode.

## Broker URL parameters

Worker configuration is read from the **broker URL query string**, with an environment variable
fallback. The query string always wins; the env var is only consulted when the parameter is
absent from the URL.

| Query parameter | Env var | Type | Default | Description |
| --- | --- | --- | --- | --- |
| `exchange` | — | `str` | **required** | Exchange the worker binds its queues to. |
| `prefetch_count` | `AMQP_PREFETCH_COUNT` | `int` | **required** | Default prefetch applied to any channel without a specific value. |
| `shared_default_channel` | `AMQP_SHARED_DEFAULT_CHANNEL` | `bool` (`"true"`) | `false` | When true, every handler without an explicit `channel_id` shares one channel. |
| `prefetch_count_by_channel_id` | `AMQP_PREFETCH_COUNT_BY_CHANNEL` | `ch:n,ch2:n2` | `{}` | Per-channel prefetch overrides. |
| `connection_retry_max` | — | `int` | `5` | Connection retry attempts. |
| `connection_retry_delay` | — | `float` | `1.0` | Initial connection retry delay (s). |
| `connection_retry_max_delay` | — | `float` | `60.0` | Max connection retry delay (s). |
| `connection_retry_backoff` | — | `float` | `2.0` | Connection retry backoff factor. |
| `consumer_retry_max` | — | `int` | `5` | Consumer setup retry attempts. |
| `consumer_retry_delay` | — | `float` | `5.0` | Initial consumer retry delay (s). |
| `consumer_retry_max_delay` | — | `float` | `60.0` | Max consumer retry delay (s). |
| `consumer_retry_backoff` | — | `float` | `3.0` | Consumer retry backoff factor. |
| `connection_heartbeat_interval` | — | `float` | `30.0` | AMQP heartbeat interval (s). |
| `connection_health_check_interval` | — | `float` | `10.0` | Connection health check interval (s). |

!!! warning "Renamed in 0.5.0"
    `shared_channel` → `shared_default_channel` and `AMQP_SHARED_CHANNEL` → `AMQP_SHARED_DEFAULT_CHANNEL`.
    `prefetch_by_channel_id` → `prefetch_count_by_channel_id` and `AMQP_PREFETCH_BY_CHANNEL_ID` → `AMQP_PREFETCH_COUNT_BY_CHANNEL`.
    Update existing deployments — the old names are silently ignored.

### Environment variables affecting the decorators

| Env var | Effect |
| --- | --- |
| `JARARACA_MESSAGEBUS_CHANNEL_ID` | Renames the shared default channel id (default `"DEFAULT"`). Read at import time. |
| `JARARACA_MESSAGEBUS_HANDLER_GROUP` | Default `group` for `@MessageHandler`. |
| `JARARACA_SCHEDULER_ACTION_GROUP` | Default `group` for `@ScheduledAction`. |

## Decorator options

```python
from jararaca import MessageHandler, ScheduledAction


class BillingController:

    @MessageHandler(InvoiceIssued, channel_id="billing-heavy")
    async def on_invoice_issued(self, message: MessageOf[InvoiceIssued]) -> None:
        ...

    @ScheduledAction("*/5 * * * *", channel_id="cron")
    async def reconcile(self) -> None:
        ...
```

`channel_id` is `str | None`. `None` (the default) means *"decide from the worker configuration"*,
per the resolution flow above — so adding the parameter to a handler is always opt-in and never
changes the behavior of the handlers around it.

## Configuration scenarios

### 1. Dedicated channel per queue (default, unchanged behavior)

```bash
jararaca worker app.main:app \
  --broker-url "amqp://guest:guest@rabbit:5672/?exchange=jararaca&prefetch_count=10" \
  --backend-url "redis://redis:6379"
```

Each handler gets its own channel with `prefetch_count=10`. A slow handler never blocks the
delivery of another handler's messages. Cost: one channel per handler.

### 2. One shared channel for the whole worker

```bash
jararaca worker app.main:app \
  --broker-url "amqp://guest:guest@rabbit:5672/?exchange=jararaca&prefetch_count=20&shared_default_channel=true" \
  --backend-url "redis://redis:6379"
```

All handlers attach to the channel `DEFAULT`, with a **shared budget** of 20 unacked messages
across all of them. Best for workers with many low-traffic handlers where channel count is the
concern, not per-handler isolation.

Equivalent via environment:

```bash
export BROKER_URL="amqp://guest:guest@rabbit:5672/?exchange=jararaca"
export AMQP_PREFETCH_COUNT=20
export AMQP_SHARED_DEFAULT_CHANNEL=true
```

### 3. Named channel pools (parametrized channels)

Group handlers by workload profile and give each group its own prefetch:

```python
class MediaController:

    # CPU-bound transcoding: keep the in-flight window small
    @MessageHandler(VideoUploaded, channel_id="transcode")
    async def on_video_uploaded(self, message: MessageOf[VideoUploaded]) -> None:
        ...

    # IO-bound webhooks: a large window is fine
    @MessageHandler(WebhookRequested, channel_id="webhooks")
    async def on_webhook_requested(self, message: MessageOf[WebhookRequested]) -> None:
        ...
```

```bash
jararaca worker app.main:app \
  --broker-url "amqp://guest:guest@rabbit:5672/?exchange=jararaca\
&prefetch_count=10\
&prefetch_count_by_channel_id=transcode:2,webhooks:200" \
  --backend-url "redis://redis:6379"
```

Result:

| Channel | Handlers | Prefetch |
| --- | --- | --- |
| `transcode` | `on_video_uploaded` | 2 |
| `webhooks` | `on_webhook_requested` | 200 |
| *(queue name)* | every other handler | 10 (default) |

### 4. Shared default + isolated hot path

Collapse everything into one channel, except the handlers that deserve isolation:

```python
@MessageHandler(PaymentAuthorized, channel_id="payments")
async def on_payment_authorized(self, message: MessageOf[PaymentAuthorized]) -> None:
    ...
```

```bash
--broker-url "amqp://guest:guest@rabbit:5672/?exchange=jararaca\
&prefetch_count=5\
&shared_default_channel=true\
&prefetch_count_by_channel_id=DEFAULT:5,payments:50"
```

Everything low-traffic lives on `DEFAULT` with a shared window of 5; payments get their own
channel with a window of 50 and are unaffected by a stuck handler elsewhere.

### 5. Per-queue prefetch without explicit `channel_id`

In the default mode the channel id *is* the queue name, so you can tune a single handler without
touching the code:

```bash
--broker-url "amqp://guest:guest@rabbit:5672/?exchange=jararaca\
&prefetch_count=10\
&prefetch_count_by_channel_id=report.generated.app.reports.ReportController.on_generated:1"
```

Useful to throttle one heavy handler in production while the code stays untouched.

### 6. Renaming the shared default channel

`JARARACA_MESSAGEBUS_CHANNEL_ID` changes the id used by the shared default channel — handy when
you want the prefetch map to read semantically, or when several worker roles share one config
template:

```bash
export JARARACA_MESSAGEBUS_CHANNEL_ID=ingest
export BROKER_URL="amqp://guest:guest@rabbit:5672/?exchange=jararaca\
&prefetch_count=10\
&shared_default_channel=true\
&prefetch_count_by_channel_id=ingest:100"
```

!!! note
    This variable is read at **import time** of `jararaca.messagebus.decorators`, so it must be
    set in the process environment before the app module is loaded — not assigned at runtime.

### 7. Splitting workloads across worker processes

`channel_id` composes with `--groups` / `--handlers`. A common production layout is one
deployment per group, each with its own channel tuning:

```python
@MessageHandler(OrderPlaced, group="orders", channel_id="orders")
async def on_order_placed(self, message: MessageOf[OrderPlaced]) -> None:
    ...

@MessageHandler(EmailQueued, group="notifications", channel_id="notifications")
async def on_email_queued(self, message: MessageOf[EmailQueued]) -> None:
    ...
```

```bash
# deployment A
jararaca worker app.main:app --groups orders \
  --broker-url "amqp://.../?exchange=jararaca&prefetch_count=10&prefetch_count_by_channel_id=orders:25" ...

# deployment B
jararaca worker app.main:app --groups notifications \
  --broker-url "amqp://.../?exchange=jararaca&prefetch_count=10&prefetch_count_by_channel_id=notifications:500" ...
```

### 8. Scheduled actions on their own channel

Scheduled actions consume from the broker the same way handlers do, so they accept the same
parameter. Isolating them keeps cron dispatch responsive even when the message workload is
saturated:

```python
class MaintenanceController:

    @ScheduledAction("0 * * * *", channel_id="cron")
    async def hourly_cleanup(self) -> None:
        ...

    @ScheduledAction("*/1 * * * *", channel_id="cron")
    async def heartbeat(self) -> None:
        ...
```

```bash
--broker-url "amqp://.../?exchange=jararaca&prefetch_count=10&prefetch_count_by_channel_id=cron:1"
```

## Programmatic configuration

The URL parsing is a convenience around `AioPikaWorkerConfig`; you can build it directly:

```python
from jararaca.messagebus.worker import AioPikaMicroserviceConsumer, AioPikaWorkerConfig
from jararaca.utils.retry import RetryPolicy

config = AioPikaWorkerConfig(
    url="amqp://guest:guest@rabbit:5672/",
    exchange="jararaca",
    default_prefetch_count=10,
    shared_default_channel=True,
    prefetch_by_channel_id={"DEFAULT": 10, "payments": 50},
    connection_retry_config=RetryPolicy(max_retries=15, initial_delay=1.0),
    consumer_retry_policy=RetryPolicy(max_retries=5, initial_delay=5.0),
    connection_heartbeat_interval=30.0,
    connection_health_check_interval=10.0,
)
```

Note the field name is `prefetch_by_channel_id`, while the query parameter is
`prefetch_count_by_channel_id`.

## Choosing a layout

| Situation | Recommended layout |
| --- | --- |
| Few handlers, throughput matters | Default (channel per queue), tune `prefetch_count`. |
| Many handlers, mostly idle | `shared_default_channel=true` with a moderate prefetch. |
| Mixed CPU-bound and IO-bound handlers | Named channels per profile + `prefetch_count_by_channel_id`. |
| One handler must never be starved | Explicit `channel_id` for it, shared default for the rest. |
| Cron dispatch must stay responsive | Dedicated `channel_id` for `@ScheduledAction`s. |

## Gotchas

- **Prefetch is per channel, not per handler.** Handlers sharing a `channel_id` share the same
  in-flight budget; a slow handler can consume the entire window.
- **A channel is a failure domain.** An AMQP protocol error closes the channel and every consumer
  attached to it. Grouping handlers trades isolation for fewer channels.
- **Unknown keys in `prefetch_count_by_channel_id` are ignored silently.** A typo in a channel or
  queue name degrades to `prefetch_count` with no warning.
- **The `INFO` log shows the declared, not the resolved, channel.** `Consuming message handler
  <queue> on channel <channel_id>` prints the value from the decorator, so it reads `None` for
  handlers relying on the resolution flow. To see what was actually opened, enable `DEBUG` on
  `jararaca.messagebus.worker` and look for `Creating channel for queue <channel_id>` /
  `Reusing existing channel for queue <channel_id>`.
- **Malformed maps raise.** A value that is not `name:int` pairs separated by commas raises
  `ValueError` at startup.
- **`prefetch_count` is mandatory.** The worker asserts it is present either in the URL or in
  `AMQP_PREFETCH_COUNT`.
- **Query string beats env var.** Once a parameter is present in the URL, its env var is not
  consulted at all.

## Related

- [Message Bus](messagebus.md) — handler definition, ack/nack semantics
- [Scheduler](scheduler.md) — scheduled actions and the beat process
- [Retry](retry.md) — retry policies and delayed messages
