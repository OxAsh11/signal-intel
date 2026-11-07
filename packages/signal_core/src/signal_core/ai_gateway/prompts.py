"""Prompt registry: immutable versioned files `prompts/<name>/v<N>.md` with a SHA-256 each.

File format: front matter (`capability: <name>`), a `---` line, the system text, a
`--- user ---` line, then the user template with `{variables}`.
"""

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent / "prompts"
_USER_MARKER = "--- user ---"


class Capability(StrEnum):
    EXTRACT = "extract"
    JUDGE = "judge"
    PREJUDGE = "prejudge"
    SUMMARISE = "summarise"
    EMBED = "embed"
    WRITE = "write"


@dataclass(frozen=True)
class Prompt:
    name: str
    version: int
    capability: Capability
    system: str
    user_template: str
    sha256: str


def file_sha256(raw: bytes) -> str:
    """Hash of the file with line endings normalised, so checkouts agree on every OS."""
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def load_prompt(name: str, version: int) -> Prompt:
    path = PROMPTS_DIR / name / f"v{version}.md"
    raw = path.read_bytes()
    lines = raw.decode("utf-8").replace("\r\n", "\n").split("\n")
    try:
        sep = lines.index("---")
        user_at = lines.index(_USER_MARKER)
    except ValueError as exc:
        raise ValueError(
            f"prompt {name}/v{version} is missing a '---' or '{_USER_MARKER}' line"
        ) from exc
    front = dict(
        (k.strip(), v.strip())
        for k, _, v in (ln.partition(":") for ln in lines[:sep] if ln.strip())
    )
    return Prompt(
        name=name,
        version=version,
        capability=Capability(front["capability"]),
        system="\n".join(lines[sep + 1 : user_at]).strip(),
        user_template="\n".join(lines[user_at + 1 :]).strip(),
        sha256=file_sha256(raw),
    )


def render(prompt: Prompt, variables: Mapping[str, str]) -> str:
    """Fill the user template. Raises KeyError on a missing variable."""
    return prompt.user_template.format_map(_Strict(variables))


class _Strict(dict[str, str]):
    def __init__(self, data: Mapping[str, str]) -> None:
        super().__init__(data)

    def __missing__(self, key: str) -> str:
        raise KeyError(key)
