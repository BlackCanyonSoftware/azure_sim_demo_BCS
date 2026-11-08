"""The check before an answer is shown: every brief must pass these rules deterministically (the app asks the model again when it does not).

(a) the spaces' floor areas add up to the area asked for within 10% — and an outdoor space (courtyard, garden) is listed apart,
    flagged floors="outdoor", and is NOT in that total;
(b) an option whose idea names an open side of its courtyard may name one only if the volumes really leave that side open
    (y is north, x is east; it is computed from the boxes, and a fully enclosed courtyard may not be called open at all);
(c) every volume in an option touches at least one other, and no two overlap (sharing a face or an edge counts as touching);
(d) heights are whole storeys of about 3.5 m;
(e) an option whose name or idea speaks of a courtyard (court, patio, garden between, U-shape, ring) must really leave
    an empty area inside the volumes' combined outline — grid the footprint by the boxes' own coordinate lines, flood-fill
    from the outside, whatever empty cells the flood cannot reach are inside; if the text says enclosed, that area must
    touch none of the outline's sides.
"""
import math
import re

STOREY = 3.5  # metres per storey — the prompt's rule

# the words for the four sides; a named side counts only when the idea text really has one
SIDES = {"north": (0, 1), "east": (1, 0), "south": (0, -1), "west": (-1, 0)}

# words that mean an open space; a space spelled like one is treated as outdoor
OUTDOOR_NAME = re.compile(r"courtyard|garden|terrace|plaza|green", re.I)
# words in an option's name/idea that promise the volumes leave an open area (rule e)
E_WORDS = re.compile(r"courtyard|court\b|patio|garden between|u-?shape|ring", re.I)
E_ENCLOSED = re.compile(r"enclos", re.I)
# the area the architect asked for: "about 140 m2", "135 sqm", "roughly 140 square metres"
ASKED = re.compile(r"(\d+(?:\.\d+)?)\s*(?:m2|m²|sqm|sq m|square met(?:re|er)s?)\b", re.I)


def _boxes(volumes):
    out = []
    for v in volumes or []:
        try:
            x, y, w, d, h = (float(v[k]) for k in ("x", "y", "w", "d", "h"))
        except (KeyError, TypeError, ValueError):
            return None
        out.append((x, y, x + w, y + d, h, v))
    return out


def _touch(a, b):
    """True when two boxes share a face or an edge: their footprints touch (flush line) without overlapping."""
    x1, y1, x2, y2, _, _ = a
    x3, y3, x4, y4, _, _ = b
    atol = 0.05
    # an x-face meeting must span some of y (face) or meet at exactly one y-line (edge)
    x_meet = math.isclose(x2, x3, abs_tol=atol) or math.isclose(x4, x1, abs_tol=atol)
    y_meet = math.isclose(y2, y3, abs_tol=atol) or math.isclose(y4, y1, abs_tol=atol) \
        or math.isclose(y1, y3, abs_tol=atol) or math.isclose(y2, y4, abs_tol=atol)
    span_y = min(y2, y4) - max(y1, y3)
    span_x = min(x2, x4) - max(x1, x3)
    face_x = x_meet and span_y >= -atol      # faces along x meet and the footprints touch along y
    face_y = y_meet and span_x >= -atol      # faces along y meet and the footprints touch along x
    return bool(face_x or face_y)


def _overlap(a, b):
    x1, y1, x2, y2, _, _ = a
    x3, y3, x4, y4, _, _ = b
    ex = min(x2, x4) - max(x1, x3)
    ey = min(y2, y4) - max(y1, y3)
    return ex > 0.05 and ey > 0.05


def _names_side(text):
    m = re.search(r"\b(north|east|south|west|northern|eastern|southern|western)\b", str(text or ""), re.I)
    if not m:
        return None
    word = m.group(1).lower()
    for side in SIDES:  # "northern" -> "north"
        if word == side or word.startswith(side):
            return side
    return word[:4]


