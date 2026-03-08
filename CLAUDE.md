# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This is a Home Assistant custom integration (`questdb_logger`) that logs entity state changes to a QuestDB time-series database over TCP using the QuestDB line protocol.

## Development Setup

This is a Home Assistant custom integration — no `setup.py` or `pyproject.toml`. To develop and test:

1. Place the `questdb_logger/` folder inside your Home Assistant `custom_components/` directory.
2. Add configuration to `configuration.yaml` (see Architecture section below).
3. Restart Home Assistant to load the integration.

There are no automated tests or CI/CD pipelines in this repository.

## Architecture

### Data Flow

```
HA STATE_CHANGED event
  → handle_state_change() (filters, type-converts)
  → QuestDBSender.enqueue() (asyncio.Queue)
  → _worker() (batches up to 100 items or 1s timeout)
  → _send_batch() (TCP → QuestDB line protocol)
```

### Key Components

- **`__init__.py`**: All integration logic
  - `async_setup()`: HA entry point — parses config, wires event listener and shutdown handler
  - `QuestDBSender`: Manages async TCP connection and batched writes to QuestDB
    - `start()` / `stop()`: Lifecycle — creates/cancels the worker task and closes the TCP writer
    - `_worker()`: Pulls from queue, batches up to 100 items or 1 s timeout, retries on failure (5 s backoff)
    - `_connect()`: Opens TCP connection with `CONNECT_TIMEOUT = 10 s`; logs success/failure
    - `_escape()`: Escapes ` `, `,`, `=`, `\`, `\n`, `\r` for ILP tag values
  - `handle_stop()`: Unsubscribes the state listener and calls `sender.stop()` on `EVENT_HOMEASSISTANT_STOP`
- **`const.py`**: Custom constants only — `DOMAIN`, `CONF_HOST`, `CONF_PORT`, `DEFAULT_HOST`, `DEFAULT_PORT`, `CONF_ENTITY_GLOBS`. Standard HA constants (`CONF_INCLUDE`, `CONF_EXCLUDE`, etc.) are imported directly from `homeassistant.const`.
- **`manifest.json`**: HA metadata (`iot_class: local_push`, version, domain)

### QuestDB Line Protocol

All state changes are written to the `ha_state` measurement table with tags `entity_id`, `friendly_name`, `unit`:
- Numeric states → `value=<float>` field (formatted with `:.10g`, never scientific notation for typical ranges)
- Boolean states (`on`/`off`) → `value=1.0`/`value=0.0`
- String states → `val_str="<string>"` field
- Timestamp: `new_state.last_changed` converted to nanoseconds and appended to each ILP line

### Configuration Schema (`configuration.yaml`)

```yaml
questdb_logger:
  host: 127.0.0.1   # DEFAULT_HOST
  port: 9009         # DEFAULT_PORT
  exclude:
    domains: [...]
    entities: [...]
    entity_globs: [...]
  include:
    domains: [...]
    entities: [...]
    entity_globs: [...]
```

The include/exclude filter logic mirrors Home Assistant's `recorder` integration filter pattern.
