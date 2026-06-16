import argparse
import json
import os
import re
import sys
from pathlib import Path

from migrate_azure_devops_repos import ENV_FILE, load_env_file


PLACEHOLDER_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")


def fail(message: str) -> None:
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(1)


def replace_placeholders(raw_value: str) -> str:
    missing_keys: list[str] = []

    def replacer(match: re.Match[str]) -> str:
        key = match.group(1)
        value = os.environ.get(key)
        if value is None:
            missing_keys.append(key)
            return match.group(0)
        return value

    rendered_value = PLACEHOLDER_PATTERN.sub(replacer, raw_value)
    if missing_keys:
        missing_list = ", ".join(sorted(set(missing_keys)))
        fail(f"Missing environment values for: {missing_list}")
    return rendered_value


def render_value(value):
    if isinstance(value, dict):
        return {key: render_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [render_value(item) for item in value]
    if isinstance(value, str):
        return replace_placeholders(value)
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render configuration.json using values from a .env file."
    )
    parser.add_argument(
        "--env-file",
        default=ENV_FILE,
        help="Path to the .env file that contains migration settings.",
    )
    parser.add_argument(
        "--template",
        default="configuration.json",
        help="Path to the JSON template file.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path to write the rendered JSON configuration.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_env_file(Path(args.env_file))

    template_path = Path(args.template)
    if not template_path.exists():
        fail(f"Template file not found: {template_path}")

    template_data = json.loads(template_path.read_text(encoding="utf-8"))
    rendered_data = render_value(template_data)

    output_path = Path(args.output)
    output_path.write_text(json.dumps(rendered_data, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()