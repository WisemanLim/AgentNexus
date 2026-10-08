> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 3차 리뷰(round3, D20·D21 hunk 한정) · 스냅샷 2026-10-08 12:23 KST · [← INDEX.md](INDEX.md) · 2차 리뷰: [round2 INFRA.md](../../round2/CODE-REVIEW-Lv2/INFRA.md)

# 13. 배포·빌드 요약 리뷰 / Deploy · Build (D20-③·D21-③ hunk)

대상(9개): `deploy/helm/agentnexus/templates/a2a-grpc.yaml`(107) · `templates/api.yaml`(119) · `templates/demo.yaml`(67) · `templates/secret.yaml`(33) · `values.yaml`(176) · `deploy/helm/check-rendered.py`(127) · `docker-compose.yml`(82) · `docker-compose.prod.yml`(36) · `Dockerfile`(58)
근거 확인을 위해 함께 읽은 파일: `templates/scaling.yaml`(a2a-grpc PDB) · `templates/configmap.yaml` · `ci/ha-values.yaml` · `Chart.yaml` · `scripts/entrypoint.sh` · `app/grpc_server.py` · `app/services/a2a_grpc.py` · `app/core/keys.py` · `app/cli/nexus.py`

YAML·Dockerfile 이라 줄마다 짚지 않고 **논리 블록 단위**로 본다.

**파일 요약 / File Summary**
- 역할 / Role: Kubernetes 배포(Helm 차트), compose 공통·prod 오버레이, 멀티스테이지 이미지 빌드.
- 진입점 거리 / Distance: 0 — 운영자가 직접 쓰는 배포 진입점(`helm install/upgrade`, `docker compose up`, `docker build`).
- 3차 변경 요약: D20-③(Epic 54) — a2a-grpc gRPC 헬스 프로브(TLS 면 TCP)·`topologySpreadConstraints`·복제 2개 이상이면 PDB · api·데모 파드 `checksum/secret`(시크릿 교체 → 재기동) · compose 공통 a2a-grpc 헬스체크 = probe readiness · prod a2a-grpc 는 `.env.prod` 대신 최소 env · e2e 베이스 digest 고정 · 잠금 파일 없으면 빌드 실패. D21-③(Epic 58) — `secrets.signingKeyPemPrevious`·`encryptionKeyPrevious`(지정했을 때만 Secret 에).
- 테스트 커버리지 흔적: `make helm-check`(`deploy/helm/check-rendered.py` — 54th 에서 `service: other`·`checksum/secret` 제거 렌더로 음성 확인, `test/impl/54th/result.md`), `test/test_deploy_hardening.py:41-50`(C54-02 compose), `:52-60`(C54-03 digest·잠금 파일), live `test/impl/54th/logs/local-probe.log`(L54-01).

## 블록 리뷰 / Block Walk (Level 2)

### (1) `deploy/helm/agentnexus/templates/a2a-grpc.yaml`

