"""Independent durable send/collect schedules, including real timezone transitions."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from croniter import croniter
from packet_foundry_contracts import digest_json
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .journal import Journal

log = logging.getLogger(__name__)


class Schedule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    interval_seconds: float | None = Field(default=None, gt=0)
    cron: str | None = None
    timezone: str = "UTC"

    @model_validator(mode="after")
    def valid(self) -> Schedule:
        try:
            ZoneInfo(self.timezone)
        except (KeyError, ValueError) as exc:
            raise ValueError("timezone must be a valid IANA timezone name") from exc
        if (self.interval_seconds is None) == (self.cron is None):
            raise ValueError("choose exactly one of interval_seconds or cron")
        if self.cron and (len(self.cron.split()) != 5 or not croniter.is_valid(self.cron)):
            raise ValueError("cron must be a valid five-field expression")
        if self.cron:
            # Reject syntactically valid expressions that can never occur (Feb 31).
            croniter(self.cron, datetime(2000, 1, 1), max_years_between_matches=8).get_next(datetime)
        return self

    def next_after(self, instant: datetime) -> datetime:
        if instant.tzinfo is None:
            raise ValueError("schedule timestamps must include a timezone")
        instant = instant.astimezone(UTC)
        if self.interval_seconds is not None:
            return instant + timedelta(seconds=self.interval_seconds)
        zone = ZoneInfo(self.timezone)
        wall = instant.astimezone(zone).replace(tzinfo=None)
        # Generate local wall times, test both folds by round-tripping through UTC.
        # A one-day look-back includes even timezone date-line changes, while the
        # look-ahead ensures a later wall time's first fold cannot be skipped.
        iterator = croniter(self.cron, wall - timedelta(days=1), max_years_between_matches=8)
        best: datetime | None = None
        while True:
            candidate = iterator.get_next(datetime)
            for fold in (0, 1):
                utc = candidate.replace(tzinfo=zone, fold=fold).astimezone(UTC)
                actual = utc.astimezone(zone)
                if actual.replace(tzinfo=None) == candidate and utc > instant:
                    best = min(best, utc) if best is not None else utc
            if candidate > wall + timedelta(days=1) and best is not None:
                return best


class Scheduler:
    """An overdue task runs once, then advances from completion time.

    Progress advances only after success. A crash replays the action, which is safe
    because submission, consumption, and acknowledgement are durable/idempotent.
    """

    def __init__(
        self,
        journal: Journal,
        tasks: dict[str, tuple[Schedule, Callable[[], Awaitable[object]]]],
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.journal = journal
        self.tasks = tasks
        self.clock = clock

    async def _run_one(
        self, name: str, schedule: Schedule, action: Callable[[], Awaitable[object]]
    ) -> bool | None:
        now = self.clock()
        definition = digest_json(schedule.model_dump())
        due = self.journal.due(name, definition, now)
        if due > now:
            return None
        try:
            await action()
            self.journal.schedule_completed(name, schedule.next_after(self.clock()))
        except Exception as exc:
            # Never log HTTP request objects, headers, response bodies, or
            # arbitrary application exception strings that may hold secrets.
            log.warning("collector task %s failed (%s)", name, type(exc).__name__)
            return False
        return True

    async def run_pending(self) -> dict[str, bool]:
        names = list(self.tasks)
        results = await asyncio.gather(*(
            self._run_one(name, *self.tasks[name]) for name in names
        ))
        return {name: result for name, result in zip(names, results, strict=True)
                if result is not None}

    async def run(self, *, tick_seconds: float = 1) -> None:
        if tick_seconds <= 0:
            raise ValueError("tick_seconds must be positive")

        async def loop(
            name: str, schedule: Schedule, action: Callable[[], Awaitable[object]]
        ) -> None:
            while True:
                await self._run_one(name, schedule, action)
                await asyncio.sleep(tick_seconds)

        # One loop per schedule: a long send cannot block timely collection.
        async with asyncio.TaskGroup() as group:
            for name, (schedule, action) in self.tasks.items():
                group.create_task(loop(name, schedule, action))
