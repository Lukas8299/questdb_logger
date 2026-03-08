# QuestDB Logger

A Home Assistant custom integration that logs entity state changes to a [QuestDB](https://questdb.io) time-series database over TCP using the [InfluxDB line protocol](https://questdb.io/docs/reference/api/ilp/overview/).

## Features

- Streams state changes to QuestDB in real time with accurate timestamps
- Batches writes for efficiency (up to 100 rows per batch)
- Filters entities by domain, entity ID, or glob pattern
- Handles numeric, boolean (`on`/`off`), and string states
- Automatically reconnects on connection loss
- Clean shutdown — flushes and closes the TCP connection when HA stops

## Installation

1. Copy the `questdb_logger/` folder into your Home Assistant `custom_components/` directory:
   ```
   config/
   └── custom_components/
       └── questdb_logger/
           ├── __init__.py
           ├── const.py
           └── manifest.json
   ```
2. Restart Home Assistant.

## Configuration

Add the following to your `configuration.yaml`:

```yaml
questdb_logger:
  host: 127.0.0.1  # QuestDB host (default: 127.0.0.1)
  port: 9009        # QuestDB ILP TCP port (default: 9009)
```

### Filtering Entities

Use `include` / `exclude` to control which entities are logged. The filter logic mirrors Home Assistant's built-in `recorder` integration.

```yaml
questdb_logger:
  host: 192.168.1.100
  port: 9009
  include:
    domains:
      - sensor
      - binary_sensor
    entities:
      - light.living_room
  exclude:
    entity_globs:
      - sensor.*_rssi
      - sensor.*_lqi
```

## Data Schema

All state changes are written to the `ha_state` table with the following structure:

| Column | Type | Description |
|---|---|---|
| `entity_id` | tag | Home Assistant entity ID |
| `friendly_name` | tag | Human-readable name |
| `unit` | tag | Unit of measurement (if present) |
| `value` | double | Numeric or boolean state (`on`=1.0, `off`=0.0) |
| `val_str` | string | Non-numeric state value |
| timestamp | designated | `last_changed` time of the state, nanosecond precision |

Example QuestDB query:

```sql
SELECT timestamp, entity_id, value
FROM ha_state
WHERE entity_id = 'sensor.living_room_temperature'
  AND timestamp > dateadd('d', -1, now())
ORDER BY timestamp;
```

## Requirements

- Home Assistant 2022.x or newer
- QuestDB with ILP TCP ingestion enabled (port 9009 by default)
