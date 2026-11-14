[Roomba+](../../README.md) › [Documentation](../README.md) › The words on your screen come from iRobot

# The words on your screen come from iRobot

Why error texts, part names and units read as they do in the iRobot apps.

**Applies to:** all robots

This integration wrote its own error labels for years. A comparison against the vendor's own app
found that **two of 126 matched**, and the differences were not stylistic:

| ours | iRobot |
|---|---|
| Charging error | Charging Issue: unable to charge |
| No charge current | Charging Issue: contacts need to be cleaned |
| Right wheel error | Right wheel sensor issue |

The second column tells somebody what to do. The first tells them something is wrong, which they
already knew from the robot stopping.

**Each robot reads its own app**: a Classic robot the Classic app's texts, from iRobot Home
7.18, with the wording the app uses for a Roomba 69x, an Essential or a robot on a Clean Base, and
a Prime robot the Prime app's, both in all eight supported languages, through roombapy-prime. The two
apps give some numbers different meanings, so one table for both could not be right. The other app's
text answers only where the robot's own has none.

**Our own texts remain where they say more than the app**: the battery and charging errors
(101–121), where the Classic app shows one line for all of them. Each is checked against iRobot's
help article for the code. A code no source has a text for (error 236 has turned up on a real
robot) shows as its number rather than an invented name.

**iRobot's article for the error** is one tap away: `help_url`, from iRobot's help catalogue for the
model, on the error sensors (*Status – Error* while it shows the error, *Error – Last code*), on
the vacuum, on the readiness sensor for a refusal to start, in the maintenance list and event for
an entry an error brought, and as *Learn more* on a repair issue an error brings.

Part names follow the same rule: **"Maintenance – High-Efficiency Filter"** keeps this integration's
grouping and takes iRobot's word for the part. So do the maintenance units: `evacs` reads "Dust
Collection Left" in the app, so it does here too, rather than the obvious translation of the field
name.

The point is not tidiness. Somebody comparing the two screens should see the same words; a list
saying "60 evacuations remaining" beside an app saying "Dust Collection Left" reads as two different
measurements of two different things.

---

[Documentation home](../README.md) · [Troubleshooting](../TROUBLESHOOTING.md) · [Questions and reports](https://github.com/johnnyh1975/ha_roomba_plus/issues)
