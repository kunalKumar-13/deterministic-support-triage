# Claude Projects — instructions and persistent context

A **Project** in Claude is a workspace that bundles instructions, files,
and conversations under a shared context. When you start a new
conversation inside a Project, Claude reads the project instructions and
attached files as system context.

## If project instructions don't appear to apply

- **Check the project scope** — instructions only apply to conversations
  started **inside** the Project, not to chats opened from the main
  sidebar.
- **Refresh the conversation** — newly added instructions are picked up
  on the next user turn, not retroactively in the existing turn.
- **Check the model** — some older models in Project context may have
  shorter effective instruction windows; switching to the current
  model usually resolves this.
- **Verify the instructions are saved** — open the project settings and
  confirm the instructions text is present and saved.

If the instructions still appear to be ignored after these steps,
contact support with: the project name, an example conversation, and a
description of which instruction was not followed.