```yaml path=deploy/helm/agentnexus/templates/a2a-grpc.yaml lines=19-55
      {{- include "agentnexus.podCommon" . | nindent 6 }}
      {{- with .Values.a2aGrpc.topologySpreadConstraints }}
      topologySpreadConstraints:
        {{- range . }}
        - {{- toYaml (merge (dict) . (dict "labelSelector" (dict "matchLabels" (include "agentnexus.selector" (list $ "a2a-grpc") | fromYaml)))) | nindent 10 }}
        {{- end }}
      {{- end }}
      containers:
        - name: a2a-grpc
          image: {{ include "agentnexus.image" (list . .Values.image) }}
          imagePullPolicy: {{ .Values.image.pullPolicy }}
          args: ["python", "-m", "app.grpc_server"]
          env:
            - { name: AGENTNEXUS_ROLE, value: a2a-grpc }
            {{- range $k := list "GATEWAY_URL" "A2A_GRPC_TLS_CERT" "A2A_GRPC_TLS_KEY" "LOG_LEVEL" "APP_ENV" }}
            - name: {{ $k }}
              valueFrom: { configMapKeyRef: { name: {{ $fn }}-config, key: {{ $k }} } }
            {{- end }}
          ports:
            - { name: grpc, containerPort: 50051 }
          {{- if $tls }}  # gRPC 프로브는 TLS 를 말하지 못한다 — TLS 수신이면 TCP
          readinessProbe:
            tcpSocket: { port: grpc }
            periodSeconds: 10
          livenessProbe:
            tcpSocket: { port: grpc }
            initialDelaySeconds: 10
            periodSeconds: 20
          {{- else }}  # grpc.health.v1 — readiness 는 위임 대상 api 의 /health 까지 (D20-③)
          readinessProbe:
            grpc: { port: 50051, service: readiness }
            periodSeconds: 10
          livenessProbe:
            grpc: { port: 50051 }
            initialDelaySeconds: 10
            periodSeconds: 20
          {{- end }}
```
**분석 / Analysis (Level 2)**
- L20-25(D20-③): 사용자가 준 존 분산 규칙마다 `labelSelector` 를 차트가 채운다. `merge (dict) . (labelSelector…)` — Sprig `merge` 는 **앞 인자가 우선**이라 사용자가 `labelSelector` 를 직접 주면 그 값이 유지되고, 없을 때만 차트 셀렉터가 들어간다(api 와 같은 형식, `templates/api.yaml:82-87`).
- L33-36: a2a-grpc 는 ConfigMap 의 키 5개만 받는다 — Secret(서명 키·DB 비밀번호)은 주지 않는다(엣지 노출 서비스 최소 권한, compose prod 와 같은 원칙).
- L39-46: TLS 수신이면 kubelet 의 gRPC 프로브가 TLS 를 말하지 못해 TCP. L47-54(D20-③): 평문이면 네이티브 gRPC 프로브 — readiness 는 `service: readiness`(위임 대상 api `/health` 까지, `app/services/a2a_grpc.py:287-294`), liveness 는 빈 서비스(프로세스 응답). 차트 `kubeVersion: ">=1.27.0-0"`(`Chart.yaml`)이라 gRPC 프로브(1.27 GA)를 쓸 수 있다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] **L19(코드 쪽과 같은 항목)** 고가용성 참조 프로파일은 **TLS 수신**이다(`ci/ha-values.yaml:12` — `tls.secretName: agentnexus-grpc-tls`). 그래서 D20-③ 의 "readiness = 위임 대상까지" 개선은 HA 구성에 적용되지 않고(L39-46 TCP), api 가 내려가도 a2a-grpc 파드는 Ready 로 남는다 — 2차 INFRA.md L84 지적이 HA 에서는 그대로다. 프로브 컨테이너에서 TLS 채널로 Health 를 부르는 `exec` 프로브(자기 인증서를 신뢰 루트로)로 의미를 살릴 수 있다.
- [위험:낮음] **L29** 평문 구성에서 readiness 가 api `/health`(DB `ping` 포함, `app/main.py:228-237`)에 묶여 있어, DB 장애조치 몇 초 동안 **모든 a2a-grpc 파드가 동시에 NotReady** → Service 엔드포인트가 비고 Ingress 가 gRPC 오류 대신 503 을 준다. api 도 같은 시간 NotReady 이므로 결과적으로 큰 차이는 없지만, readiness 를 "api 에 TCP 로 닿는가" 정도로 완화하거나 `failureThreshold` 를 api 보다 크게 두면 순간 장애가 전파되지 않는다.

### (2) `deploy/helm/agentnexus/templates/scaling.yaml`(함께 읽음 — a2a-grpc PDB)

```yaml path=deploy/helm/agentnexus/templates/scaling.yaml lines=28-39
{{- if and .Values.podDisruptionBudget.enabled .Values.a2aGrpc.enabled (gt (int .Values.a2aGrpc.replicas) 1) }}
---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: {{ $fn }}-a2a-grpc
  labels: {{- include "agentnexus.labels" . | nindent 4 }}
spec:
  minAvailable: 1
  selector:
    matchLabels: {{- include "agentnexus.selector" (list . "a2a-grpc") | nindent 6 }}
{{- end }}
```
**분석 / Analysis (Level 2)**
- D20-③: PDB 가 켜져 있고 a2a-grpc 복제가 2개 이상일 때만 `minAvailable: 1`(복제 1개에 `minAvailable: 1` 을 두면 노드 드레인이 영원히 막힌다 — 그 실수를 피한 조건). 2차 INFRA.md L85 해결.

