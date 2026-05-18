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
Directory inputs are expanded into one prompt per supported image file. Supported extensions are `.png`, `.jpg`, `.jpeg`, and `.webp`.
When a directory contains multiple supported image files, the app asks whether to shuffle file order for that run. The choice is stored with the directory value and reused by `u` repeat and history restore.
The default upper area is a single interaction panel for workflow selection and guided input. Press `h` on a selected workflow to enter history browse mode.

The app stores per-workflow history under `./data/workflow_history` as formatted JSON.
Each workflow keeps up to 10 history records. Reusing an existing record moves it to the newest position instead of duplicating it. Normal values are stored directly. `:seed` is saved as a random-seed marker. `LoadImage` directory batches are saved as `image_batch` records with the original directory path and shuffle flag. Template references are saved as the original template text, not as resolved values.

ComfyUI is expected at:

```text
http://127.0.0.1:8188
```

## Configurable Fields

The app only exposes fields that are listed in each node's `_meta.configurable` array.
Each entry is a full field path starting from the node's `inputs` object.

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

- Each item in `_meta.configurable` must match a field path under that node's `inputs` object exactly.
- Nested object fields use dot paths. For example, `lora_2.strength` maps to `inputs.lora_2.strength`.
- Only object paths are supported. Array indexes such as `items.0.name` are not supported.
- Supported editable field types are strings, integers, floats, and booleans. Unsupported configured fields are counted and skipped during guided input.
- `LoadImage.image` is a special case. It accepts a local file path or a directory path, and the CLI treats it as a path-based input instead of a plain string.
- If a configured field name contains `path`, the CLI also enables path completion for it.
- Fields not listed in `_meta.configurable` are ignored by guided input, even if they exist in the workflow JSON.
- The app resolves fields in workflow dependency order before submission, so a field can reference another field even if that other field appears later in the guided input flow.
- Template references use `${node_id.field_name}` syntax. A value can be a plain reference like `${117.value}` or a string with embedded references like `prefix_${117.value}_suffix`.
- Nested configurable paths are referenced with the same full path, for example `${123.lora_2.strength}`.
- Prefix a reference with a backslash to keep it as literal text. For example, `\${117.value}` submits `${117.value}`. Use `\\${117.value}` when you need one literal backslash followed by the resolved reference value.
- Integer, float, and boolean fields allow whole-field references and keep the referenced type.
- String fields allow interpolation and coerce referenced values to text during substitution.
- `LoadImage` values can reference other fields too. If a referenced `LoadImage` field is a directory batch, the reference resolves to the actual image path used by the current prompt branch.

Effects in the TUI:

- The guided input flow walks through configurable fields in workflow graph order.
- Each configurable field can be edited individually from the keyboard.
- Nested fields are written back to only that path, so sibling values in the same object are preserved.
- History browse mode shows saved records for the selected workflow and lets you reuse one for editing, single submit, or batch submit.
- Saved workflow history tracks only configurable fields, and it preserves the original template text for template-based values.

## Basic Controls

- `Enter`: run the selected workflow once.
- `b`: run the selected workflow as a batch; after guided fields, enter the submit count.
- `u`: repeat the last recorded submission.
- `h`: enter or exit history browse for the selected workflow.
- In history browse, `↑/↓` selects a record, `Enter` edits from that record, `u` submits it once, and `b` starts batch submit from it.
- `Shift+Enter`: also attempts batch mode when the terminal reports it as a distinct key.
- In integer fields, `:seed` requests a fresh random value when submission values are resolved. With a directory `LoadImage` batch, fields resolved before the directory batch share one generated value across that directory expansion; fields resolved after the directory batch are resolved per expanded image prompt.
- In field editing mode, `F2` fills the current field value back into the input box.
- In field editing mode, `F7` clears the current input box content.
- In field editing mode, `F3` goes to the previous editable field.
- In field editing mode, `Esc` cancels the workflow run.
- `Tab`: switch focus between workflow selection and status/queue area.
- `q`: quit the TUI.
