# Roomba+ — Moving to Roomba+, and moving robots

[← Roomba+](../README.md)

- [From the built-in Roomba integration](#from-the-built-in-roomba-integration)
- [From roomba_rest980](#from-roomba_rest980)
- [More than one robot](#more-than-one-robot)
- [Replacing or selling a robot](#replacing-or-selling-a-robot)
- [Moving Home Assistant to new hardware](#moving-home-assistant-to-new-hardware)

---

## From the built-in Roomba integration

Roomba+ and Home Assistant's built-in *iRobot Roomba and Braava* integration
cannot run side by side: a robot accepts one local connection at a time, and
the two need different versions of the `roombapy` library.

1. Settings → Devices & services → iRobot Roomba and Braava → ⋮ → **Delete**.
2. Restart Home Assistant.
3. Install Roomba+ (see [Installation](../README.md#installation)) and set it up.

Even after you delete it, the built-in integration keeps finding your robot:
a **Discovered** Roomba card appears that does not open (`Invalid handler
specified`) and cannot be ignored, and the log repeats
`Error occurred loading flow for integration roomba`. Both are harmless. What
you can do about them, including a workaround that removes the card:
[Troubleshooting](TROUBLESHOOTING.md#error-occurred-loading-flow-for-integration-roomba-in-your-log-or-a-roomba-card-you-cannot-dismiss).

---

## From roomba_rest980

> ⚠️ **"rest980" means two things here, and they need opposite treatment.**
> The **integration** stays installed — that is where your room names come
> from. The **server container** (often `rest980_j7`) has to be **stopped
> before you set Roomba+ up**, because a robot accepts exactly one local
> connection at a time and the server holds it on port 8883. Roomba+ then
> cannot connect at all, and nothing in the error says why.

**Assumed at the start:** roomba_rest980 is working, with its room entities
present in Home Assistant. Those entities exist because the container was
running the last time Home Assistant started, and the steps below keep them.

1. Install Roomba+ and **restart Home Assistant**, even if the Marketplace
   does not ask you to — but do not set it up yet.
2. **Stop the rest980 server container.** Leave the integration installed.
3. Set Roomba+ up. It connects to the robot directly.
4. Enter your iRobot account in the setup to get the cloud room names and
   favourites back.
5. Settings → Devices & services → Roomba+ → Configure → **Import rooms from
   roomba_rest980**. This entry appears only when roomba_rest980 is installed
   and the robot has a Smart Map. It reads the room names from its `select.*`
   entities and fills in the Roomba+ room names that are not set yet; a name
   you gave in Roomba+ is never overwritten. It takes the rooms of the map the
   robot is on; zones come from your iRobot account (step 4).
6. When you are happy with the result, remove the roomba_rest980 integration
   and delete the container.

> ⚠️ **Do not restart Home Assistant between steps 2 and 5.**
>
> roomba_rest980 builds its room entities during setup, from data it fetches
> from the server. With the container stopped, it fails to load and those
> entities never appear, so step 5 would find nothing to import. Once they
> exist they are safe until the next start. If you restart by accident, start
> the container again, restart Home Assistant, and pick up from step 2.

---

## More than one robot

Each robot is its own integration entry, with its own device, entities and
storage.

Set up the first robot with **Set up with my iRobot account**. The account's
other robots then appear under **Discovered** in Settings → Devices & services,
and each is added with one click; nothing needs to be entered again. Robots of
one account share one cloud login, and the Prime robots of one account share
one cloud connection.

A Classic robot Home Assistant finds on your network needs no press of the
HOME button when another robot of its iRobot account is already set up: its
password comes from the account. Setting a robot up without any iRobot account
works as before.

---

## Replacing or selling a robot

Roomba+ keeps what it has learned — mission history, room geometry, coverage,
maintenance timers, health trends — in Home Assistant, not on the robot.
Deleting the robot's entry deletes all of it.

1. **Back it up first**, if you may want it again: call
   `roomba_plus.create_backup` for the robot. It writes one ZIP with every store
   to `<config>/roomba_plus_backups/`. `roomba_plus.restore_backup` puts it back,
   without a restart.
2. **Remove the entry**: Settings → Devices & services → Roomba+ → ⋮ →
   **Delete**.
3. **Selling it?** Reset the robot in the **iRobot app** (robot → Settings →
   Factory reset). Roomba+ does not reset robots.

**A replacement robot** is a new entry and starts fresh. A backup belongs to the
robot it was made from; to carry only the **mission history** over to the new
robot, export it from the old entry before you delete it and import it into the
new one, through the [REST API](API.md) with a long-lived access token:

```bash
# before deleting the old entry
curl -H "Authorization: Bearer <token>" \
     "https://<ha>/api/roomba_plus/<old_entry_id>/mission_history?format=export" \
     -o roomba_history.json

# after setting up the new robot
curl -X POST -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
     -d @roomba_history.json \
     "https://<ha>/api/roomba_plus/<new_entry_id>/mission_history/import"
```

The entry id is in the address of the entry's page (Settings → Devices &
services → Roomba+). The import skips missions it already has, so running it
twice does no harm.

---

## Moving Home Assistant to new hardware

A Home Assistant backup carries everything Roomba+ stored. If you move without
one, call `roomba_plus.create_backup` for each robot before, and
`roomba_plus.restore_backup` after the move.
