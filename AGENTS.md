# Agent Notes

## Textual Key Handling

- Avoid handling the same shortcut in both `BINDINGS` and `on_key`.
- For state-toggle shortcuts, duplicate handling can make the UI enter a mode and immediately exit it in the same key press. This happened with `h`: `on_key` entered history browse, then the `BINDINGS` action ran and toggled history browse off again.
- Prefer one owner per shortcut:
  - Use `on_key` when the behavior depends on current mode, focus, confirmation prompts, or input state.
  - Use `BINDINGS` only for simple actions that do not need custom event ordering.
- When debugging key behavior, temporarily log the normalized key, current mode, and focus area, then downgrade or remove noisy logging before finishing.
