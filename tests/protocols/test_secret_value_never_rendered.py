import ast
import json
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

import kora.conformance.secrets
import kora.protocols.common
import kora.protocols.identity
from kora.conformance.secrets import check_never_rendered
from kora.observability import LoggingSettings
from kora.protocols.common import BoundaryModel
from kora.protocols.secret_value import SecretScope
from tests.protocols.conftest import StartCapture
from tests.protocols.secret_support import ESCAPED, PLAIN, Holder, Wrapper

SOURCE: Path = Path(__file__).parents[2] / "src"
TRANSFER_FAILURES: tuple[str, ...] = (
    '{"credential": 7}',
    '{"credential": "short"}',
    '{"credential": "' + "x" * 20000 + '"}',
    '{"credential": "' + PLAIN + '"',
)


def failure_forms(error: ValidationError) -> str:
    return " ".join(
        (
            str(error),
            repr(error),
            error.json(include_input=False),
            json.dumps(error.errors(include_input=False, include_context=False)),
        )
    )


@pytest.mark.parametrize("value", [PLAIN, ESCAPED])
def test_secret_value_never_rendered(start_capture: StartCapture, value: str) -> None:
    capture = start_capture(LoggingSettings())

    secret = capture.scope.admit(value)

    check_never_rendered(secret, value)
    assert (
        Holder.model_validate_json(
            json.dumps({"credential": value}), context={"secret_scope": capture.scope}
        ).credential.reveal()
        == value
    )


@pytest.mark.parametrize("document", TRANSFER_FAILURES)
@pytest.mark.parametrize("model", [Holder, Wrapper])
def test_transfer_failures_carry_no_input(
    start_capture: StartCapture, document: str, model: type[BaseModel]
) -> None:
    scope: SecretScope = start_capture(LoggingSettings()).scope
    wrapped = document if model is Holder else '{"inner": ' + document + "}"

    with pytest.raises(ValidationError) as raised:
        model.model_validate_json(wrapped, context={"secret_scope": scope})

    rendered = failure_forms(raised.value)
    assert PLAIN not in rendered
    assert "short" not in rendered
    assert "xxxxxxxxxx" not in rendered
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def model_classes() -> list[type[BaseModel]]:
    modules = (kora.protocols.common, kora.protocols.identity, kora.conformance.secrets)
    return [
        item
        for module in modules
        for item in vars(module).values()
        if isinstance(item, type)
        and issubclass(item, BaseModel)
        and item.__module__ == module.__name__
        and item is not BoundaryModel
    ]


def test_every_boundary_model_derives_from_the_base() -> None:
    classes = model_classes()

    assert classes
    assert all(issubclass(item, BoundaryModel) for item in classes)


def unsafe_error_calls(tree: ast.AST) -> list[int]:
    lines: list[int] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "errors"
            and not any(
                keyword.arg == "include_input"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value is False
                for keyword in node.keywords
            )
        ):
            lines.append(node.lineno)
    return lines


def test_no_source_reads_validation_errors_with_input() -> None:
    offenders = {
        str(path.relative_to(SOURCE)): unsafe_error_calls(ast.parse(path.read_text()))
        for path in SOURCE.rglob("*.py")
    }

    assert {path: lines for path, lines in offenders.items() if lines} == {}
