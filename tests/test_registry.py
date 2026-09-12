"""Test registry module."""

from __future__ import annotations

from collections.abc import Callable
import threading
from typing import Any

from asusrouter.registry import ARCallableRegistry as ARCallReg


def test_register_and_get_callable_by_instance_and_class() -> None:
    """Test registering and retrieving callables by instance and class."""

    class Base:
        pass

    class Child(Base):
        pass

    def base_get(s: Any) -> str:
        return "base"

    # register using kwargs API
    ARCallReg.register(Base, get_state=base_get)

    # lookup by instance
    fn = ARCallReg.get_callable(Child(), "get_state")
    assert fn is base_get
    assert fn is not None
    assert fn(Child()) == "base"

    # lookup by class
    fn2 = ARCallReg.get_callable(Child, "get_state")
    assert fn2 is base_get


def test_register_with_callable_flag_tuple() -> None:
    """Test registering callable entries with optional metadata."""

    class A:
        pass

    def get_state(s: Any) -> str:
        return "state"

    def set_state(s: Any, v: Any) -> str:
        return "set"

    ARCallReg.register(
        A,
        get_state=(get_state, True),
        set_state=set_state,
    )

    callable_result = ARCallReg.get_callable(A(), "get_state")
    assert callable_result is get_state

    assert ARCallReg.get_callable_flag(A(), "get_state") is True
    assert ARCallReg.get_callable_flag(A(), "set_state") is False
    assert ARCallReg.get_callable_flag(get_state) is True
    assert ARCallReg.get_callable_flag(set_state) is False


def test_register_plain_callable_resets_flag_to_false() -> None:
    """Test that registering a plain callable clears a previous tuple flag."""

    class A:
        pass

    def get_state(s: Any) -> str:
        return "state"

    ARCallReg.register(A, get_state=(get_state, True))
    assert ARCallReg.get_callable_flag(get_state) is True

    ARCallReg.register(A, get_state=get_state)
    assert ARCallReg.get_callable_flag(get_state) is False
    assert ARCallReg.get_callable(A(), "get_state") is get_state


def test_get_callable_flag_returns_false_when_source_is_not_callable() -> None:
    """Test get_callable_flag returns False for non-callables."""

    assert ARCallReg.get_callable_flag("not_callable") is False


def test_get_callable_flag_returns_false_for_missing_name() -> None:
    """Test get_callable_flag returns False when the named entry is missing."""

    class A:
        pass

    assert ARCallReg.get_callable_flag(A(), "missing") is False


def test_get_callable_returns_none_when_missing() -> None:
    """Test that get_callable returns None when no callable is found."""

    class X:
        pass

    assert ARCallReg.get_callable(X(), "nope") is None


def test_concurrent_registers_are_thread_safe() -> None:
    """Test that concurrent registrations are thread-safe."""

    class Root:
        pass

    def make_fn(i: int) -> Callable[[Any], int]:
        """Create a function that returns the given integer."""

        def f(s: Any) -> int:
            """Return the given integer."""

            return i

        return f

    def worker(i: int) -> None:
        """Register a function in the ARCallableRegistry."""

        ARCallReg.register(Root, **{f"fn{i}": make_fn(i)})

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    for i in range(8):
        assert callable(ARCallReg.get_callable(Root, f"fn{i}"))
