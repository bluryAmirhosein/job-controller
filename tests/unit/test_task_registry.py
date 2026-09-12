import pytest

from app.workers.task_registry import (
    TaskValidationError,
    demo_sleep_task,
    get_task_handler,
    task,
)


class TestTaskRegistry:
    def test_get_task_handler_returns_the_registered_handler(self):
        handler = get_task_handler("demo_sleep")

        assert handler is demo_sleep_task

    def test_get_task_handler_raises_value_error_for_an_unknown_task_type(self):
        with pytest.raises(ValueError, match="No handler registered"):
            get_task_handler("does_not_exist")

    def test_task_decorator_registers_the_handler_under_the_given_name(self):
        @task("temp_test_task_registration")
        async def _handler(payload: dict, log) -> dict:
            return {"ok": True}

        assert get_task_handler("temp_test_task_registration") is _handler


class TestDemoSleepTask:
    async def test_raises_validation_error_when_seconds_is_missing(self):
        async def noop_log(message: str) -> None:
            pass

        with pytest.raises(TaskValidationError, match="seconds"):
            await demo_sleep_task({}, noop_log)

    async def test_returns_a_result_with_zero_seconds_and_logs_nothing(self):
        logged_messages: list[str] = []

        async def collecting_log(message: str) -> None:
            logged_messages.append(message)

        result = await demo_sleep_task({"seconds": 0}, collecting_log)

        assert result == {"message": "Slept for 0 seconds"}
        assert logged_messages == ["Sleeping for 0 seconds"]

    async def test_logs_progress_for_each_second(self):
        logged_messages: list[str] = []

        async def collecting_log(message: str) -> None:
            logged_messages.append(message)

        result = await demo_sleep_task({"seconds": 1}, collecting_log)

        assert result == {"message": "Slept for 1 seconds"}
        assert any("Progress: 1/1" in message for message in logged_messages)