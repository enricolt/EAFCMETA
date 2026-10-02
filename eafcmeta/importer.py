"""Import da CSV. Colonne: name,version,position,price,is_sbc,body_type,weak_foot,skill_moves,playstyles(;),<stat>..."""
import csv
import json
import sys

from . import db, scoring


def import_csv(path: str, conn) -> int:
    cfg = scoring.load_config()
    stat_keys = {k for w in cfg["role_weights"].values() for k in w}
    n = 0
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["position"] not in cfg["position_to_role"]:
                raise ValueError(f"posizione non supportata: {row['position']} ({row['name']})")
            data = {
                "stats": {k: int(row[k]) for k in stat_keys if row.get(k)},
                "playstyles": [p.strip() for p in row.get("playstyles", "").split(";") if p.strip()],
                "body_type": row.get("body_type") or "Average",
                "weak_foot": int(row.get("weak_foot") or 3),
                "skill_moves": int(row.get("skill_moves") or 3),
            }
            conn.execute("INSERT INTO cards (name, version, position, price, is_sbc, data) VALUES (?,?,?,?,?,?)",
                         (row["name"], row.get("version", ""), row["position"], int(row["price"]),
                          int(row.get("is_sbc") in ("1", "true", "True")), json.dumps(data)))
            n += 1
    conn.commit()
    return n


if __name__ == "__main__":
    print(import_csv(sys.argv[1], db.connect()), "carte importate")
