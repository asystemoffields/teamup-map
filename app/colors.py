"""Teamup's fixed 48-color sub-calendar palette: color id -> hex.

The sub-calendar API returns `color` as an integer 1-48 (no hex), so we resolve
it here to the real hex value the map should use. Source: the official Teamup
API "Colors" reference (apidocs.teamup.com -> Colors), captured verbatim.
"""

TEAMUP_COLORS = {
    1: "#f2665b",  2: "#cf2424",  3: "#a01a1a",  4: "#7e3838",
    5: "#ca7609",  6: "#f16c20",  7: "#f58a4b",  8: "#d2b53b",
    9: "#d96fbf", 10: "#b84e9d", 11: "#9d3283", 12: "#7a0f60",
    13: "#542382", 14: "#7742a9", 15: "#8763ca", 16: "#b586e2",
    17: "#668cb3", 18: "#4770d8", 19: "#2951b9", 20: "#133897",
    21: "#1a5173", 22: "#1a699c", 23: "#0080a6", 24: "#4aaace",
    25: "#88b347", 26: "#5a8121", 27: "#2d850e", 28: "#176413",
    29: "#0f4c30", 30: "#386651", 31: "#00855b", 32: "#4fb5a1",
    33: "#553711", 34: "#724f22", 35: "#9c6013", 36: "#f6c811",
    37: "#ce1212", 38: "#b20d47", 39: "#d8135a", 40: "#e81f78",
    41: "#f5699a", 42: "#5c1c1c", 43: "#a55757", 44: "#c37070",
    45: "#000000", 46: "#383838", 47: "#757575", 48: "#a3a3a3",
}


def resolve_color(value):
    """Return a '#hex' string for a Teamup color value.

    Accepts a Teamup color id (int or numeric str, 1-48) or an already-hex
    string (passed through). Returns None if it can't be resolved.
    """
    if value is None:
        return None
    if isinstance(value, str):
        v = value.strip()
        if v.startswith("#"):
            return v
        if v.isdigit():
            value = int(v)
        else:
            return None
    if isinstance(value, int):
        return TEAMUP_COLORS.get(value)
    return None
