import os
import yaml


# Repository root. Data, prompt and asset paths are resolved from here so a
# module keeps working no matter how deep inside modules/ it sits.
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def root_path(*parts):
    return os.path.join(ROOT_DIR, *parts)


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
