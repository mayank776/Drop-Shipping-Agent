"""Keeps the daily-brief Temporal Schedule in line with settings (created or updated on worker start)."""

from __future__ import annotations

from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
    ScheduleUpdate,
)

from .brief import SCHEDULE_ID, BriefInput, DailyBriefWorkflow

TIME_ZONE = "Asia/Kolkata"


def parse_brief_time(value: str) -> tuple[int, int]:
    try:
        hour, minute = (int(p) for p in value.split(":"))
    except ValueError:
        raise ValueError(f"DAILY_BRIEF_TIME must be HH:MM, got {value!r}") from None
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError(f"DAILY_BRIEF_TIME out of range: {value!r}")
    return hour, minute


def brief_schedule(task_queue: str, brief_time: str) -> Schedule:
    hour, minute = parse_brief_time(brief_time)
    return Schedule(
        action=ScheduleActionStartWorkflow(
            DailyBriefWorkflow.run, BriefInput(), id=SCHEDULE_ID, task_queue=task_queue
        ),
        spec=ScheduleSpec(cron_expressions=[f"{minute} {hour} * * *"], time_zone_name=TIME_ZONE),
        policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
    )


async def ensure_brief_schedule(client: Client, task_queue: str, brief_time: str) -> None:
    schedule = brief_schedule(task_queue, brief_time)
    try:
        await client.create_schedule(SCHEDULE_ID, schedule)
    except ScheduleAlreadyRunningError:
        await client.get_schedule_handle(SCHEDULE_ID).update(lambda _: ScheduleUpdate(schedule=schedule))
