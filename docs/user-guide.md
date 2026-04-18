# User Guide

This guide is the operator-facing reference for D. manager.

It is written for the people who actually run the lab workflow day to day: adding stocks, creating crosses, organizing trays, flipping vials, generating labels, and keeping the system healthy after deployment.

## What D. manager Does

D. manager gives a fly lab one place to manage:

- stock records
- crosses derived from existing stocks
- tray layouts and occupied positions
- flip schedules and attention queues
- QR label generation and scanner-assisted maintenance
- workbook-based import and export for administrative data moves

The public product name is D. manager. The repository and package name remain FlyManager.

## Who Should Read This

- Operators: for daily maintenance, flipping, tray movement, and label generation
- Lab admins: for first-run setup, user onboarding, workbook workflows, and maintenance jobs
- Deployment owners: for launch checks and links into the deployment and recovery runbooks

## Deployment-Ready Setup Checklist

Use this checklist after the application is deployed and reachable.

1. Confirm that the app opens and authentication pages load correctly.
2. Create or bootstrap an administrator account.
3. Open Settings as admin and fill in lab name, administrator name, and administrator email.
4. Create tray records that match the physical tray layout in the lab.
5. Decide whether to import an existing workbook or start with manual entry.
6. Configure recovery emails for users who need password reset support.
7. Configure SMTP if you want reminder email delivery.
8. Verify that only one app instance is running the scheduler.
9. Run an export and store it as a known-good baseline.
10. Review the backup and recovery runbook before the system goes live.

!!! note
    The scheduler is designed for a single active app instance. If you run more than one app container with scheduling enabled, reminder and maintenance behavior can become ambiguous.

## First Login And Access Model

### Accounts

The application uses named user accounts with passwords.

- Log in with a username and password.
- Registration is available through the register page.
- Password reset is email-based when the user has a recovery email on file and outbound mail is configured.
- Administrative tools are restricted to the `admin` account.

### Password Reset Reality

The password reset flow does not use a shared master password. It sends a reset link to the recovery email associated with the account when mail delivery is configured.

If mail is not configured, password reset requests will not deliver messages even though the request page still responds safely.

## Navigation Map

The main navigation reflects the core operator loop.

| Area | What it is for |
| --- | --- |
| Home | Dashboard, urgent work, recent activity, tray heatmaps, weekly signals |
| Flipper | Rapid maintenance entry for stocks and crosses, with optional scanner input |
| Schedule | Upcoming flip calendar with per-day items and tray grouping |
| Stocks | Filter, review, update, label, and bulk-manage stock lines |
| Crosses | Create and maintain crosses derived from existing stocks |
| Trays | Build and manage physical tray layouts and positions |
| Tools | User guide, logout, and admin-only workbook and settings actions |

## Daily Operating Loop

Most labs will spend the majority of their time in this loop:

1. Open Home and review overdue items, due-today items, and the personal work queue.
2. Open Schedule to see which trays and lines are due in the next few days.
3. Open Flipper and record completed flips as you work through the bench.
4. Use Stocks, Crosses, and Trays to clean up records after physical work is complete.
5. Generate labels for new or relabeled lines before returning vials to storage.

### How Attention Is Calculated

The dashboard treats these as attention states:

- lines due today
- overdue lines
- lines marked `Showing Issues`
- lines marked `Needs refresh`

Archived items with status `No longer maintained` are excluded from active working views by default.

## Home Dashboard

The Home page is not a passive summary. It is the operator control surface.

It includes:

- high-level counts for active stocks, active crosses, and total lines
- attention cards for overdue, due-today, and review-needed items
- queue suggestions that prioritize urgent or soon-due work
- tray heatmaps showing occupancy, blocked cells, and alert cells
- a seven-day schedule preview
- recent activity and weekly trend signals

Use Home as the first screen at the start of a maintenance session.

## Stocks

### Stock Explorer

Stock Explorer is the primary working list for stock lines.

It supports:

- filtering by type, tray, status, food type, provenance, and species
- fuzzy search across IDs, genotype text, name, tray location, and comments
- bulk selection workflows
- label generation for selected items
- bulk flip, bulk status change, and bulk tray removal

By default, archived records with status `No longer maintained` are hidden unless you explicitly filter for them.

### Adding A Stock

You can create a stock in multiple ways:

- manual entry
- prefilled from an internal stock
- prefilled from external stock data when a valid source and source ID are available

Typical fields include:

- source ID
- genotype
- descriptive name
- type
- food type
- tray location
- vial lifetime
- flip frequency
- developmental time
- species
- provenance
- comments

### Working Advice

- Use a current naming convention before large imports or new line creation.
- Set tray information only when the physical vial has a confirmed home.
- Use `No longer maintained` instead of immediately deleting records when you want to preserve history.

### Deletion Behavior

Permanent deletion is intentionally constrained. Records are expected to be archived first by setting status to `No longer maintained`. That reduces accidental loss during normal operations.

## Crosses

### Cross Explorer

Cross Explorer parallels Stock Explorer but is tuned for crosses.

It supports:

- filtering by male species, female species, tray, status, and food type
- fuzzy search across name, tray data, and comments
- bulk selection and bulk operations
- label generation for selected crosses

### Creating A Cross

Cross creation is built around existing stock records.

Operators provide:

- male parent stock
- female parent stock
- cross name
- maintenance parameters such as vial lifetime and flip frequency
- tray placement and comments when needed

The app predicts offspring genotype information from the selected parents so the operator can review the expected line before saving.

### Scanner Support During Cross Creation

The cross form can use supported scanner input to identify parent stocks more quickly. This is useful when working from printed QR labels instead of manual UID entry.

## Trays

Tray records represent real storage surfaces in the lab.

### Tray Management

