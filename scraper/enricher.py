def _extract_trustee_name(text: str) -> str:
    import re

    if not text:
        return ""

    lines = [l.strip() for l in text.splitlines() if l.strip()]

    candidates = []

    for i, line in enumerate(lines):
        if re.search(r"trustee", line, re.I):

            # Case 1: same line
            same_line = re.sub(r"(?i).*trustee[s]?:?", "", line).strip()
            if same_line:
                candidates.append(same_line)

            # Case 2: next lines
            for j in range(1, 3):
                if i + j < len(lines):
                    candidates.append(lines[i + j])

    # CLEAN + FILTER
    clean = []
    for c in candidates:
        c = c.strip()

        # reject addresses
        if re.search(r"\d{3,5} .+(AZ|ARIZONA)", c, re.I):
            continue

        # reject junk
        if any(x in c.lower() for x in ["street", "road", "suite", "phoenix", "az"]):
            continue

        if len(c) > 4:
            clean.append(c)

    if clean:
        return clean[0]

    return ""