### (3) `templates/api.yaml` · `templates/demo.yaml` — `checksum/secret`

```yaml path=deploy/helm/agentnexus/templates/api.yaml lines=15-21
  template:
    metadata:
      labels: {{- include "agentnexus.selector" (list . "api") | nindent 8 }}
      annotations:
        checksum/config: {{ include (print $.Template.BasePath "/configmap.yaml") . | sha256sum }}
        checksum/secret: {{ include (print $.Template.BasePath "/secret.yaml") . | sha256sum }}  # 시크릿 교체도 재기동 (D20-③)
        {{- with .Values.api.podAnnotations }}{{ toYaml . | nindent 8 }}{{- end }}
```
```yaml path=deploy/helm/agentnexus/templates/demo.yaml lines=18-21
    metadata:
      labels: {{- include "agentnexus.selector" (list $root $c.name) | nindent 8 }}
      annotations:
        checksum/secret: {{ include (print $root.Template.BasePath "/secret.yaml") $root | sha256sum }}  # 데모 워크로드 시크릿 교체 시 재기동 (D20-③)
```
**분석 / Analysis (Level 2)**
- D20-③: Secret 템플릿을 렌더한 결과의 해시를 파드 주석에 넣는다 — Secret 값이 바뀌면 파드 템플릿이 바뀌어 롤링 재기동된다(2차 INFRA.md L144 해결). 키 회전 env 절차(새 키 + `*_PREVIOUS` → 재기동)가 이 재기동에 기댄다(`test/impl/58th/scenario.md` "env 모드" 행).
**짚어볼 점 / Watch-outs**
- [위험:낮음] **L27** ① `existingSecret` 을 쓰면 `secret.yaml` 이 통째로 빈 렌더라(`templates/secret.yaml:1`) 해시가 **항상 같다** — 외부에서 Secret 을 바꿔도 재기동되지 않는다. 그런데 차트는 운영에 `existingSecret` 을 **권장**하고(`values.yaml:1`), HA 참조 프로파일도 그렇다(`ci/ha-values.yaml:6`). 즉 권장 경로에서는 D20-③ 과 D21-③ env 회전 절차의 "재기동" 이 자동으로 일어나지 않는다(2차 INFRA.md L144 가 "existingSecret 이면 생략" 이라고 적은 그 공백). `kubectl rollout restart` 를 절차서에 명시하거나 `values.yaml` 에 `secrets.rotationNonce` 같은 값을 두어 주석에 넣는다. ② `secret.yaml` 은 `lookup` 으로 기존 값을 유지하고 없으면 난수를 만든다(L4·L8·L10-11). `helm template` 기반 GitOps(Argo CD 등 — `lookup` 이 빈 값)에서는 렌더마다 서명·암호화 키가 새로 생성되어 **모든 토큰·암호문이 무효**가 되는데(이 위험은 이전부터 있었다), 이제 해시도 렌더마다 바뀌어 동기화 때마다 api·데모 파드가 재기동된다 — GitOps 사용자는 반드시 `existingSecret` 이어야 한다는 안내가 필요하다. ③ `demo.yaml` 의 해시는 Secret 전체라, 데모가 쓰지 않는 키(서명 키 등)가 바뀌어도 데모가 재기동된다(무해, 불필요한 재기동).

### (4) `templates/secret.yaml` — 이전 키(D21-③)

