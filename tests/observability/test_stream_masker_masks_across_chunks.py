import json

import pytest

from kora.observability.redaction import MASK, SecretRegistry, StreamMasker
from kora.protocols.secret_value import registered_forms

PLAIN: str = "plain-secret-7d41c0"
NON_ASCII: str = "секрет-значение-é"
FIRST: str = "abcdefgh12"
SECOND: str = "gh12ijkl99"
REPEATED: str = "aaaaaaaa"


class Collected:
    def __init__(self) -> None:
        self.parts: list[str] = []

    def __call__(self, text: str) -> None:
        self.parts.append(text)

    def text(self) -> str:
        return "".join(self.parts)


def registry_with(*values: str) -> SecretRegistry:
    registry = SecretRegistry()
    for value in values:
        for form in registered_forms(value):
            registry.register(form)
    return registry


def masked_stream(registry: SecretRegistry, chunks: list[bytes]) -> str:
    collected = Collected()
    with StreamMasker(registry, collected) as masker:
        for chunk in chunks:
            masker.feed(chunk)
    return collected.text()


def split_everywhere(data: bytes) -> list[list[bytes]]:
    return [[data[:index], data[index:]] for index in range(len(data) + 1)]


@pytest.mark.parametrize("value", [PLAIN, NON_ASCII])
def test_a_value_split_at_every_byte_offset_is_masked(value: str) -> None:
    registry = registry_with(value)
    data = f"before {value} after\n".encode()

    outputs = [masked_stream(registry, chunks) for chunks in split_everywhere(data)]

    assert set(outputs) == {f"before {MASK} after\n"}


def test_overlapping_values_are_masked_whole() -> None:
    registry = registry_with(FIRST, SECOND)

    output = masked_stream(registry, [b"x abcdefgh12ijkl99 y"])

    assert output == f"x {MASK} y"


def test_a_self_overlapping_value_is_masked_in_a_long_run() -> None:
    registry = registry_with(REPEATED)
    data = b"a" * 100_003

    output = masked_stream(registry, [data[:50_000], data[50_000:]])

    assert "a" * len(REPEATED) not in output
    assert set(output.replace(MASK, "")) <= {"a"}


def test_a_value_registered_mid_stream_is_masked_from_then_on() -> None:
    registry = SecretRegistry()
    collected = Collected()

    with StreamMasker(registry, collected) as masker:
        masker.feed(b"early line\n")
        registry.register(PLAIN)
        masker.feed(f"late {PLAIN}\n".encode())

    assert collected.text() == f"early line\nlate {MASK}\n"


def test_escaped_forms_of_a_value_are_masked() -> None:
    registry = registry_with(NON_ASCII)
    escaped = (json.dumps(NON_ASCII), json.dumps(NON_ASCII, ensure_ascii=False))

    output = masked_stream(registry, [f"{escaped[0]} {escaped[1]}".encode()])

    assert output == f'"{MASK}" "{MASK}"'


def test_invalid_utf8_is_replaced_and_masking_continues() -> None:
    registry = registry_with(PLAIN)

    output = masked_stream(registry, [b"bad \xff\xfe ", PLAIN.encode()])

    assert output == f"bad �� {MASK}"


def test_ordinary_output_is_not_held_back() -> None:
    registry = registry_with(PLAIN)
    collected = Collected()
    masker = StreamMasker(registry, collected)

    masker.feed(b"ordinary output line\n")

    assert masker.held == 0
    assert collected.text() == "ordinary output line\n"
    masker.close()


def test_input_above_the_chunk_size_is_split_and_masked() -> None:
    registry = registry_with(PLAIN)
    padding = b"x" * (65_536 - 5)
    data = padding + PLAIN.encode() + padding

    output = masked_stream(registry, [data])

    assert output == padding.decode() + MASK + padding.decode()


def feed_then_fail(masker: StreamMasker, data: bytes) -> None:
    with masker:
        masker.feed(data)
        raise LookupError


def test_closing_after_an_exception_emits_the_held_tail_masked() -> None:
    registry = registry_with(PLAIN)
    collected = Collected()
    masker = StreamMasker(registry, collected)

    with pytest.raises(LookupError):
        feed_then_fail(masker, PLAIN[:-1].encode())
    masker.close()

    assert collected.text() == PLAIN[:-1]
    assert masker.held == 0
