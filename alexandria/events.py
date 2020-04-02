from typing import AsyncIterable, Awaitable, Callable, Optional, Set, Type
from types import TracebackType

import trio

from alexandria.abc import (
    SessionAPI,
    EventAPI,
    EventsAPI,
    EventSubscriptionAPI,
    TAwaitable,
    TEventPayload,
)


class ReAwaitable(Awaitable[TAwaitable]):
    _result: TAwaitable

    def __init__(self, awaitable: Awaitable[TAwaitable]) -> None:
        self._awaitable = awaitable

    @property
    def is_done(self) -> bool:
        return hasattr(self, '_result')

    def __await__(self) -> TAwaitable:
        if not self.is_done:
            self._result = self._awaitable.__await__()
        return self._result


class EventSubscription(EventSubscriptionAPI[TAwaitable]):
    def __init__(self,
                 lock: trio.Lock,
                 remove_fn: Callable[[], None],
                 receive_channel: trio.abc.ReceiveChannel[TAwaitable],
                 ) -> None:
        self._lock = lock
        self._remove_fn = remove_fn
        self._receive_channel = receive_channel

    def __await__(self) -> TAwaitable:
        return self.receive().__await__()

    async def __aiter__(self) -> AsyncIterable[TAwaitable]:
        async for payload in self.stream():
            yield payload

    async def receive(self) -> TAwaitable:
        return await self._receive_channel.receive()

    async def stream(self) -> AsyncIterable[TAwaitable]:
        async with self._receive_channel:
            async for payload in self._receive_channel:
                yield payload

    async def __aenter__(self) -> Awaitable[TAwaitable]:
        await trio.hazmat.checkpoint()
        return self

    async def __aexit__(self,
                        exc_type: Optional[Type[BaseException]],
                        exc_value: Optional[BaseException],
                        traceback: Optional[TracebackType],
                        ) -> None:
        async with self._lock:
            self._remove_fn()


class Event(EventAPI[TEventPayload]):
    _channels: Set[trio.abc.SendChannel[TEventPayload]]

    def __init__(self) -> None:
        self._lock = trio.Lock()
        self._channels = set()

    async def trigger(self, payload: TEventPayload) -> None:
        async with self._lock:
            for send_channel in self._channels:
                await send_channel.send(payload)

    def wait(self) -> EventSubscriptionAPI[TEventPayload]:
        send_channel, receive_channel = trio.open_memory_channel[SessionAPI](0)

        self._channels.add(send_channel)

        return EventSubscription(
            self._lock,
            lambda: self._channels.remove(send_channel),
            receive_channel,
        )


class Events(EventsAPI):
    def __init__(self) -> None:
        self.new_session: Event[SessionAPI] = Event()
        self.handshake_complete: Event[SessionAPI] = Event()
        self.listening: Event[Endpoint] = Event()