Use Tray Management to:

- create trays with an ID, name, row count, and column count
- view a tray as a position-based grid
- edit tray definitions
- delete empty trays

### Occupancy Rules

Tray views account for:

- occupied positions
- blocked positions required by multi-vial items
- empty positions

This means tray usage reflects more than the number of named items. It includes blocked space consumed by vial requirements.

### Moving Items

Tray workflows let operators:

- place a stock or cross into a tray position
- move an item to a new position
- remove an item from tray placement

Items marked `No longer maintained` are excluded from the standard tray placement workflow.

## Flipper And Schedule

### Flipper

The Flipper is the high-speed maintenance entry surface.

It supports:

- manual UID entry
- scanner-assisted UID lookup
- per-item status updates during flip entry
- optional comments attached to the flip event
- bulk flipping for selected explorer items

The application also guards against accidental rapid re-flips by checking the last recorded flip time.

### Schedule

The Schedule page groups upcoming work by day.

Each scheduled day can show:

- items due to flip
- associated trays for the day
- direct label generation for that day

Use the schedule when planning bench work by tray, not just by individual line.

### Reminder Emails

Daily reminder emails are optional.

- If SMTP is configured and a user has a recovery email, reminders can be sent.
- If SMTP is not configured, reminder delivery is skipped cleanly instead of failing the scheduler.

## Labels And Scanners

### Label Generation

Labels can be generated from:

- selected stocks in Stock Explorer
- selected crosses in Cross Explorer
- a specific day in the Schedule page

Generated labels include QR content for scanner-based workflows.

### Blank Spaces

Label workflows support adding blank spaces so the printed sheet can align with partially used label paper.

### Scanner Workflow

Supported scanner-based flows typically look like this:

1. Connect the scanner.
2. Open Flipper or a scanner-enabled form.
3. Select the available port.
4. Start scanning.
5. Scan the QR label.
6. Confirm the resolved item and complete the action.

If no port appears, confirm that the scanner is attached to the host seen by the app and that the deployment environment exposes the device correctly.

## Workbook Import And Export

Workbook actions are admin-only.

### Export

Use export when you want:

- a backup-like workbook snapshot
- a template for clean imports
- a structured handoff for data cleanup outside the app

### Import

Use import when you want to replace or update records in bulk from an `.xlsx` workbook.

Best practice:

1. Export a fresh workbook first.
2. Make edits against that workbook structure.
3. Validate IDs and sheet layout before upload.
4. Import during a controlled admin session.

!!! warning
    Workbook import is a privileged administrative action. Treat it like a controlled data migration, not a routine operator action.

## Admin Settings And Maintenance

The admin settings page manages:

- theme behavior and accent color
- displayed lab information
- manual FlyBase release-aware dataset refresh
- manual legacy Bloomington compatibility refresh
- manual legacy Bloomington gene metadata refresh
- manual FlyBase gene metadata refresh

FlyBase is the primary external stock catalog. The FlyBase release sync action redownloads the current dataset bundle, updates local inspection outputs, and refreshes stock-derived gene metadata. The legacy Bloomington refresh workflow remains available as a compatibility fallback during the BDSC migration, and it stores backup material under the repository data area.

FlyBase release refreshes also run automatically on the first day of each month at 1:30 AM. The legacy Bloomington compatibility refresh remains scheduled at 2:00 AM.

Admin settings and the home dashboard now flag FlyBase sync health directly from the local manifest, including stale or failed refresh attempts.

## Launch And Handoff Checklist

Before calling the deployment complete, verify all of the following:

- Operators can log in and stay logged in across page changes.
- The Home page loads without missing statistics or tray data.
- At least one stock, one cross, and one tray can be created successfully.
- Flipper accepts manual UID entry.
- Schedule renders upcoming work.
- Label generation produces a PDF.
- Admin export succeeds.
- Import has been tested in a non-production copy or with a known-safe workbook.
- Backup scripts and restore steps have been reviewed by the deployment owner.
- SMTP behavior is known: either working or intentionally disabled.

## Troubleshooting

### A UID Does Not Resolve In Flipper

- Confirm the item belongs to the logged-in user.
- Confirm the QR label or typed UID matches the saved `UniqueID`.
- Confirm the record was not permanently deleted.

### A Tray Position Will Not Accept An Item

- The target position may already be occupied.
- The target may be blocked by a multi-vial item.
- The line may require more contiguous space than is available.
- The line may already be archived.

### Password Reset Emails Never Arrive

- Confirm SMTP is configured.
- Confirm the user account has a recovery email.
- Confirm mail delivery from the host is allowed.

### Reminder Emails Never Arrive

- Confirm the scheduler is enabled on exactly one app instance.
- Confirm SMTP is configured.
- Confirm the user has an email on file.

### The Deployment Looks Healthy But Users Still Report Problems

Check the operational runbooks:

- [Deployment Guide](https://github.com/neurorishika/FlyManager/blob/main/DEPLOYMENT.md)
- [Platform Deployment Notes](https://github.com/neurorishika/FlyManager/blob/main/PLATFORM_DEPLOYMENT.md)
- [Backup And Recovery](https://github.com/neurorishika/FlyManager/blob/main/BACKUP_AND_RECOVERY.md)

For container deployments, also verify the documented health endpoints:

- `/health`
- `/health/ready`

## Related References

- [Home](index.md)
- [Deployment Guide](https://github.com/neurorishika/FlyManager/blob/main/DEPLOYMENT.md)
- [Platform Deployment Notes](https://github.com/neurorishika/FlyManager/blob/main/PLATFORM_DEPLOYMENT.md)
- [Backup And Recovery](https://github.com/neurorishika/FlyManager/blob/main/BACKUP_AND_RECOVERY.md)
