from typing import Annotated

from pydantic import StringConstraints

from kora.protocols.common import BoundaryModel
from kora.protocols.identity import Identifier

PathText = Annotated[
    str,
    StringConstraints(
        strict=True, min_length=1, max_length=4096, pattern=r"^[^\x00-\x1f\x7f]+$"
    ),
]


class WorkspaceTarget(BoundaryModel):
    root: PathText
    host_path: PathText | None


class WorkspaceRef(BoundaryModel):
    attempt_id: Identifier
    root: PathText
    repo: PathText
    commit: Identifier
    base_commit: Identifier
    branch: Identifier | None
