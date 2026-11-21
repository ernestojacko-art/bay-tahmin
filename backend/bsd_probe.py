import json
from datetime import datetime, timezone

import accuracy
import bsd_profile


async def run_once() -> None:
    try:
        out = await bsd_profile.profile("Trabzonspor", "Eyüpspor", "2026-11-22T16:00:00+00:00")
    except Exception as exc:  # noqa: BLE001
        out = {"hata": str(exc)[:300]}
    await accuracy._db("POST", "sportoto_bulletin", params={"on_conflict": "id"}, prefer=True, json_body={
        "id": "bsd_diag", "season": "", "week": "", "matches": json.loads(json.dumps(out, default=str)), "source": "bsd",
        "updated_at": datetime.now(timezone.utc).isoformat()})
