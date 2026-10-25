import os
import yaml


# Repository root. Data, prompt and asset paths are resolved from here so a
# module keeps working no matter how deep inside modules/ it sits.
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def root_path(*parts):
    return os.path.join(ROOT_DIR, *parts)


# Private versions of shared files live in local/ under the same path, e.g.
# local/prompts/teams.yaml, and win over the repository's copy. local/ is
# gitignored, so they never reach GitHub. The tests set XIAOLIAN_IGNORE_LOCAL
# so they always check the shared defaults.
LOCAL_DIR = root_path("local")


def use_local():
    return not os.environ.get("XIAOLIAN_IGNORE_LOCAL")


def content_path(*parts):
    """A shared file such as prompts/teams.yaml, or its private version in local/ if there is one."""
    private = os.path.join(LOCAL_DIR, *parts)
    return private if use_local() and os.path.exists(private) else root_path(*parts)


def load_config():
    """Read config.yaml.

    A missing file yields an empty dict, so optional features simply stay off
    (and tests can import modules without real credentials).
    """
    path = root_path("config.yaml")
    if not os.path.exists(path):
        return {}
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f) or {}