def _open_sides(boxes):
    """For an arrangement that wraps around an inner space: which sides of it the boxes leave open, computed from the footprints.
    y is north, x is east. Grid the arrangement's bounding region by the boxes' exact coordinate lines; the inner space
    (courtyard) is the empty cell(s). A side is open when nothing occupies the region beyond that side of the inner
    space (the inner space reaches the edge of the arrangement that way), covered when a box does. A pocket walled on
    all four sides is fully enclosed (no open side); an L corner is open on the sides it does not wall."""
    if len(boxes) < 2:
        return []
    atol = 0.05
    xs = sorted({b[0] for b in boxes} | {b[2] for b in boxes})
    ys = sorted({b[1] for b in boxes} | {b[3] for b in boxes})
    cols = list(zip(xs[:-1], xs[1:])); rows = list(zip(ys[:-1], ys[1:]))
    nx, ny = len(cols), len(rows)
    if nx < 2 or ny < 2:
        return []
    def occupied(i, j):  # is this cell's centre strictly inside some box's footprint?
        cx, cy = (cols[i][0] + cols[i][1]) / 2, (rows[j][0] + rows[j][1]) / 2
        return any(b[0] - atol < cx < b[2] + atol and b[1] - atol < cy < b[3] + atol for b in boxes)
    empty = {(i, j) for i in range(nx) for j in range(ny) if not occupied(i, j)}
    # the inner space must be bounded by at least one box (a field of open ground is not a courtyard)
    if not any((i + di, j + dj) not in empty for (i, j) in empty for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)) if 0 <= i + di < nx and 0 <= j + dj < ny):
        return []
    open_sides = set()
    for (i, j) in empty:
        for side, (di, dj) in (("east", (1, 0)), ("west", (-1, 0)), ("north", (0, 1)), ("south", (0, -1))):
            n = (i + di, j + dj)
            if n in empty:
                continue  # part of the same inner space — not a side of it
            if not (0 <= n[0] < nx and 0 <= n[1] < ny):
                open_sides.add(side)  # flush with the arrangement's edge: nothing beyond, so it is open that way
            # a box cell next to it covers that side
    return sorted(open_sides)


def _inside_cells(boxes):
    """Grid the volumes' combined outline (bounding region) by the boxes' own coordinate lines (x east, y north), and
    flood-fill from any empty cell that touches at least one volume — that flood is the open area the volumes really
    leave (the same criterion as the FreeCAD tool's layout open_spaces: a field of empty ground nothing bounds is not
    a courtyard). Returns the set of cells it reaches; empty means the promise of an open area is empty. Cells on the
    region's edge are open to a side; an enclosed area must contain none of them."""
    if len(boxes) < 2:
        return set()
    atol = 0.05
    xs = sorted({b[0] for b in boxes} | {b[2] for b in boxes})
    ys = sorted({b[1] for b in boxes} | {b[3] for b in boxes})
    cols = list(zip(xs[:-1], xs[1:])); rows = list(zip(ys[:-1], ys[1:]))
    nx, ny = len(cols), len(rows)
    if nx < 1 or ny < 1:
        return set()
    def filled(i, j):  # is this cell's centre strictly inside some box's footprint?
        cx, cy = (cols[i][0] + cols[i][1]) / 2, (rows[j][0] + rows[j][1]) / 2
        return any(b[0] - atol < cx < b[2] + atol and b[1] - atol < cy < b[3] + atol for b in boxes)
    def touches_volume(i, j):
        for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (i + di, j + dj)
            if 0 <= n[0] < nx and 0 <= n[1] < ny and filled(*n):
                return True
        return False
    # seeds: empty cells next to a volume; the flood spreads through empty cells from there
    flood = {(i, j) for i in range(nx) for j in range(ny) if not filled(i, j) and touches_volume(i, j)}
    stack = list(flood)
    while stack:
        i, j = stack.pop()
        for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (i + di, j + dj)
            if 0 <= n[0] < nx and 0 <= n[1] < ny and not filled(*n) and n not in flood:
                flood.add(n); stack.append(n)
    return flood


