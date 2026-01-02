from collections import deque
from threading import Condition, Thread
from typing import Deque, Generic, Iterable, Iterator, Optional, TypeVar, Callable

T = TypeVar("T")

class AppendableIterable(Generic[T]):
    """
    - 내부 큐에 들어온 아이템을 순서대로 내보냅니다.
    - .append(), .extend()로 언제든 아이템을 추가할 수 있습니다.
    - .close()를 호출하면 반복이 종료됩니다.
    - 선택적으로 base_iter를 백그라운드에서 계속 비워 넣게 할 수 있습니다.
    """
    def __init__(
        self,
        base_iter: Optional[Iterable[T]] = None,
        feed_in_background: bool = False,
        on_error: Optional[Callable[[Exception], None]] = None,
        auto_close_when_empty=False
    ) -> None:
        self._q: Deque[T] = deque()
        self._cv = Condition()
        self._closed = False
        self._feeding_thread: Optional[Thread] = None
        self._on_error = on_error
        self._auto_close_when_empty = auto_close_when_empty

        if base_iter is not None:
            if feed_in_background:
                # 백그라운드로 base_iter를 계속 흘려 넣기
                self._feeding_thread = Thread(
                    target=self._feed_base_iter, args=(base_iter,), daemon=True
                )
                self._feeding_thread.start()
            else:
                # 시작 전에 전부 넣기
                self.extend(base_iter)

    def _feed_base_iter(self, base_iter: Iterable[T]) -> None:
        try:
            for item in base_iter:
                with self._cv:
                    if self._closed:
                        return
                    self._q.append(item)
                    self._cv.notify()
        except Exception as e:
            if self._on_error:
                self._on_error(e)
            else:
                # 기본 처리
                import sys, traceback
                traceback.print_exc(file=sys.stderr)
        finally:
            # base_iter 공급 종료. 닫지는 않습니다. 외부에서 계속 append할 수 있음
            with self._cv:
                self._cv.notify_all()

    def append(self, item: T) -> None:
        with self._cv:
            if self._closed:
                raise RuntimeError("Already closed")
            self._q.append(item)
            self._cv.notify()

    def extend(self, items: Iterable[T]) -> None:
        with self._cv:
            if self._closed:
                raise RuntimeError("Already closed")
            any_added = False
            for it in items:
                self._q.append(it)
                any_added = True
            if any_added:
                self._cv.notify_all()

    def replace_last(self, item: T) -> None:
        with self._cv:
            if not self._q:
                raise IndexError("Queue is empty, nothing to replace")
            self._q[-1] = item
            self._cv.notify()

    def close(self) -> None:
        """더 이상 아이템을 넣지 않을 때 호출. 반복자는 큐를 모두 비우면 종료합니다."""
        with self._cv:
            self._closed = True
            self._cv.notify_all()

    def finish(self) -> None:
        self.close()

    def __iter__(self) -> Iterator[T]:
        while True:
            with self._cv:
                while not self._q and not self._closed:
                    if self._auto_close_when_empty:
                        # 큐가 비었고 더 기다리지 않고 종료
                        return
                    self._cv.wait()
                if self._q:
                    item = self._q.popleft()
                else:
                    return
            yield item

    @property
    def q(self):
        return self._q
