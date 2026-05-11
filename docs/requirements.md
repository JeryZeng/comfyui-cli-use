# ComfyUI Helper Requirements

## Goal

Build a Python-based full-screen terminal UI for running ComfyUI API workflows.

The tool starts directly in an interactive TUI. Users select a workflow JSON from a configured directory, optionally provide guided values for fields declared as configurable inside workflow node metadata, submit the workflow to ComfyUI, and monitor queue/task status in the same terminal.

## First-Version Scope

- Full-screen TUI, not a shell-style REPL.
- Fixed ComfyUI server address: `127.0.0.1:8188`.
- Workflow files are ComfyUI API-format JSON files.
- Workflow files are discovered recursively from a configured directory.
- Users can run selected workflows from the TUI.
- Configurable parameters are declared inside each node's `_meta.configurable`.
- Parameter entry is guided and sequential, not a popup form.
- Queue and task status are visible in the lower TUI area.
- Basic queue management is supported.
- No output file management in the first version.

## Non-Goals

- No ordinary command mode or REPL.
- No non-interactive/CI mode.
- No Windows-specific support.
- No mouse support.
- No workflow editing or saving modified defaults.
- No output download/copy/open behavior.
- No persistent task history.
- No multiple ComfyUI server profiles.
- No command-line startup arguments.
- No search/filter for workflow list.
- No model/sampler/string auto-completion except `LoadImage.image` path completion.

## Configuration

Configuration is project-local and optional.

Config file:

```yaml
./comfy-helper.yaml
```

Default config:

```yaml
workflow_dir: ./workflows
refresh_interval: 1.0
# comfyui_dir: /root/ComfyUI
```

Rules:

- If `comfy-helper.yaml` does not exist, start with defaults.
- If YAML parsing fails, start with all defaults and log the parse error.
- If individual config fields are invalid, use defaults only for invalid fields.
- `workflow_dir` relative paths are resolved from the current working directory.
- `refresh_interval` is in seconds and may be a float.
- No strict range is enforced for `refresh_interval`.
- `comfyui_dir` is optional. If set, `LoadImage.image` files are copied into `comfyui_dir/input` before submission.

Workflow history:

- Per-workflow last values are stored under `./data/workflow_history`.
- History files are formatted JSON.
- Normal values are stored directly.
- `:seed` is stored as a random-seed marker.
- `LoadImage` directory batches are stored as `image_batch` records with the original directory path.
- `data/` is ignored by git.

## Logging

Write logs to:

```text
./comfy-helper.log
```

Logs are in English.

Log at least:

- TUI startup and shutdown.
- Config loading and fallback decisions.
- Workflow scanning errors.
- Invalid workflow reasons.
- ComfyUI connection lost/restored.
- API request errors.
- Workflow submission result.
- Queue operations.
- Unhandled exceptions.

## Workflow Discovery

Workflow files are discovered recursively under `workflow_dir`.

Display name:

```text
workflows/txt2img.json       -> txt2img
workflows/image/upscale.json -> image/upscale
```

Sorting:

- Sort by file modification time descending.
- If modification times match, sort by relative path.

Refresh:

- Scan once on startup.
- User can manually refresh the workflow list.
- No automatic file watching.

Unreadable files:

- Hidden from the workflow list.
- Error is written to the log.
- TUI may show a general "some workflows failed to scan" message.

JSON syntax errors:

- Hidden from the workflow list.
- Error is written to the log.

JSON-valid but structurally invalid workflows:

- Shown as `invalid`.
- Cannot be executed.
- Current selection details show the invalid reason.

Symlinks:

- No product-specific symlink behavior is defined.
- Use the scanning library/runtime default behavior.

## Workflow JSON Format

Workflow JSON must be ComfyUI API workflow format: a top-level object whose entries are nodes.

Top-level rules:

- Top-level entries must all be workflow nodes.
- No top-level `_meta`.
- No top-level comments/metadata fields.
- Node IDs are not required to be numeric strings. ComfyUI validates final acceptability.

Each node must have:

- `class_type`
- `inputs`

`inputs` must be an object.

Node-level `_meta` is allowed and submitted to ComfyUI unchanged.

## Configurable Fields

Configurable fields are declared in each node:

```json
{
  "3": {
    "class_type": "KSampler",
    "inputs": {
      "seed": 123,
      "steps": 20
    },
    "_meta": {
      "title": "Sampler",
      "configurable": ["seed", "steps"]
    }
  }
}
```