def check(answer, prompt):
    """The rules a brief must pass before it may be shown. Returns a list of problems; empty list means good."""
    problems = []
    asked = None
    m = ASKED.search(prompt or "")
    if m:
        asked = float(m.group(1))

    spaces = answer.get("spaces")
    if not isinstance(spaces, list) or not spaces:
        problems.append("no spaces listed")
        spaces = []
    indoor, outdoor = 0.0, 0.0
    outdoor_listed = False
    for s in spaces:
        area = s.get("area_m2")
        if not isinstance(area, (int, float)) or area <= 0:
            problems.append(f"space '{s.get('name')}' has no area")
            continue
        name = str(s.get("name") or "")
        floors = str(s.get("floors") or "").lower()
        is_outdoor = floors == "outdoor" or (floors != "outdoor" and OUTDOOR_NAME.search(name))
        if is_outdoor and floors != "outdoor":
            problems.append(f"outdoor space '{name}' is not flagged floors='outdoor'")
        if is_outdoor:
            outdoor += area
            outdoor_listed = True
        else:
            indoor += area
    if asked and indoor and abs(indoor - asked) > 0.1 * asked:
        problems.append(f"indoor areas add to {indoor:g} m2, not within 10% of the {asked:g} m2 asked")
    if OUTDOOR_NAME.search(prompt or "") and not outdoor_listed:
        problems.append("the prompt asked for a courtyard/garden but the answer lists none — list it as its own space, flagged floors='outdoor', out of the total")

    for opt in answer.get("options") or []:
        boxes = _boxes(opt.get("volumes"))
        if not boxes:
            problems.append(f"option {opt.get('id')} has no usable volumes")
            continue
        if not (2 <= len(boxes) <= 4):
            problems.append(f"option {opt.get('id')} has {len(boxes)} volumes (the prompt wants 2 to 4)")
        for b in boxes:
            storeys = b[4] / STOREY
            if not (math.isclose(storeys, round(storeys), abs_tol=0.05) and round(storeys) >= 1):
                problems.append(f"option {opt.get('id')} height {b[4]:g} m is not a whole number of storeys of about {STOREY} m")
        for i in range(len(boxes)):
            if not any(_touch(boxes[i], boxes[j]) for j in range(len(boxes)) if j != i):
                problems.append(f"option {opt.get('id')}: volume '{boxes[i][5].get('name')}' touches no other volume")
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                if _overlap(boxes[i], boxes[j]):
                    problems.append(f"option {opt.get('id')}: volumes overlap")
        named = _names_side(opt.get("idea"))
        if named and named not in _open_sides(boxes):
            problems.append(f"option {opt.get('id')}: idea names an open {named} side but the volumes do not leave that side open")
        text = f"{opt.get('name') or ''} {opt.get('idea') or ''}"
        if E_WORDS.search(text):
            area = _inside_cells(boxes)
            if not area:
                problems.append(f"option {opt.get('id')}: the name/idea speaks of a courtyard, but the volumes leave no empty area inside their combined outline")
            elif E_ENCLOSED.search(text):
                xs = sorted({b[0] for b in boxes} | {b[2] for b in boxes})
                ys = sorted({b[1] for b in boxes} | {b[3] for b in boxes})
                nx, ny = len(xs) - 1, len(ys) - 1
                if any(i in (0, nx - 1) or j in (0, ny - 1) for (i, j) in area):
                    problems.append(f"option {opt.get('id')}: the text says enclosed but the open area reaches the outline's edge")
    return problems


def fix_note(problems):
    """The extra words to send with the instructions so the model can fix exactly these problems."""
    return ("Your last answer broke these rules — fix them and answer again in the same JSON shape, changing nothing else:"
            + "".join(f"\n- {p}" for p in problems))