```yaml path=deploy/helm/agentnexus/templates/secret.yaml lines=1-13
{{- if not .Values.secrets.existingSecret }}
{{- $fn := include "agentnexus.fullname" . -}}
{{- $name := include "agentnexus.secretName" . -}}
{{- $old := (lookup "v1" "Secret" .Release.Namespace $name).data | default dict -}}
{{- $s := .Values.secrets -}}
{{- $keep := dict -}}
{{- range $k, $v := dict "BOOTSTRAP_ADMIN_PASSWORD" $s.bootstrapAdminPassword "METRICS_TOKEN" $s.metricsToken "OTLP_INGEST_TOKEN" $s.otlpIngestToken "A2A_DEMO_CLIENT_SECRET" $s.a2aDemoClientSecret }}
{{- $_ := set $keep $k ($v | default (get $old $k | default "" | b64dec) | default (randAlphaNum 32)) -}}
{{- end }}
{{- $_ := set $keep "SIGNING_KEY_PEM" ($s.signingKeyPem | default (get $old "SIGNING_KEY_PEM" | default "" | b64dec) | default (genPrivateKey "rsa")) -}}
{{- $_ := set $keep "ENCRYPTION_KEY" ($s.encryptionKey | default (get $old "ENCRYPTION_KEY" | default "" | b64dec) | default (randBytes 32 | replace "+" "-" | replace "/" "_")) -}}
{{- with $s.signingKeyPemPrevious }}{{- $_ := set $keep "SIGNING_KEY_PEM_PREVIOUS" . -}}{{- end }}
{{- with $s.encryptionKeyPrevious }}{{- $_ := set $keep "ENCRYPTION_KEY_PREVIOUS" . -}}{{- end }}
```
**분석 / Analysis (Level 2)**
- L10-11: 서명·암호화 키는 values → 기존 Secret → 생성 순. **Helm 배포는 언제나 env 모드**가 된다 — 관리 API 회전(`POST /api/v1/admin/keys/rotate`)은 409 `keys_env_managed`(`app/api/admin.py:572-575`)이고, 회전은 values 로 한다.
- L12-13(D21-③): 이전 키는 지정했을 때만 Secret 에 들어가고, 값을 비우면 다음 렌더에서 키 자체가 빠진다(`$keep` 를 매번 새로 만들기 때문) — "유예가 끝나면 비움" 이 values 한 줄로 끝난다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] **L7 — env 모드 회전 중 롤링 재기동 구간의 키 불일치.** api 는 `maxSurge: 1, maxUnavailable: 0` 롤링이다(`templates/api.yaml:12`). 서명 키를 바꾸면(`signingKeyPem`=새 키, `signingKeyPemPrevious`=옛 키) 새 파드는 새 키로 서명하고 옛 키도 검증하지만, **아직 남은 옛 파드는 새 키를 모른다** — 새 파드가 발급한 토큰이 옛 파드로 가면 401(`app/core/keys.py:139-144` 의 kid 불일치). 암호화 키도 같다 — 새 파드가 새 키로 암호화한 시크릿·MFA 비밀을 옛 파드가 풀지 못한다. 롤링이 끝날 때까지(수 분) 간헐 오류. 최소 수정(절차): 2단계 — ① 새 키를 먼저 "검증·복호화 전용" 으로 배포(서명 키는 JWKS·검증 목록에만, 암호화 키는 `ENCRYPTION_KEY_PREVIOUS` 에 새 키를) ② 전 파드 반영 뒤 주·보조를 바꾸는 배포. 코드로는 "다음 키(next)" env 를 하나 더 받으면 된다.
- [위험:낮음] 이전 서명 키는 **개인키** PEM 이어야 한다(`app/core/keys.py:116`) — 차트 주석(`values.yaml:75` "직전 서명 키 PEM")만으로는 공개키를 넣는 실수를 막지 못한다. [keys.py.md](app/core/keys.py.md) 참고.

### (5) `values.yaml`

