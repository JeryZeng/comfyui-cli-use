# comfyui-helper

Terminal UI for selecting and running ComfyUI API workflows.

## Setup

This project uses a local Python 3.10 virtual environment.

```bash
python3.10 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## prepare workflows

put `workflow-api.json(API FORMAT)` files into `./workflows`

## Run

```bash
.venv/bin/python main.py
```

The app reads optional project config from:

```text
./comfy-helper.yaml
```

Copy `comfy-helper.yaml.example` to `comfy-helper.yaml` to create a local config file.

Default config:

```yaml
workflow_dir: ./workflows
refresh_interval: 1.0
comfyui_server: http://127.0.0.1:8188
# comfyui_dir: /root/ComfyUI
```

If `comfyui_server` is not set, the app uses `http://127.0.0.1:8188`.
If you omit the scheme, the app assumes `http://` and derives the WebSocket URL from that scheme. If you set `https://`, the WebSocket URL follows as `wss://`.
If `comfyui_dir` is set, `LoadImage` files are copied into `comfyui_dir/input` before submission.
Directory inputs are expanded into one prompt per image file.
When a directory contains multiple image files, the app asks whether to shuffle file order for that run. The choice is stored with the directory value and reused by `u` repeat and history restore.
The upper area is split into a left interaction panel and a right read-only history panel on wide screens. The history panel shows the selected workflow's latest submitted field values. It is hidden on small screens.

The app stores per-workflow history under `./data/workflow_history` as formatted JSON.
Normal values are stored directly. `:seed` is saved as a random-seed marker, and `LoadImage` directory batches are saved as `image_batch` records with the original directory path and shuffle flag.

ComfyUI is expected at:

```text
http://127.0.0.1:8188
```

## Configurable Fields

The app only exposes fields that are listed in each node's `_meta.configurable` array.

Example:

```json
{
  "117": {
    "class_type": "PrimitiveInt",
    "inputs": {
      "value": 0
    },
    "_meta": {
      "title": "Seed",
      "configurable": ["value"]
    }
  }
}
```

Rules:

- Each item in `_meta.configurable` must match a key in that node's `inputs` object exactly.
- A configurable field must be editable by the CLI. Supported field types are strings, integers, floats, and booleans.
- `LoadImage.image` is a special case. It accepts a local file path or a directory path, and the CLI treats it as a path-based input instead of a plain string.
- If a configured field name contains `path`, the CLI also enables path completion for it.
- Fields not listed in `_meta.configurable` are ignored by guided input, even if they exist in the workflow JSON.

Effects in the TUI:

- The guided input flow walks through configurable fields in workflow graph order.
- Each configurable field can be edited individually from the keyboard.
- The right-side history panel shows the last submitted value for each configurable field.
- Saved workflow history also only tracks configurable fields.

## Basic Controls

- `Enter`: run the selected workflow once.
- `b`: run the selected workflow as a batch; after guided fields, enter the submit count.
- `u`: repeat the last successful submission.
- `Shift+Enter`: also attempts batch mode when the terminal reports it as a distinct key.
- In integer fields, `:seed` requests a fresh random value on every submission.
- In field editing mode, `F2` fills the current field value back into the input box.
- In field editing mode, `F7` clears the current input box content.
- In field editing mode, `F3` goes to the previous editable field.
- In field editing mode, `Esc` cancels the workflow run.
- `Tab`: switch focus between workflow selection and status/queue area.
- `q`: quit the TUI.
