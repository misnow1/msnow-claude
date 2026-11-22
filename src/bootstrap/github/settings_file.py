"""Read the GitHub settings file and the state file, and check them."""

from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError

from bootstrap.errors import BootstrapError
from bootstrap.github.models import CurrentState, DesiredState

SETTINGS_PATH = Path(".github/bootstrap.yml")
MERGE_TAG = "tag:yaml.org,2002:merge"


def describe(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc']) or 'file'}: {item['msg']}"
        for item in error.errors()
    )


def validate[T: BaseModel](model: type[T], data: object, name: str) -> T:
    try:
        return model.model_validate(data)
    except ValidationError as error:
        raise BootstrapError(f"{name} is not valid: {describe(error)}") from error


class UniqueKeyLoader(yaml.SafeLoader):
    """A YAML loader that rejects a duplicate key. PyYAML keeps the last value without it."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[object, object]:
        seen: set[object] = set()
        for key_node, _ in node.value:
            if key_node.tag == MERGE_TAG:
                continue
            key = self.construct_object(key_node, deep=True)
            try:
                duplicate = key in seen
            except TypeError:
                raise yaml.constructor.ConstructorError(
                    None, None, "found an unhashable key", key_node.start_mark
                ) from None
            if duplicate:
                raise yaml.YAMLError(f"duplicate key {key!r} (line {key_node.start_mark.line + 1})")
            seen.add(key)
        return super().construct_mapping(node, deep)


def check_not_symlinked(target: Path) -> Path:
    """Return the settings path. Reject a symlink, so the command reads no file outside the repo."""
    path = target / SETTINGS_PATH
    for part in (target / SETTINGS_PATH.parent, path):
        if part.is_symlink():
            raise BootstrapError(f"{part.relative_to(target)} is a symlink. Use a regular file.")
    return path


def parse_desired(text: str, name: str) -> DesiredState:
    """Parse and check the text of a settings file."""
    try:
        data = yaml.load(text, Loader=UniqueKeyLoader)
    except yaml.YAMLError as error:
        raise BootstrapError(f"{name} is not valid YAML: {error}") from error
    if data is None:
        raise BootstrapError(f"{name} is empty.")
    return validate(DesiredState, data, name)


def load_desired(target: Path) -> DesiredState:
    """Read the desired state from the GitHub settings file in the target repo."""
    path = check_not_symlinked(target)
    if not path.is_file():
        raise BootstrapError(
            f"{SETTINGS_PATH} is missing in {target}. Run `bootstrap apply` first."
        )
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise BootstrapError(f"{SETTINGS_PATH} is not valid UTF-8: {error}") from error
    return parse_desired(text, str(SETTINGS_PATH))


def load_state(path: Path) -> CurrentState:
    """Read a GitHub state file, which replaces the live read of GitHub."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise BootstrapError(f"cannot read the state file {path}: {error}") from error
    try:
        return CurrentState.model_validate_json(text)
    except ValidationError as error:
        raise BootstrapError(f"the state file {path} is not valid: {describe(error)}") from error
