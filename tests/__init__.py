import os

# Tests check the shared files in the repository, never someone's private
# versions in local/ (see core/config.py).
os.environ["XIAOLIAN_IGNORE_LOCAL"] = "1"
