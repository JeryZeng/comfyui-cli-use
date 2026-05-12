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
When a directory contains multiple image files, the app asks whether to shuffle file order for that run. The choice is stored with the directory value and reused by `u` repeat and history restore.
The upper area is split into a left interaction panel and a right read-only history panel on wide screens. The history panel shows the selected workflow's latest submitted field values. It is hidden on small screens.

The app stores per-workflow history under `./data/workflow_history` as formatted JSON.
Normal values are stored directly. `:seed` is saved as a random-seed marker, and `LoadImage` directory batches are saved as `image_batch` records with the original directory path and shuffle flag.

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
- In field editing mode, `F2` fills the current field value back into the input box.
- In field editing mode, `F7` clears the current input box content.
- In field editing mode, `F3` goes to the previous editable field.
- In field editing mode, `Esc` cancels the workflow run.
- `Tab`: switch focus between workflow selection and status/queue area.
- `q`: quit the TUI.
