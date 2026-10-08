"""helm template 결과 오프라인 정합성 검사 (클러스터·인터넷 불필요).

사용: helm template anx deploy/helm/agentnexus [-f values] | python3 deploy/helm/check-rendered.py
검사: 워크로드 셀렉터 ↔ 파드 레이블 · Service 셀렉터·targetPort ↔ 컨테이너 포트 · ConfigMap/Secret/PVC 참조 존재
      · 이미지 태그 고정(latest 금지) · 비루트·권한 상승 차단 · 프로브 · api 의 ENTRYPOINT 유지(args 사용)
      · 데모 시드 = 데모 서비스 유무 · api NetworkPolicy 출처를 좁히면 같은 릴리스 내부 호출자 포함
"""
import sys

import yaml

docs = [d for d in yaml.safe_load_all(sys.stdin) if d]
by_kind: dict[str, dict[str, dict]] = {}
for d in docs:
    by_kind.setdefault(d["kind"], {})[d["metadata"]["name"]] = d
errors: list[str] = []
secret_names = set(by_kind.get("Secret", {})) | {"agentnexus-prod"}  # ci/ha-values 의 existingSecret
config_names = set(by_kind.get("ConfigMap", {}))
pvc_names = set(by_kind.get("PersistentVolumeClaim", {}))
pods: dict[str, dict] = {}

for kind in ("Deployment", "StatefulSet"):
    for name, w in by_kind.get(kind, {}).items():
        sel = w["spec"]["selector"]["matchLabels"]
        labels = w["spec"]["template"]["metadata"]["labels"]
        if any(labels.get(k) != v for k, v in sel.items()):
            errors.append(f"{kind}/{name}: selector {sel} ≠ labels {labels}")
        spec = w["spec"]["template"]["spec"]
        pods[name] = {"labels": labels, "spec": spec}
        psc = spec.get("securityContext", {})
        if not psc.get("runAsNonRoot"):
            errors.append(f"{kind}/{name}: runAsNonRoot 아님")
        for c in spec.get("initContainers", []) + spec["containers"]:
            img = c["image"]
            if ":" not in img.rsplit("/", 1)[-1] or img.endswith(":latest"):
                errors.append(f"{kind}/{name}/{c['name']}: 이미지 태그 미고정 {img}")
            if c.get("securityContext", {}).get("allowPrivilegeEscalation") is not False:
                errors.append(f"{kind}/{name}/{c['name']}: allowPrivilegeEscalation 차단 없음")
            if c in spec["containers"] and "readinessProbe" not in c:
                errors.append(f"{kind}/{name}/{c['name']}: readinessProbe 없음")
            if c.get("command") and "agentnexus" in img:
                errors.append(f"{kind}/{name}/{c['name']}: command 가 ENTRYPOINT(entrypoint.sh) 를 덮어씀 — args 사용")
            for ef in c.get("envFrom", []):
                if "configMapRef" in ef and ef["configMapRef"]["name"] not in config_names:
                    errors.append(f"{name}: 없는 ConfigMap {ef['configMapRef']['name']}")
                if "secretRef" in ef and ef["secretRef"]["name"] not in secret_names:
                    errors.append(f"{name}: 없는 Secret {ef['secretRef']['name']}")
            for e in c.get("env", []):
                ref = e.get("valueFrom", {})
                if "configMapKeyRef" in ref:
                    cm = by_kind.get("ConfigMap", {}).get(ref["configMapKeyRef"]["name"])
                    if not cm or ref["configMapKeyRef"]["key"] not in cm["data"]:
                        errors.append(f"{name}: ConfigMap 키 없음 {ref['configMapKeyRef']}")
                if "secretKeyRef" in ref:
                    sname = ref["secretKeyRef"]["name"]
                    sec = by_kind.get("Secret", {}).get(sname)
                    if sname not in secret_names or (sec and ref["secretKeyRef"]["key"] not in sec["data"]):
                        errors.append(f"{name}: Secret 키 없음 {ref['secretKeyRef']}")
        for v in spec.get("volumes", []):
            if "persistentVolumeClaim" in v and v["persistentVolumeClaim"]["claimName"] not in pvc_names:
                errors.append(f"{name}: 없는 PVC {v['persistentVolumeClaim']['claimName']}")
            if "configMap" in v and v["configMap"]["name"] not in config_names:
                errors.append(f"{name}: 없는 ConfigMap 볼륨 {v['configMap']['name']}")

