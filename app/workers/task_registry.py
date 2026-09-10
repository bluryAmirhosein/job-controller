import asyncio
from collections.abc import Awaitable, Callable

LogFn = Callable[[str], Awaitable[None]]
TaskHandler = Callable[[dict, LogFn], Awaitable[dict]]

_TASK_REGISTRY: dict[str, TaskHandler] = {}


def task(name: str):
    def decorator(func: TaskHandler) -> TaskHandler:
        _TASK_REGISTRY[name] = func
        return func

    return decorator


def get_task_handler(task_type: str) -> TaskHandler:
    handler = _TASK_REGISTRY.get(task_type)
    if handler is None:
        raise ValueError(f"No handler registered for task_type '{task_type}'")
    return handler


@task("demo_sleep")
async def demo_sleep_task(payload: dict, log: LogFn) -> dict:
    seconds = int(payload.get("seconds", 5))
    await log(f"Sleeping for {seconds} seconds")
    for i in range(seconds):
        await asyncio.sleep(1)
        await log(f"Progress: {i + 1}/{seconds}")
    return {"message": f"Slept for {seconds} seconds"}