Rules:

- `_meta` may be absent.
- `_meta.configurable` may be absent.
- `_meta.configurable` may be an empty array.
- If present, `_meta.configurable` must be an array.
- All array elements must be strings.
- Each configurable field must exist in that node's `inputs`.
- Duplicate fields in the same node are deduplicated, preserving the first occurrence.
- Same field names across different nodes are allowed.
- Configurable field order follows JSON node order, then `_meta.configurable` array order.
- Configurable field count is based on deduplicated declared fields.
- Unsupported editable types are counted but skipped during guided input.

Supported editable value types:

- string
- int
- float
- bool

Unsupported first-version editable types:

- list
- dict
- null
- other complex values

Unsupported configured fields do not make the workflow invalid. During guided input, the tool reports that the field cannot be edited and keeps the original value.

## LoadImage File Input

Special case:

- Node `class_type` is `LoadImage`.
- Configurable field is `image`.

Behavior:

- The field accepts a local file path or a directory path.
- Tab completion is supported for paths.
- No extension filtering.
- If user enters a new path, it must exist.
- Direct Enter keeps the current workflow value and does not validate it as a local file.
- If `comfyui_dir` is configured, file paths are copied into `comfyui_dir/input` and directory entries are expanded from the copied files.
- If `comfyui_dir` is not configured, file paths are uploaded through the ComfyUI API and directory entries are uploaded file by file.
- A directory path expands to one prompt per image file in that directory.

## TUI Layout

The TUI has two vertical regions.

Upper region: operation area.

- Workflow selection.
- Selected workflow details.
- Guided parameter entry.
- Submission actions and operation hints.

Lower region: status area.

- ComfyUI connection status.
- Running tasks.
- Pending queue.
- Recent task results.
- Recent messages/errors.

The upper region switches content:

- Workflow browsing state.
- Guided parameter input state.

The lower region remains visible and continues refreshing during parameter input without disturbing input focus or current typed text.

If terminal size is too small, show:

```text
Terminal too small, please resize.
```

## Workflow Browsing State

Display:

- Workflow list.
- Current selection details.

Selection details include:

- workflow name
- path
- modification time
- status: valid/invalid
- configurable field count
- unsupported field count if any
- invalid reason if invalid

Workflow list supports scrolling.

If refreshing the workflow list:

- Try to preserve current selection.
- If current workflow no longer exists, select the first item.

## Guided Parameter Input

Start:

- Select valid workflow.
- Press Enter.
- If no configurable fields exist, submit directly.
- If configurable fields exist, enter guided input in the upper region.
- Press `b` when starting a workflow to use the same guided input flow, then choose how many times to submit after fields are completed.
- `Shift+Enter` may also trigger batch mode if the terminal reports it as a distinct key.

Display each field as:

```text
[1/4] [6] Positive Prompt / CLIPTextEncode.text
Current value: a cat
Input new value, Enter keeps current, :run submits now, Esc cancels:
>
```

Node label:

- Always include node ID.
- If `_meta.title` exists, show it as-is.
- If no title exists, show `class_type`.

Input rules:

- One line per field.
- Text input is single-line only.
- Current value is shown as-is, without truncation.
- Input is stripped of leading/trailing whitespace.
- Empty input after stripping keeps the current value.
- First version cannot set a non-empty string field to an empty string.
- `Esc` immediately cancels the run and returns to workflow browsing.
- `:run` skips remaining fields and submits immediately.
- `:batch` submits with the values entered so far, then asks for a positive integer batch count.
- `Shift+Enter` does the same when supported by the terminal.
- Integer fields accept `:seed` to request a fresh random integer at submit time.
- No final submit confirmation.

## Batch Submission

Batch submission is a submit-time variant, not a separate workflow type.

- A batch count prompt asks for a positive integer `N`.
- Submitting creates `N` independent ComfyUI prompts.
- Each prompt gets a unique client-generated `prompt_id`.
- The same resolved workflow parameters are used for every prompt.
- If `LoadImage.image` is a directory, it expands to one prompt per image file.
- If an integer field was set to `:seed`, that field is re-randomized before every prompt submission.
- If a `LoadImage.image` value was changed to a file, the local file is prepared once during guided input and the final ComfyUI image name is reused for every prompt in the batch.
- If one submission fails, already submitted prompts remain queued and the app reports how many were submitted before the failure.

Boolean parsing accepts:

