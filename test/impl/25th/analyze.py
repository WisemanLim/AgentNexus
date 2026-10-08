"""k6 --out json 결과 + 장애 주입 시각 → 구간별 가용성·복구 시간 (NFR-A1). 사용: python3 analyze.py points.json events.log"""
import json
import sys
from collections import defaultdict
from datetime import datetime

pts = defaultdict(lambda: [0, 0])  # 초 → [성공, 전체]
by_scn = defaultdict(lambda: [0, 0])
with open(sys.argv[1]) as f:
    for line in f:
        d = json.loads(line)
        if d.get("type") != "Point" or d.get("metric") != "checks":
            continue
        t = int(datetime.fromisoformat(d["data"]["time"].replace("Z", "+00:00")).timestamp())
        ok = d["data"]["value"] >= 1
        pts[t][0] += ok
        pts[t][1] += 1
        s = d["data"]["tags"].get("scenario", "?")
        by_scn[s][0] += ok
        by_scn[s][1] += 1
events = [(int(a), b) for a, b in (ln.strip().split(" ", 1) for ln in open(sys.argv[2]) if ln.strip())]
secs = sorted(pts)
total_ok = sum(v[0] for v in pts.values())
total = sum(v[1] for v in pts.values())
out = {"requests": total, "success": total_ok, "availability": round(total_ok / total, 5) if total else None,
       "by_scenario": {k: {"requests": v[1], "availability": round(v[0] / v[1], 5)} for k, v in by_scn.items()}, "faults": []}
for i, (t0, name) in enumerate(events):
    if name.startswith("end:"):
        continue
    t_end = next((t for t, n in events[i + 1:] if not n.startswith("end:")), secs[-1] + 1)
    win = [s for s in secs if t0 <= s < t_end]
    ok = sum(pts[s][0] for s in win)
    n = sum(pts[s][1] for s in win)
    # 복구 = 장애 시작 후 처음 실패가 난 뒤, 연속 3초 동안 성공률 ≥ 99% 가 되는 첫 시각까지
    fails = [s for s in win if pts[s][0] < pts[s][1]]
    rec = 0
    if fails:
        first = fails[0]
        for s in [x for x in win if x >= first]:
            run = [x for x in range(s, s + 3) if x in pts]
            if len(run) == 3 and all(pts[x][0] >= 0.99 * pts[x][1] for x in run):
                rec = s - t0
                break
        else:
            rec = None
    out["faults"].append({"fault": name, "window_s": t_end - t0, "requests": n, "availability": round(ok / n, 5) if n else None,
                          "failed_requests": n - ok, "recovery_s": rec})
print(json.dumps(out, ensure_ascii=False, indent=1))
