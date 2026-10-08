"""서드파티 라이선스 목록 생성 (D17-③, NFR-LIC1) — `make licenses` 가 prod 이미지 안에서 실행한다(오프라인, 설치 메타데이터만).

사용: python scripts/third_party.py <pnpm licenses list --prod --json 결과 파일>  > THIRD_PARTY_LICENSES.md
파이썬은 이 이미지에 설치된 런타임 패키지(`uv sync --no-dev`), JS 는 콘솔 번들 의존성(prod), 폰트는 번들에 포함된 Pretendard.
약한·강한 카피레프트(GPL·LGPL·AGPL·MPL·EPL·CDDL)와 소스 공개형(SSPL·BUSL)은 '검토' 표에 따로 올린다.
"""
import json
import re
import sys
from importlib import metadata

REVIEW = re.compile(r"\b(A?GPL|LGPL|MPL|EPL|CDDL|SSPL|BUSL)", re.I)
# 검토 결과(COMPLIANCE NFR-LIC1 예외) — 이름 → 사유
EXCEPTIONS = {
    "psycopg": "LGPL-3.0-only — 수정 없이 동적 연결(파이썬 import)로만 사용, 교체 가능(SQLAlchemy 드라이버). 배포 시 라이선스 사본·출처 고지",
    "psycopg-binary": "LGPL-3.0-only — psycopg 과 같음(빌드된 libpq·OpenSSL 동봉 휠)",
    "certifi": "MPL-2.0 — 파일 단위 카피레프트, 수정 없이 사용(CA 번들 데이터). 수정 시 해당 파일 소스 공개",
}


def _py_license(d: metadata.Distribution) -> str:
    m = d.metadata
    if m.get("License-Expression"):
        return m["License-Expression"].strip()
    classifiers = [c.split("::")[-1].strip() for c in m.get_all("Classifier") or [] if c.startswith("License ::")]
    lic = (m.get("License") or "").strip()
    if lic and len(lic) <= 60 and "\n" not in lic:
        return lic
    return " / ".join(classifiers) or (lic.splitlines()[0][:60] if lic else "UNKNOWN")


def main(js_path: str) -> None:
    py = sorted({(d.metadata["Name"], d.version, _py_license(d)) for d in metadata.distributions() if d.metadata["Name"]},
                key=lambda r: r[0].lower())
    js = []
    for lic, pkgs in json.load(open(js_path, encoding="utf-8")).items():
        for p in pkgs:
            js.append((p["name"], ", ".join(p.get("versions") or []), p.get("license") or lic))
    js.sort(key=lambda r: r[0].lower())
    out = ["# THIRD_PARTY_LICENSES", "",
           "> 자동 생성 — `make licenses` (prod 이미지의 설치 메타데이터 + 콘솔 prod 의존성). 직접 고치지 말고 다시 생성한다.",
           "> 저장소 자체 라이선스는 `LICENSE`(Apache-2.0), 고지는 `NOTICE`.", ""]
    review = [(n, v, lic, "python") for n, v, lic in py if REVIEW.search(lic)] + \
             [(n, v, lic, "js") for n, v, lic in js if REVIEW.search(lic)]
    out += ["## 검토 대상 (카피레프트·소스 공개형)", "", "| 패키지 | 버전 | 라이선스 | 구분 | 판정 |", "|---|---|---|---|---|"]
    out += [f"| {n} | {v} | {lic} | {k} | {EXCEPTIONS.get(n.lower(), '검토 필요')} |" for n, v, lic, k in review] or ["| (없음) | | | | |"]
    out += ["", f"## Python 런타임 ({len(py)})", "", "| 패키지 | 버전 | 라이선스 |", "|---|---|---|"]
    out += [f"| {n} | {v} | {lic} |" for n, v, lic in py]
    out += ["", f"## 콘솔 JS (prod, {len(js)})", "", "| 패키지 | 버전 | 라이선스 |", "|---|---|---|"]
    out += [f"| {n} | {v} | {lic} |" for n, v, lic in js]
    out += ["", "## 폰트", "", "| 이름 | 라이선스 | 사본 |", "|---|---|---|",
            "| Pretendard | OFL-1.1 | `web/public/licenses/Pretendard-OFL-1.1.txt` (콘솔 번들 `/licenses/` 에 포함) |", ""]
    sys.stdout.write("\n".join(out))


if __name__ == "__main__":
    main(sys.argv[1])