for name, svc in by_kind.get("Service", {}).items():
    sel = svc["spec"]["selector"]
    targets = [p for p in pods.values() if all(p["labels"].get(k) == v for k, v in sel.items())]
    if not targets:
        errors.append(f"Service/{name}: 셀렉터와 맞는 파드 없음 {sel}")
        continue
    ports = {p.get("name") for t in targets for c in t["spec"]["containers"] for p in c.get("ports", [])}
    for p in svc["spec"]["ports"]:
        if isinstance(p["targetPort"], str) and p["targetPort"] not in ports:
            errors.append(f"Service/{name}: targetPort {p['targetPort']} 없음 ({ports})")

services = set(by_kind.get("Service", {}))
for name, ing in by_kind.get("Ingress", {}).items():
    for r in ing["spec"]["rules"]:
        for p in r["http"]["paths"]:
            if p["backend"]["service"]["name"] not in services:
                errors.append(f"Ingress/{name}: 없는 Service {p['backend']['service']['name']}")
for name, hr in by_kind.get("HTTPRoute", {}).items():
    for r in hr["spec"]["rules"]:
        for b in r["backendRefs"]:
            if b["name"] not in services:
                errors.append(f"HTTPRoute/{name}: 없는 Service {b['name']}")
for name, hpa in by_kind.get("HorizontalPodAutoscaler", {}).items():
    if hpa["spec"]["scaleTargetRef"]["name"] not in by_kind.get("Deployment", {}):
        errors.append(f"HPA/{name}: 대상 Deployment 없음")

# 데모 시드 = 데모 서비스 유무 (D19-④) · api 수신 출처를 좁히면 같은 릴리스 내부 호출자는 차트가 넣는다
demo_on = any(n.endswith("-a2a-demo") for n in by_kind.get("Deployment", {}))
for name, cm in by_kind.get("ConfigMap", {}).items():
    if name.endswith("-config") and cm["data"].get("SEED_DEMO") != str(demo_on).lower():
        errors.append(f"ConfigMap/{name}: SEED_DEMO={cm['data'].get('SEED_DEMO')} ≠ 데모 서비스 {demo_on}")
for name, np in by_kind.get("NetworkPolicy", {}).items():
    if not name.endswith("-api"):
        continue
    for rule in np["spec"].get("ingress", []):
        if "from" not in rule:
            continue
        comps = {v for f in rule["from"] for e in f.get("podSelector", {}).get("matchExpressions", [])
                 if e["key"] == "app.kubernetes.io/component" for v in e["values"]}
        if not {"api", "mcp-servers", "a2a-demo", "a2a-grpc"} <= comps:
            errors.append(f"NetworkPolicy/{name}: 같은 릴리스 내부 호출자 누락 {comps}")

# a2a-grpc 프로브 — TLS 수신이 아니면 grpc.health.v1(readiness = 위임 대상까지), TLS 면 TCP · 시크릿 교체 시 api 재기동 (D20-③)
for name, w in by_kind.get("Deployment", {}).items():
    spec = w["spec"]["template"]["spec"]
    if name.endswith("-a2a-grpc"):
        tls = any(v.get("name") == "tls" for v in spec.get("volumes", []))
        c = spec["containers"][0]
        want = "tcpSocket" if tls else "grpc"
        if want not in c["readinessProbe"] or want not in c["livenessProbe"]:
            errors.append(f"Deployment/{name}: 프로브가 {want} 가 아님 (tls={tls})")
        if not tls and c["readinessProbe"]["grpc"].get("service") != "readiness":
            errors.append(f"Deployment/{name}: readiness 프로브 service=readiness 아님")
    if name.endswith("-api") and by_kind.get("Secret") and \
            "checksum/secret" not in (w["spec"]["template"]["metadata"].get("annotations") or {}):
        errors.append(f"Deployment/{name}: checksum/secret 없음 — 시크릿 교체가 재기동으로 이어지지 않음")
    if name.endswith("-api") and not by_kind.get("Secret") and \
            "agentnexus/rotation-nonce" not in (w["spec"]["template"]["metadata"].get("annotations") or {}):
        errors.append(f"Deployment/{name}: existingSecret 인데 rotation-nonce 없음 — 키 교체 뒤 재기동 수단 없음 (ci/ha)")

summary = ", ".join(f"{k} {len(v)}" for k, v in sorted(by_kind.items()))
if errors:
    print("FAIL", summary)
    print("\n".join(f"  - {e}" for e in errors))
    sys.exit(1)
print("OK", summary)
