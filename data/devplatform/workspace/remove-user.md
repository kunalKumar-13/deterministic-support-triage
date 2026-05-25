# Removing a user (interviewer / recruiter) from your workspace

Workspace owners and admins can remove members from a DevPlatform for
Work workspace.

## To remove a member

1. Open **Settings > Members**.
2. Locate the user you want to remove.
3. Click the **three-dot menu** at the end of the row, then **Remove
   from workspace**.

If the three-dot menu does not show a **Remove** option:

- Verify your own role: only owners and admins can remove other
  members. Standard recruiters or interviewers see a limited menu.
- Verify the target's role: workspace owners cannot be removed; a new
  owner must be assigned first.
- If you are the owner trying to remove yourself, transfer ownership
  to another admin first (Settings > Members > the new owner > Make
  owner).

## What happens after removal

- The removed user loses access immediately.
- Their previously-created tests, candidates, and reports remain in
  the workspace.
- Pending invites they sent are not auto-cancelled.

## Bulk removal

For HR offboarding flows, use **Settings > Members > Bulk actions**
with a CSV of email addresses. SCIM-provisioned workspaces should
deprovision through their identity provider.
