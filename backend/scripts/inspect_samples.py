"""Print the structure of the ODRS and NDP sample records."""

import json
from pathlib import Path

RES = Path(__file__).resolve().parents[2] / "ai" / "resources"


def main() -> None:
    odrs = json.load(open(RES / "BayesExport_20250729.json"))["opportunities"][0]
    print("=== ODRS opportunity (trimmed) ===")
    for k in ["id", "title", "description", "organization", "last_verified_at", "updated_at", "unavailable_at"]:
        print(f"{k}: {json.dumps(odrs.get(k))[:200]}")
    loc0 = (odrs.get("locations") or [{}])[0]
    print("location[0] keys:", list(loc0.keys()))
    print("location[0].address:", json.dumps(loc0.get("address"))[:300])
    print("phones:", json.dumps(odrs.get("phones"))[:200])

    ndp = json.load(open(RES / "national-data-platform-sample-1.json"))["data"][0]
    print("\n=== NDP record (trimmed) ===")
    for k in ["id", "name", "description", "url", "email"]:
        print(f"{k}: {json.dumps(ndp.get(k))[:200]}")
    svc0 = (ndp.get("services") or [{}])[0]
    print("services[0] keys:", list(svc0.keys()))
    print("services[0].name:", json.dumps(svc0.get("name"))[:120])
    print("services[0].status:", json.dumps(svc0.get("status"))[:120])
    loc = (ndp.get("locations") or [{}])[0]
    print("locations[0] keys:", list(loc.keys()))
    print("locations[0]:", json.dumps(loc)[:500])


if __name__ == "__main__":
    main()