```yaml path=deploy/helm/agentnexus/values.yaml lines=69-79
# 시크릿 — existingSecret 을 쓰면 아래 값은 무시. 비워 두면 설치 시 생성하고 업그레이드 때 유지(lookup).
secrets:
  existingSecret: ""
  bootstrapAdminPassword: ""
  signingKeyPem: ""
  encryptionKey: ""
  signingKeyPemPrevious: ""     # 키 회전 — 직전 서명 키 PEM (토큰 수명 뒤 비움, D21-③)
  encryptionKeyPrevious: ""     # 키 회전 — 직전 암호화 키(쉼표 구분, 재암호화 뒤 비움)
  metricsToken: ""
  otlpIngestToken: ""
  a2aDemoClientSecret: ""
```
```yaml path=deploy/helm/agentnexus/values.yaml lines=112-127
# A2A gRPC 바인딩 수신 (D16-②·D17-②) — 상태 없음: RPC 를 api 의 HTTP+JSON 바인딩으로 위임(같은 인증·정책·감사)
a2aGrpc:
  enabled: false
  replicas: 1
  publicUrl: ""               # Agent Card 에 광고할 주소 (예: grpcs://grpc.agentnexus.local:443 — 비우면 GRPC 인터페이스 없음)
  tls:
    secretName: ""            # kubernetes.io/tls Secret — 지정하면 파드가 직접 TLS 수신(A2A_GRPC_TLS_CERT·KEY), 비우면 평문(h2c)
  ingress:
    enabled: false            # ingress-nginx 기준 backend-protocol GRPC(파드 TLS 면 GRPCS) — Ingress 가 TLS 종료
    className: ""
    host: grpc.agentnexus.local
    annotations: {}
    tls: []                   # [{ secretName: agentnexus-grpc-tls, hosts: [grpc.agentnexus.local] }]
  resources:
    requests: { cpu: 50m, memory: 128Mi }
  topologySpreadConstraints: []  # api 와 같은 형식 (labelSelector 는 차트가 채움) — replicas > 1 이면 PDB 도 (D20-③)
```
**분석 / Analysis (Level 2)**
- L75-76(D21-③): 이전 키 두 칸. L127(D20-③): a2a-grpc 존 분산(형식은 api 와 같음).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L75 주석은 "토큰 수명 뒤 비움" 이지만, 비우지 않아도 앱은 경고하지 않고 그 키를 **영구히** 검증에 쓴다(env 이전 키는 만료가 없다 — `app/core/keys.py:115-117`). 회전 사유가 유출이면 위험이 길어진다. 차트 `NOTES.txt` 에 "이전 키가 설정되어 있음" 경고를 출력하거나 앱 기동 로그 경고.

### (6) `deploy/helm/check-rendered.py`

```python path=deploy/helm/check-rendered.py lines=107-120
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
```
**분석 / Analysis (Level 2)**
- D20-③ 불변식: a2a-grpc 는 TLS 여부에 맞는 프로브 종류(L110-115), 평문이면 readiness `service=readiness`(L116-117), api 는 Secret 이 렌더될 때 `checksum/secret` 필수(L118-120). 54th 에서 위반 렌더로 FAIL 이 나는 음성 확인을 했다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] 해시 주석의 **존재**만 본다 — `helm template`(`lookup` 없음)으로 렌더하므로 (3)의 ①②(existingSecret 이면 고정값, GitOps 면 매번 변경)는 이 검사로 잡을 수 없다. 데모 Deployment 의 `checksum/secret` 은 검사하지 않는다.

### (7) `docker-compose.yml` · `docker-compose.prod.yml`

```yaml path=docker-compose.yml lines=61-76
  # A2A gRPC 바인딩 수신 (D16-②) — 상태 없음: 요청을 게이트웨이(api) HTTP+JSON 바인딩으로 위임 (인증·정책·감사는 api)
  a2a-grpc:
    <<: *app
    command: ["python", "-m", "app.grpc_server"]
    ports: ["${BIND_HOST:-127.0.0.1}:${A2A_GRPC_PORT:-8851}:50051"]
    environment:
      AGENTNEXUS_ROLE: a2a-grpc
      GATEWAY_URL: http://api:8000
    depends_on:
      api: { condition: service_healthy }
    networks: [edge]
    healthcheck:  # grpc.health.v1 — readiness = 위임 대상 api 까지 (TLS 수신이면 TCP) (D20-③)
      test: ["CMD", "python", "-m", "app.grpc_server", "--probe", "readiness"]
      interval: 10s
      timeout: 3s
      retries: 6
```
```yaml path=docker-compose.prod.yml lines=14-20
  a2a-grpc:  # 엣지에 노출되는 상태 없는 위임 서비스 — .env.prod 전체(서명 키·DB 비밀번호 등) 대신 필요한 값만 (D20-③)
    environment:
      APP_ENV: ${APP_ENV:-prod}
      LOG_LEVEL: ${LOG_LEVEL:-INFO}
      A2A_GRPC_TLS_CERT: ${A2A_GRPC_TLS_CERT:-}
      A2A_GRPC_TLS_KEY: ${A2A_GRPC_TLS_KEY:-}
    restart: unless-stopped
```
**분석 / Analysis (Level 2)**
- 공통(L72-76, D20-③): a2a-grpc 헬스체크 = `python -m app.grpc_server --probe readiness`(TLS 면 TCP). 54th live 에서 api 정지 → readiness exit 1, liveness 0 확인.
- prod(L14-20, D20-③): 엣지(`edge` 네트워크, 호스트 포트 공개)에 노출되는 a2a-grpc 에 `.env.prod` 전체(서명 키·DB 비밀번호·암호화 키) 대신 필요한 값 4개만 — 엣지 서비스가 뚫려도 플랫폼 키가 함께 새지 않게(2차 INFRA.md L421 해결). `${…}` 는 `--env-file .env.prod` 로 **치환만** 되고 컨테이너 환경으로 통째로 들어가지 않는다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] **L28** prod 오버레이가 `A2A_GRPC_TLS_CERT`·`A2A_GRPC_TLS_KEY` 를 **경로**로 넘기지만 인증서 파일을 넣을 볼륨이 없다(공통 정의 L62-76 에도 a2a-grpc 볼륨 없음). 운영자가 경로를 채우면 컨테이너 안에 파일이 없어 `server_tls()` 의 `open` 이 실패(`app/services/a2a_grpc.py:255`)해 재시작 반복. `compose/` 오버레이 예시(`- ./certs:/tls:ro`)나 README 안내 필요.
- [위험:낮음] 헬스체크가 10초마다 파이썬 프로세스를 새로 띄워 a2a-sdk·protobuf·httpx 를 import 한다 — 바쁜 호스트에서 `timeout: 3s`(L75)에 가까워질 수 있다([a2a_grpc.py.md](app/services/a2a_grpc.py.md) L19).
- [위험:중간] (교차) **M11** L4-6 의 `build:` + 로컬 태그 `image:` 앵커 때문에, 설치 CLI 의 "서명 검증한 이미지를 그대로 기동"(`app/cli/nexus.py:347`)이 로컬 이미지·로컬 빌드로 바뀔 수 있다. 상세는 [nexus.py.md](app/cli/nexus.py.md).

