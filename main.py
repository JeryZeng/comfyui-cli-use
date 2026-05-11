import sys

from comfyui_helper.app import ComfyHelperApp
from comfyui_helper.logging_setup import setup_logging


def main() -> int:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("comfy-helper requires an interactive terminal.", file=sys.stderr)
        return 1

    setup_logging()
    ComfyHelperApp().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
