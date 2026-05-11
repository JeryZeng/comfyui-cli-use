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

Default config:

```yaml
workflow_dir: ./workflows
refresh_interval: 1.0
# comfyui_dir: /root/ComfyUI
```

If `comfyui_dir` is set, `LoadImage` files are copied into `comfyui_dir/input` before submission.
Directory inputs are expanded into one prompt per image file.

ComfyUI is expected at:

```text
http://127.0.0.1:8188
```

## Basic Controls

- `Enter`: run the selected workflow once.
- `b`: run the selected workflow as a batch; after guided fields, enter the submit count.
- `u`: repeat the last successful submission.
- `Shift+Enter`: also attempts batch mode when the terminal reports it as a distinct key.
- In integer fields, `:seed` requests a fresh random value on every submission.
- `Tab`: switch focus between workflow selection and status/queue area.
- `q`: quit the TUI.