### (8) `Dockerfile`

```dockerfile path=Dockerfile lines=19-28
# web-dev: 콘솔 SPA(React + Vite) 개발 서버 — 의존성 설치는 이미지 빌드 안에서만 (local 은 src 만 바인드)
FROM node:22-alpine@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402 AS web-dev
RUN npm install -g pnpm@10.12.1
WORKDIR /web
ARG PNPM_FROZEN=1
COPY web/package.json web/pnpm-lock.yaml* ./
# 잠금 파일이 없으면 실패 — 고정 안 된 설치로 조용히 넘어가지 않는다 (D20-③). 잠금 갱신은 make web-lock(PNPM_FROZEN=0)
RUN if [ "$PNPM_FROZEN" = 1 ]; then test -f pnpm-lock.yaml || { echo "web/pnpm-lock.yaml 없음 — make web-lock"; exit 1; }; \
      pnpm install --frozen-lockfile; else pnpm install --no-frozen-lockfile; fi \
    && cp pnpm-lock.yaml /tmp/pnpm-lock.resolved.yaml
```
```dockerfile path=Dockerfile lines=37-46
# e2e: 콘솔 브라우저 스모크 (Playwright · Chromium) — compose e2e 프로파일에서만 사용
FROM mcr.microsoft.com/playwright:v1.55.0-noble@sha256:b27e719ecbfef153e13fd24e8341736733bf2658b229677eb21ff57ff5d7fb29 AS e2e
WORKDIR /e2e
ARG NPM_LOCKED=1
COPY web/e2e/package.json web/e2e/package-lock.json* ./
RUN if [ "$NPM_LOCKED" = 1 ]; then test -f package-lock.json || { echo "web/e2e/package-lock.json 없음 — make e2e-lock"; exit 1; }; \
      npm ci --no-audit --no-fund; else npm install --no-audit --no-fund; fi \
    && cp package-lock.json /tmp/package-lock.resolved.json
COPY web/e2e/ ./
CMD ["npx", "playwright", "test"]
```
**분석 / Analysis (Level 2)**
- L23-28(D20-③): 기본 `PNPM_FROZEN=1` 에서 잠금 파일이 없으면 **빌드 실패** — 전에는 `COPY … pnpm-lock.yaml*`(글롭이라 파일이 없어도 성공)에 이어 잠금 없이 설치되어 고정 안 된 의존성이 이미지에 들어갈 수 있었다. 잠금 갱신은 `PNPM_FROZEN=0` 으로만.
- L38(D20-③): e2e 베이스(Playwright)도 digest 고정. L40-44: `npm ci`(잠금 그대로) 또는 갱신 모드.
- 흔한 실수: `COPY a b*` 의 글롭은 "없어도 오류 아님" 이라, 잠금 파일 존재를 COPY 로 보장했다고 착각하기 쉽다 — 여기처럼 `test -f` 로 확인해야 한다.
**짚어볼 점 / Watch-outs**
- (없음 — D20-③ 변경은 정확하다. Python 쪽은 `uv sync --locked`(L14·L52)가 잠금 파일 없음·불일치에서 실패해 이미 같은 성질이다.)