- `true` / `false`
- `yes` / `no`
- `y` / `n`
- `1` / `0`
- `on` / `off`

Boolean parsing is case-insensitive.

Numeric validation:

- int fields must parse as integers.
- float fields must parse as floats.
- No local min/max/range validation.
- ComfyUI handles deeper workflow validation.

Submit result:

- On success, return to workflow browsing and refresh queue.
- On failure, return to workflow browsing and show a short error in messages.
- Detailed error is written to log.
- Input values are not retained after failure.

## ComfyUI Status and Queue

Connection:

- TUI starts even if ComfyUI is offline.
- Offline state is shown.
- Tool periodically attempts to reconnect.
- User may manually refresh status.
- Offline mode allows workflow browsing but not submission.

Server:

```text
http://127.0.0.1:8188
ws://127.0.0.1:8188/ws
```

Queue display:

- Show global ComfyUI running queue.
- Show global ComfyUI pending queue.
- Tasks submitted by this TUI session show workflow name.
- Other tasks show `unknown`.

Progress tracking:

- Focus on tasks submitted by the current TUI session.
- Unknown tasks may be displayed as busy/unknown without detailed tracking.

Recent:

- Only current-session tasks are recorded.
- Keep last 20 records.
- Not persisted.
- Unknown tasks are not recorded.
- Interrupt action itself does not add an interrupted record.

Messages:

- Keep last 5 messages.
- Not persisted.

## Queue Management

Supported:

- Interrupt current running task.
- Delete selected pending task.
- Clear all pending tasks.

Scope:

- Pending queue display is global.
- Deleting a pending task may delete any pending item, including `unknown`.
- Clearing pending queue clears all ComfyUI pending tasks, including `unknown`.
- Interrupt may interrupt any current running task, including `unknown`.

Safety:

- Dangerous operations require confirmation:
  - interrupt running task
  - delete pending task
  - clear pending queue
  - quit while this session has running/pending submitted tasks
- Confirmation defaults to cancel.

Exit:

- Quitting the TUI does not interrupt running tasks.
- Quitting the TUI does not delete pending tasks.
- Submitted tasks continue in ComfyUI.
- Session task mapping is lost after exit.

## Keyboard Controls

Global/main controls:

```text
Up/Down   select item
Enter     run selected workflow
b         run selected workflow as batch
u         repeat the last successful submission
Shift+Enter run selected workflow as batch if supported by terminal
Tab       switch focus between upper operation area and lower status area
r         refresh workflow list
s         refresh ComfyUI status
i         interrupt current running task
d         delete selected pending task
c         clear pending queue
q         quit
Esc       close confirmation/cancel current run
```

Guided input controls:

```text
Enter     keep current value or accept typed value
:batch    submit values entered so far as batch
Shift+Enter submit values entered so far as batch if supported by terminal
:run      skip remaining fields and submit now
Esc       cancel run and return to workflow browsing
Tab       no focus switch during guided input
```

Batch count controls:

```text
Enter     submit batch count
Esc       cancel batch submit
```

Focus:

- In browsing mode, Tab switches focus between upper and lower regions.
- In guided input mode, focus is locked in the upper region.
- Lower region keeps refreshing but is read-only while guided input is active.

Scrolling:

- Workflow list scrolls with selection.
- Pending queue scrolls with selection when lower region has focus.

## Visual Language

TUI language:

- English.

Workflow/node/user content:

- Display original text as-is, including Chinese `_meta.title`.

Color:

- online: green
- offline: red
- running: yellow or blue
- pending: normal or yellow
- completed: green
- failed/error: red
- invalid workflow: red
- selected item: highlighted
- focused region: visible border/title highlight

Keyboard help:

- Always show a one-line help hint.
- Hint changes based on current state.

## Open Technical Questions

Resolved for the first implementation:

1. TUI framework: Textual.
2. `LoadImage.image` local files: copy into `comfyui_dir/input` when `comfyui_dir` is configured; otherwise upload through `POST /upload/image`. Directory inputs expand to one prompt per image file.
3. API paths: bare ComfyUI paths such as `/prompt`, `/queue`, `/ws`.
4. Progress: WebSocket for current-session task progress plus `/queue` polling for global queue state.
5. Prompt identity: generate UUID `prompt_id` client-side and submit with a session `client_id`.
6. Startup: no packaging yet; run with `.venv/bin/python main.py`.
7. Runtime model: async Textual app, async HTTP, async WebSocket.
