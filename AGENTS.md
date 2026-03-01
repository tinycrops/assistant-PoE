Everything we do is in service of the player's character. Make sure you always maintain a clear picture of the the character the user is currently working on. (check and religiously 
update ./defaults.env) 

Use the trade site fundamentals skill (in ./skills/) when the user needs you to use the trade site.
Default trade links to instant buyout using `status.option = "securable"`. Only use `online` when the user explicitly asks for in-person whisper trades.

Headless Path of Building calculations must inform progression, gearing, and trade recommendations. 
Refresh with `poe_stat_watch.py` first when snapshot-derived stats could affect the decision, and treat `characters/<character-slug>/ledger.json -> latest_snapshot` as stale unless it was refreshed recently enough for the task.
Save every new stat-watch snapshot as an immutable archived artifact for the active character so ledger visualizations can validate progression over time.


Temprary objectives:
things got a little crazy and now there are unnecessary poe, POE, PoE prefixes everywhere in this repo. if you notice them, please remove them thoughtfully.

Going forward our objective is to construct an ecosystem/living organism around/with the character data we extract. 

The player character is not just a list of stats, it is a report card of the user's understanding and mastery in the game.
