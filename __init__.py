import logging
import asyncio
import voluptuous as vol

from homeassistant.core import HomeAssistant, Event
from homeassistant.const import (
    EVENT_STATE_CHANGED,
    EVENT_HOMEASSISTANT_STOP,
    STATE_UNKNOWN,
    STATE_UNAVAILABLE,
    CONF_EXCLUDE,
    CONF_INCLUDE,
    CONF_DOMAINS,
    CONF_ENTITIES
)
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers import entityfilter
from homeassistant.helpers.typing import ConfigType

from .const import (
    DOMAIN,
    CONF_HOST,
    CONF_PORT,
    CONF_ENTITY_GLOBS,
    DEFAULT_HOST,
    DEFAULT_PORT
)

_LOGGER = logging.getLogger(__name__)

CONNECT_TIMEOUT = 10  # seconds

# --- CONFIG SCHEMA ---
FILTER_SCHEMA = vol.Schema({
    vol.Optional(CONF_EXCLUDE, default={}): vol.Schema({
        vol.Optional(CONF_DOMAINS, default=[]): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(CONF_ENTITY_GLOBS, default=[]): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(CONF_ENTITIES, default=[]): cv.entity_ids,
    }),
    vol.Optional(CONF_INCLUDE, default={}): vol.Schema({
        vol.Optional(CONF_DOMAINS, default=[]): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(CONF_ENTITY_GLOBS, default=[]): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(CONF_ENTITIES, default=[]): cv.entity_ids,
    }),
})

CONFIG_SCHEMA = vol.Schema({
    DOMAIN: FILTER_SCHEMA.extend({
        vol.Optional(CONF_HOST, default=DEFAULT_HOST): cv.string,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
    })
}, extra=vol.ALLOW_EXTRA)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    conf = config.get(DOMAIN)
    if conf is None:
        return True

    host = conf.get(CONF_HOST, DEFAULT_HOST)
    port = conf.get(CONF_PORT, DEFAULT_PORT)

    should_track = entityfilter.convert_include_exclude_filter(conf)

    sender = QuestDBSender(hass, host, port)
    await sender.start()

    async def handle_state_change(event: Event):
        entity_id = event.data.get("entity_id")

        if not should_track(entity_id):
            return

        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return

        try:
            value = float(new_state.state)
            val_type = "num"
        except ValueError:
            state_lower = new_state.state.lower()
            if state_lower == "on":
                value = 1.0
                val_type = "num"
            elif state_lower == "off":
                value = 0.0
                val_type = "num"
            else:
                value = new_state.state
                val_type = "str"

        friendly_name = new_state.attributes.get("friendly_name", entity_id)
        unit = new_state.attributes.get("unit_of_measurement")
        ts_ns = int(new_state.last_changed.timestamp() * 1e9)

        sender.enqueue(entity_id, value, val_type, friendly_name, unit, ts_ns)

    unsub = hass.bus.async_listen(EVENT_STATE_CHANGED, handle_state_change)

    async def handle_stop(event: Event):
        unsub()
        await sender.stop()

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, handle_stop)

    return True


class QuestDBSender:
    def __init__(self, hass, host, port):
        self.hass = hass
        self.host = host
        self.port = port
        self.queue = asyncio.Queue()
        self.writer = None
        self._running = False
        self._task = None

    async def start(self):
        self._running = True
        self._task = self.hass.async_create_task(self._worker())

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self.writer:
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except Exception:
                pass
            self.writer = None

    def enqueue(self, entity_id, value, val_type, friendly_name, unit, ts_ns):
        self.queue.put_nowait((entity_id, value, val_type, friendly_name, unit, ts_ns))

    async def _worker(self):
        batch = []
        while self._running:
            try:
                try:
                    item = await asyncio.wait_for(self.queue.get(), timeout=1.0)
                    batch.append(item)
                except asyncio.TimeoutError:
                    pass

                while not self.queue.empty() and len(batch) < 100:
                    batch.append(self.queue.get_nowait())

                if batch:
                    if await self._send_batch(batch):
                        batch.clear()
                    else:
                        await asyncio.sleep(5)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                _LOGGER.error("Worker error: %s", e)
                await asyncio.sleep(10)

    async def _connect(self):
        if self.writer:
            return True
        try:
            _, self.writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=CONNECT_TIMEOUT,
            )
            _LOGGER.info("Connected to QuestDB at %s:%s", self.host, self.port)
            return True
        except Exception as e:
            _LOGGER.warning("Failed to connect to QuestDB at %s:%s: %s", self.host, self.port, e)
            return False

    async def _send_batch(self, batch):
        if not await self._connect():
            return False

        lines = []
        for entity_id, value, val_type, friendly_name, unit, ts_ns in batch:
            clean_entity = self._escape(entity_id)
            clean_friendly = self._escape(friendly_name)

            unit_tag = ""
            if unit:
                unit_tag = f",unit={self._escape(unit)}"

            if val_type == "num":
                value_str = f"{value:.10g}"
                if "e" not in value_str and "." not in value_str:
                    value_str += ".0"
                field = f"value={value_str}"
            else:
                safe_val = str(value).replace('"', '\\"')
                field = f'val_str="{safe_val}"'

            lines.append(
                f"ha_state,entity_id={clean_entity},friendly_name={clean_friendly}{unit_tag} {field} {ts_ns}"
            )

        payload = "\n".join(lines) + "\n"

        try:
            self.writer.write(payload.encode("utf-8"))
            await self.writer.drain()
            return True
        except Exception as e:
            _LOGGER.warning("Failed to send batch to QuestDB: %s", e)
            self.writer = None
            return False

    def _escape(self, tag_val):
        return (
            str(tag_val)
            .replace("\\", "\\\\")
            .replace(" ", "\\ ")
            .replace(",", "\\,")
            .replace("=", "\\=")
            .replace("\n", "\\n")
            .replace("\r", "\\r")
        )
