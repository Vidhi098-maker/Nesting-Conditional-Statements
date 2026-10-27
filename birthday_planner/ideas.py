# Catalog of birthday ideas the planner picks from.
#
# Each idea has:
#   vibes     - which group moods it suits
#   settings  - where it is possible: "city", "suburb", "town", "rural"
#   needs     - a nearby natural feature it requires ("beach", "mountains",
#               "lake") or None if it works anywhere
#   outdoor   - True if the activity happens outside
#   cost      - 1 = cheap, 2 = moderate, 3 = splurge
#   max_group - the largest group it comfortably works for

ALL = ("city", "suburb", "town", "rural")
URBAN = ("city", "suburb")

IDEAS = [
    {"name": "Rooftop dinner with a view", "vibes": ("fancy", "chill"), "settings": ("city",), "needs": None, "outdoor": True, "cost": 3, "max_group": 12},
    {"name": "Escape room challenge", "vibes": ("adventurous", "party"), "settings": URBAN, "needs": None, "outdoor": False, "cost": 2, "max_group": 8},
    {"name": "Karaoke night in a private booth", "vibes": ("party",), "settings": URBAN, "needs": None, "outdoor": False, "cost": 2, "max_group": 15},
    {"name": "Chef's tasting menu", "vibes": ("fancy",), "settings": ("city",), "needs": None, "outdoor": False, "cost": 3, "max_group": 8},
    {"name": "Comedy club show", "vibes": ("party", "chill"), "settings": URBAN, "needs": None, "outdoor": False, "cost": 2, "max_group": 12},
    {"name": "Live concert or gig", "vibes": ("party", "adventurous"), "settings": ("city",), "needs": None, "outdoor": False, "cost": 3, "max_group": 20},
    {"name": "Museum or gallery day + cafe", "vibes": ("chill", "creative"), "settings": URBAN, "needs": None, "outdoor": False, "cost": 1, "max_group": 10},
    {"name": "Pottery or painting class", "vibes": ("creative", "chill"), "settings": ("city", "suburb", "town"), "needs": None, "outdoor": False, "cost": 2, "max_group": 12},
    {"name": "Cooking class together", "vibes": ("creative", "fancy"), "settings": URBAN, "needs": None, "outdoor": False, "cost": 2, "max_group": 10},
    {"name": "Bowling and arcade night", "vibes": ("party", "chill"), "settings": ("city", "suburb", "town"), "needs": None, "outdoor": False, "cost": 1, "max_group": 20},
    {"name": "Go-kart racing", "vibes": ("adventurous", "party"), "settings": ("suburb", "town"), "needs": None, "outdoor": True, "cost": 2, "max_group": 12},
    {"name": "Trampoline park", "vibes": ("adventurous", "party"), "settings": URBAN, "needs": None, "outdoor": False, "cost": 1, "max_group": 20},
    {"name": "Indoor rock climbing", "vibes": ("adventurous",), "settings": URBAN, "needs": None, "outdoor": False, "cost": 2, "max_group": 10},
    {"name": "Spa day", "vibes": ("chill", "fancy"), "settings": ("city", "suburb", "town"), "needs": None, "outdoor": False, "cost": 3, "max_group": 6},
    {"name": "Food market crawl", "vibes": ("adventurous", "chill"), "settings": ("city",), "needs": None, "outdoor": True, "cost": 1, "max_group": 10},
    {"name": "Themed house party", "vibes": ("party", "creative"), "settings": ALL, "needs": None, "outdoor": False, "cost": 1, "max_group": 40},
    {"name": "Backyard barbecue", "vibes": ("chill", "party"), "settings": ("suburb", "town", "rural"), "needs": None, "outdoor": True, "cost": 1, "max_group": 30},
    {"name": "Movie marathon sleepover", "vibes": ("chill",), "settings": ALL, "needs": None, "outdoor": False, "cost": 1, "max_group": 10},
    {"name": "Board game and pizza night", "vibes": ("chill", "creative"), "settings": ALL, "needs": None, "outdoor": False, "cost": 1, "max_group": 12},
    {"name": "Murder mystery dinner at home", "vibes": ("creative", "party", "fancy"), "settings": ALL, "needs": None, "outdoor": False, "cost": 1, "max_group": 12},
    {"name": "Picnic in the park", "vibes": ("chill",), "settings": ALL, "needs": None, "outdoor": True, "cost": 1, "max_group": 25},
    {"name": "Outdoor movie under the stars", "vibes": ("chill", "creative"), "settings": ("suburb", "town", "rural"), "needs": None, "outdoor": True, "cost": 1, "max_group": 20},
    {"name": "Bonfire and s'mores night", "vibes": ("chill", "party"), "settings": ("town", "rural"), "needs": None, "outdoor": True, "cost": 1, "max_group": 25},
    {"name": "Stargazing trip", "vibes": ("chill", "adventurous"), "settings": ("town", "rural"), "needs": None, "outdoor": True, "cost": 1, "max_group": 12},
    {"name": "Farm visit and fruit picking", "vibes": ("chill", "creative"), "settings": ("town", "rural"), "needs": None, "outdoor": True, "cost": 1, "max_group": 20},
    {"name": "Horse riding trail", "vibes": ("adventurous",), "settings": ("town", "rural"), "needs": None, "outdoor": True, "cost": 2, "max_group": 8},
    {"name": "Winery or brewery tour", "vibes": ("fancy", "chill"), "settings": ("town", "rural"), "needs": None, "outdoor": True, "cost": 2, "max_group": 12},
    {"name": "DIY craft party (tie-dye, candles)", "vibes": ("creative", "party"), "settings": ALL, "needs": None, "outdoor": False, "cost": 1, "max_group": 15},
    {"name": "Fancy dress-up dinner party", "vibes": ("fancy", "party"), "settings": ALL, "needs": None, "outdoor": False, "cost": 2, "max_group": 14},
    {"name": "Scavenger hunt around town", "vibes": ("adventurous", "creative", "party"), "settings": ALL, "needs": None, "outdoor": True, "cost": 1, "max_group": 20},
    {"name": "Beach day and sunset dinner", "vibes": ("chill", "party"), "settings": ALL, "needs": "beach", "outdoor": True, "cost": 1, "max_group": 30},
    {"name": "Surf or paddleboard lesson", "vibes": ("adventurous",), "settings": ALL, "needs": "beach", "outdoor": True, "cost": 2, "max_group": 8},
    {"name": "Sunset boat cruise", "vibes": ("fancy", "chill"), "settings": ALL, "needs": "beach", "outdoor": True, "cost": 3, "max_group": 15},
    {"name": "Mountain hike with a summit picnic", "vibes": ("adventurous", "chill"), "settings": ALL, "needs": "mountains", "outdoor": True, "cost": 1, "max_group": 12},
    {"name": "Cabin weekend getaway", "vibes": ("chill", "fancy"), "settings": ALL, "needs": "mountains", "outdoor": False, "cost": 3, "max_group": 10},
    {"name": "Ski or snowboard day", "vibes": ("adventurous",), "settings": ALL, "needs": "mountains", "outdoor": True, "cost": 3, "max_group": 10, "winter_only": True},
    {"name": "Kayaking on the lake", "vibes": ("adventurous", "chill"), "settings": ALL, "needs": "lake", "outdoor": True, "cost": 2, "max_group": 10},
    {"name": "Lakeside camping trip", "vibes": ("adventurous", "chill"), "settings": ALL, "needs": "lake", "outdoor": True, "cost": 1, "max_group": 15},
    {"name": "Ice skating and hot chocolate", "vibes": ("chill", "party"), "settings": ALL, "needs": None, "outdoor": True, "cost": 1, "max_group": 20, "winter_only": True},
]
