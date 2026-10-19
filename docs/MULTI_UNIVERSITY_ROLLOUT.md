# UniCart multi-university rollout

## Commercial-edition behavior

The commercial edition supports these campus choices:

- `pau` — Pan-Atlantic University
- `augustine` — Augustine University
- `babcock` — Babcock University

Registration and login use the student's UniCart account email and password. After each successful login, the app shows the campus selector. Selecting a campus stores that choice on the account and opens that campus's marketplace.

**Campus selection is not student verification.** The current commercial flow does not require a school email, matriculation number, student ID upload, or manual approval. Users can select any of the supported campuses. Do not describe the marketplaces as student-verified or student-only unless a reliable verification method is added later.

## Data isolation

Each lobby has a `university_code`. Lobby discovery, lobby history, user payment history, manual payment verification, item-payment initialization, and campus-admin queries are scoped to the selected university. Payment callbacks and authenticated Flutterwave webhooks continue to resolve transactions by their unique server-created reference, so they can complete a payment even if the user changes campus before returning to the app.

The current founder admin role can administer the selected campus. If campus-specific administrators are introduced, assign explicit per-campus admin permissions before issuing those accounts.

## Database migration

Before deploying this branch to a database that already contains UniCart tables, take a database backup and run the idempotent migration once:

```bash
python -m scripts.migrate_multischool
```

The script creates missing tables on a fresh database and adds the `university_code` columns if they are absent. Existing users and lobbies are assigned to PAU when the columns are first added, preserving the existing marketplace. New registrations explicitly start with no selected campus until the user chooses one.

For Render deployments where no shell is available, the script can be run temporarily as the service Start Command, then the normal Uvicorn Start Command must be restored immediately after the migration succeeds. Do this only after confirming the correct database is attached and a backup exists. Do not leave the migration script as the permanent Start Command.

## Deployment boundary

Develop and test this branch separately. Do not deploy it until the migration has been run against the intended database and the frontend/backend tests pass. The PAU final-year-project snapshot branch remains separate and should not receive commercial-edition changes.
