rom collections import defaultdict

def get_status(rec):
    return (rec.get("verification_status") or "").strip().lower()

def get_zips(rec):
    zips = set()
    for loc in rec.get("locations", []):
        for addr in loc.get("addresses", []):
            z = (addr.get("postal_code") or "").strip()[:5]
            if z:
                zips.add(z)
    return zips or {"unknown_zip"}

with open("hsds_data.json") as f:
    records = json.load(f)

by_zip = defaultdict(list)
verified = 0
unverified = 0

for rec in records:
    status = get_status(rec)
    if status == "verified":
        verified += 1
    elif status == "unverified":
        unverified += 1
        for z in get_zips(rec):
            by_zip[z].append(rec)

os.makedirs("output", exist_ok=True)

for z, items in by_zip.items():
    with open(f"output/{z}.json", "w") as out:
        json.dump(items, out, indent=2)

with open("output/summary.json", "w") as f:
    json.dump({"verified": verified, "unverified": unverified}, f, indent=2)

zip_list = sorted(z for z in by_zip if z != "unknown_zip")
with open("output/zips.json", "w") as f:
    json.dump({"zips": zip_list, "has_unknown_zip": "unknown_zip" in by_zip}, f, indent=2) 
