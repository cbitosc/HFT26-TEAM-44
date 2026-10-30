"""Danger score, level, recommended response and message text. All numbers come from config.yaml."""


def is_night(dt, cfg=None):
    return dt.hour < 6 or dt.hour >= 19


def assess(species, count, night, zone, person, cfg):
    r = cfg["risk"]
    weight = float(r["species_weight"].get(species, r["default_species_weight"]))
    count = max(1, int(count))              # a species vote always means at least one animal
    nf = float(r["night_factor"]) if night else 1.0
    score = round(weight * count * nf * float(zone), 2)
    lv = r["levels"]
    level = "CRITICAL" if score >= lv["CRITICAL"] else "HIGH" if score >= lv["HIGH"] else "MEDIUM" if score >= lv["MEDIUM"] else "LOW"
    if person and level in ("LOW", "MEDIUM"):
        level = "HIGH"
    return {"weight": weight, "count": count, "night_factor": nf, "zone": float(zone), "score": score,
            "level": level, "response": r["response"][level]}


PLURALS = {"deer": "deer", "bird": "birds", "person": "people", "bobcat": "bobcats", "opossum": "opossums"}


def plural(species, count):
    if count == 1:
        return species
    return PLURALS.get(species, species + "s")


def title(a):
    if a["person"]:
        return "Person detected · possible poacher"
    return f"{a['count']} {plural(a['species'], a['count'])} near {a['village']}"


def template_message(a):
    when = a["captured_at"][11:16]
    if a["person"]:
        return (f"Possible poacher: a person was detected at Cam {a['camera_id']} ({a['village']}) at {when}"
                f"{' in IR night mode' if a['night'] else ''}. Send {a['response'].lower()} and approach with caution.")
    review = " This was a faint detection and needs a quick human check." if a.get("review") else ""
    return (f"Alert for {a['village']}: at least {a['count']} {plural(a['species'], a['count'])} seen at Cam {a['camera_id']} "
            f"at {when}{' (night)' if a['night'] else ''}. Risk is {a['level']}. Recommended: {a['response'].lower()}.{review}")