## 파일 평가 / File Assessment
- 강점: (1) a2a-grpc 에 liveness·readiness 를 나눈 표준 gRPC 헬스와 복제 수에 맞춘 PDB·존 분산을 넣었다(`templates/a2a-grpc.yaml:47-54`, `templates/scaling.yaml:28-39`). (2) 엣지 노출 서비스에서 시크릿을 걷어 냈다(`templates/a2a-grpc.yaml:33-36`, `docker-compose.prod.yml:14-20`). (3) 잠금 파일·베이스 digest 를 빌드 실패 조건으로 강제(`Dockerfile:25-28`, `:38`, `:42-43`).
- 위험: (1) [위험:낮음] 권장 경로(`existingSecret`)에서 `checksum/secret` 이 고정 — 시크릿·키 회전 뒤 자동 재기동 없음, GitOps 는 렌더마다 키 재생성(`templates/api.yaml:20`, `templates/secret.yaml:1-11`). (2) [위험:낮음] env 모드 키 회전의 롤링 구간 불일치(`templates/secret.yaml:10-13` + `templates/api.yaml:12`). (3) [위험:낮음] HA 프로파일은 TLS 라 readiness 개선 미적용(`ci/ha-values.yaml:12`), compose prod TLS 경로에 볼륨 없음(`docker-compose.prod.yml:18-19`).
- 개선 권고(영향순): (1) 회전·시크릿 교체 절차서(롤아웃 재시작, 2단계 키 배포, GitOps 는 existingSecret 필수). (2) TLS readiness 를 `exec` 프로브로. (3) compose TLS 볼륨 예시. (4) 설치 이미지 다이제스트 고정(교차 M11).
- 파일 위험 등급 / Risk grade: 중간 — 자체는 [위험:낮음]만이지만, 배포 진입점이고 교차 [위험:중간] 1건(M11)과 키 회전 운영 절차의 공백이 겹친다.
- 재읽기 권고 레벨: 3 — 설정 파일은 블록 단위로 충분하다. 키 회전 절차만 코드(`app/core/keys.py`)와 함께 따라갈 것.

## 2차 리뷰 대비 / vs 2nd Review
| 2차 INFRA.md 지적 | 3차 상태 | 근거(현재 path:line) |
|---|---|---|
| L84 a2a-grpc 준비성이 TCP 뿐 | 해결(평문) · 미적용(TLS — HA 프로파일) | `templates/a2a-grpc.yaml:47-54`, TLS 는 `:39-46` |
| L85 a2a-grpc PDB·존 분산 없음 | 해결 | `templates/scaling.yaml:28-39`, `templates/a2a-grpc.yaml:20-25` |
| L144 `checksum/config` 만 — Secret 교체가 재기동으로 이어지지 않음 | 해결(차트가 Secret 을 만들 때) · 공백(existingSecret) | `templates/api.yaml:20`, `templates/demo.yaml:21` |
| L46 a2a-grpc 에 OTLP env 미전달 | 해당 없음으로 종결(스팬을 만들지 않는 프로세스, `test/impl/54th/result.md`) | — |
| L350 잠금 파일이 없으면 조용히 비고정 설치 | 해결 | `Dockerfile:25-28`, `:42-43` |
| L351 e2e 베이스 digest 미고정 | 해결 | `Dockerfile:38` |
| L421 prod 오버레이가 엣지 노출 a2a-grpc 에도 `.env.prod` 전체 | 해결 | `docker-compose.prod.yml:14-20` · 시험 `test/test_deploy_hardening.py:41-50` |
