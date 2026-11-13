"""`python -m modules.fun.teams.check`: reads the game file the bot would read (local/prompts/teams.yaml
when there is one, else prompts/teams.yaml) and says what is in it, or what is wrong with it.

For whoever edits the file by hand: a mistake in it stops the bot at startup, so check before
restarting. It doesn't know the bot's other command words, so an action named like one of them is
still only found at startup.
"""
import sys
import yaml
from modules.fun.teams.rules import RULES_FILE, load_rules


def main(path=RULES_FILE, out=sys.stdout):
    try:
        rules = load_rules(path)
    except (OSError, ValueError, yaml.YAMLError) as e:
        print(f"!! {e}", file=out)
        return 1
    print(f"{path} 没有问题：{len(rules.actions)} 个操作，{len(rules.events)} 件突发事件，"
          f"{len(rules.spots)} 个躲的地方。", file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
