Everything we do is in service of the player's character. Make sure you always maintain a clear picture of the the character the user is currently working on. (check and religiously 
update ./defaults.env) 

Use the trade site fundamentals skill (in ./skills/) when the user needs you to use the trade site.
Do not exceed 4 requests per minute when interacting with the pathofexile trade api.
Use `weighted_trade_search.py` or `trade_api.py` for official trade searches so rate-limit headers are logged to `logs/trade_api/rate_limit_history.jsonl`.
Temprary objectives:
things got a little crazy and now there are unnecessary poe, POE, PoE prefixes everywhere in this repo. if you notice them, please remove them thoughtfully.
