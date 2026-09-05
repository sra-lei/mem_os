"""infra/llm 工厂 + 降级保底（failover）的离线单测。

用可控 fake client（异常 / 空返回 / 成功）验证：
- 工厂按配置实例化：单 provider 直出、多 provider 包成 FailoverClient、
  跳过未知/不可用 provider、无可用时抛 ClientConfigError；
- failover 降级：异常与空返回都触发切换、全部失败语义、主路冷却探测恢复。
"""

from __future__ import annotations

import pytest

from os_mem.infra.llm import factory as llm_factory
from os_mem.infra.llm.factory import (
    ClientConfigError,
    LLMClientFactory,
    get_llm_client,
    register_llm_client,
)
from os_mem.infra.llm.failover import FailoverClient


class _Fake:
    """行为可编排的假 client：按调用次数消费 script，超界后重复最后一个。"""

    def __init__(self, script: str | Exception | list[str | Exception]) -> None:
        self._script: list[str | Exception] = (
            [script]
            if isinstance(script, (str, Exception))
            else list(script)
        )
        self.calls = 0

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        response_format: dict | None = None,
        retries: int = 3,
    ) -> str:
        self.calls += 1
        step = self._script[min(self.calls - 1, len(self._script) - 1)]
        if isinstance(step, Exception):
            raise step
        return step


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t


_MSGS: list[dict[str, str]] = [{'role': 'user', 'content': 'hi'}]


# --------------------------------------------------------------------------
# 工厂：配置驱动实例化
# --------------------------------------------------------------------------


def test_factory_single_provider_returns_client_directly() -> None:
    a = _Fake('a')
    factory = LLMClientFactory(builders={'a': lambda: a})
    assert factory.build(['a']) is a
    assert factory.build('a') is a


def test_factory_multi_provider_wraps_failover() -> None:
    a, b = _Fake('a'), _Fake('b')
    factory = LLMClientFactory(builders={'a': lambda: a, 'b': lambda: b})
    client = factory.build('a, b')
    assert isinstance(client, FailoverClient)
    assert client.clients == [a, b]


def test_factory_skips_unknown_and_unavailable_provider() -> None:
    a = _Fake('a')

    def bad() -> _Fake:
        raise ClientConfigError('no key')

    factory = LLMClientFactory(builders={'a': lambda: a, 'bad': bad})
    # 未知 provider 与构造失败 provider 都被跳过，剩可用者直接返回
    assert factory.build(['a', 'unknown', 'bad']) is a
    # 全部不可用 -> 明确报错，不静默
    all_bad = LLMClientFactory(builders={'bad': bad})
    with pytest.raises(ClientConfigError, match='bad'):
        all_bad.build(['bad'])
    with pytest.raises(ClientConfigError):
        LLMClientFactory(builders={}).build('')


def test_register_llm_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        llm_factory, '_LLM_CLIENT_BUILDERS', dict(llm_factory._LLM_CLIENT_BUILDERS)
    )
    register_llm_client('fake_provider', lambda: _Fake('x'))
    assert 'fake_provider' in LLMClientFactory().available_providers()
    with pytest.raises(ValueError, match='已注册'):
        register_llm_client('fake_provider', lambda: _Fake('x'))
    with pytest.raises(ValueError):
        register_llm_client('', lambda: _Fake('x'))


def test_default_factory_build_and_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    # 默认（单测环境无 API key 也允许实例化，调用时才失败——与旧行为一致）
    client = LLMClientFactory().build()
    assert hasattr(client, 'chat')
    # get_llm_client 单例缓存同一实例
    monkeypatch.setattr(llm_factory, '_default_client', None)
    first = get_llm_client()
    assert get_llm_client() is first


# --------------------------------------------------------------------------
# failover：降级保底
# --------------------------------------------------------------------------


def test_failover_falls_back_on_exception_and_stays_on_backup() -> None:
    clock = _Clock()
    a, b = _Fake(Exception('boom')), _Fake('ok')
    f = FailoverClient([a, b], probe_interval=300, now_fn=clock.now)
    assert f.chat(_MSGS) == 'ok'
    assert f.active_index == 1
    assert a.calls == 1 and b.calls == 1
    # 冷却期内再次请求直接走备胎，不反复踩主路失败开销
    assert f.chat(_MSGS) == 'ok'
    assert a.calls == 1 and b.calls == 2


def test_failover_empty_content_falls_back() -> None:
    clock = _Clock()
    a, b = _Fake(''), _Fake('ok')
    f = FailoverClient([a, b], probe_interval=300, now_fn=clock.now)
    assert f.chat(_MSGS) == 'ok'
    assert a.calls == 1 and b.calls == 1


def test_failover_all_fail_raises_last_exception() -> None:
    clock = _Clock()
    a, b = _Fake(Exception('e1')), _Fake(Exception('e2'))
    f = FailoverClient([a, b], probe_interval=300, now_fn=clock.now)
    with pytest.raises(Exception, match='e2'):
        f.chat(_MSGS)


def test_failover_all_empty_returns_empty_string() -> None:
    clock = _Clock()
    a, b = _Fake(''), _Fake('')
    f = FailoverClient([a, b], probe_interval=300, now_fn=clock.now)
    assert f.chat(_MSGS) == ''
    assert a.calls == 1 and b.calls == 1


def test_failover_recovers_to_primary_after_cooldown() -> None:
    clock = _Clock()
    # 主路：第一次调用失败，之后恢复
    a = _Fake([Exception('boom'), 'a'])
    b = _Fake('b')
    f = FailoverClient([a, b], probe_interval=300, now_fn=clock.now)

    clock.t = 0
    assert f.chat(_MSGS) == 'b'  # 主路失败 -> 备胎，游标=1
    assert f.active_index == 1

    clock.t = 10
    assert f.chat(_MSGS) == 'b'  # 冷却期内不探测主路
    assert a.calls == 1

    clock.t = 301
    assert f.chat(_MSGS) == 'a'  # 过冷却期探测主路，恢复成功回主路
    assert f.active_index == 0

    assert f.chat(_MSGS) == 'a'  # 回主路后持续走主路
    assert a.calls == 3 and b.calls == 2


def test_failover_probe_failure_refreshes_cooldown() -> None:
    clock = _Clock()
    # 主路：第一次与探测时都失败，第 3 次恢复
    a = _Fake([Exception('b1'), Exception('b2'), 'a'])
    b = _Fake('b')
    f = FailoverClient([a, b], probe_interval=300, now_fn=clock.now)

    clock.t = 0
    assert f.chat(_MSGS) == 'b'  # 主路第一次失败
    clock.t = 301
    assert f.chat(_MSGS) == 'b'  # 探测主路仍失败 -> 刷新冷却，继续备胎
    assert a.calls == 2
    clock.t = 400
    assert f.chat(_MSGS) == 'b'  # 冷却未到(距 301 仅 99s)，不探测
    assert a.calls == 2
    clock.t = 610
    assert f.chat(_MSGS) == 'a'  # 距上次探测 309s，再次探测成功回主路
    assert a.calls == 3 and b.calls == 3


def test_failover_requires_at_least_one_client() -> None:
    with pytest.raises(ValueError, match='至少'):
        FailoverClient([])
