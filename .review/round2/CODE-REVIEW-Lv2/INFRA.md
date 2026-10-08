> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 2차 리뷰(round2) · 스냅샷 2026-10-07 21:03 KST · [← INDEX.md](INDEX.md) · 1차 리뷰: 대상 아님(1차 INDEX 부록 A — `deploy/helm/agentnexus/templates/*`·`Dockerfile`·`.github/workflows/ci.yml` 은 표에만 등장)

# 32. 배포·빌드·CI 요약 리뷰 / Deploy · Build · CI (short section)

대상(7개, 605줄): `deploy/helm/agentnexus/templates/a2a-grpc.yaml`(91) · `deploy/helm/agentnexus/templates/api.yaml`(118) · `deploy/helm/agentnexus/templates/networkpolicy.yaml`(55) · `deploy/helm/agentnexus/values.yaml`(172) · `Dockerfile`(55) · `docker-compose.yml`(82) · `.github/workflows/ci.yml`(32).
근거 확인을 위해 함께 읽은 파일: `templates/configmap.yaml`·`templates/secret.yaml`·`templates/_helpers.tpl`·`templates/demo.yaml`·`templates/exposure.yaml`·`ci/ha-values.yaml`·`ci/airgap-values.yaml`·`scripts/entrypoint.sh`·`.dockerignore`·`docker-compose.prod.yml`·`Makefile`·`app/seed.py`·`app/api/idp.py`·`app/core/config.py`·`app/services/orchestrator.py`·`app/builtin/{a2a_agent,mcp_auth}.py`·`test/impl/41st/cluster-grpc.sh`.

YAML·Dockerfile 이라 줄마다 짚지 않고 **논리 블록 단위**로 본다(설정 파일 — Level 2 독자가 "이 블록이 무엇을 하고 무엇을 깨뜨릴 수 있는가"를 알 수 있게).

**파일 요약 / File Summary**
- 역할 / Role: Kubernetes 배포(Helm 차트 — api·a2a-grpc Deployment/Service/Ingress, NetworkPolicy, 기본 values), 컨테이너 이미지 빌드(멀티스테이지), compose 공통 정의, GitHub Actions CI.
- 진입점 거리 / Distance: 0 — 운영자가 직접 쓰는 배포 진입점(`helm install`, `docker compose up`, CI 트리거).
- 2차 변경 요약 / Changes since 1st review (7~10차): a2a-grpc Helm 템플릿 신설·TLS·Ingress(Epic 41) · `/tmp` emptyDir(41st r2 의 읽기 전용 루트 결함 수정) · api NetworkPolicy 에 a2a-grpc 자동 허용 · 존 분산 `topologySpreadConstraints`(Epic 46) · liveness 를 tcpSocket 으로(DB 장애 시 재시작 폭주 방지, Epic 46) · 베이스 이미지 digest 고정·uv 버전 고정(Epic 42) · CI 액션 SHA 고정·`permissions: contents: read`·`audit` 잡(Epic 42·50) · compose 데모 서비스 `UVICORN_WORKERS=1`(39th).
- 부수효과: 클러스터 리소스 생성·시크릿 생성(`secret.yaml` 이 `lookup` 으로 기존 값 유지), 이미지 빌드 시 네트워크(의존성 설치).
- 테스트 커버리지 흔적: `test/test_deploy.py`(차트 ↔ 앱 env 정합), `make helm-check`(`deploy/helm/check-rendered.py` — 렌더 결과 오프라인 검사), kind 시나리오 `test/impl/41st/cluster-grpc.sh`(20/20)·`test/impl/46th`(CNPG 장애조치 15/15).

## 블록 리뷰 / Block Walk (Level 2)

### (1) `deploy/helm/agentnexus/templates/a2a-grpc.yaml`

```yaml path=deploy/helm/agentnexus/templates/a2a-grpc.yaml lines=1-30
{{- if .Values.a2aGrpc.enabled }}
{{- $fn := include "agentnexus.fullname" . -}}
{{- $tls := not (empty .Values.a2aGrpc.tls.secretName) -}}
# A2A gRPC 바인딩 (lf.a2a.v1.A2AService) — 상태 없음: RPC 를 api 의 HTTP+JSON 바인딩으로 위임 (인증·정책·감사는 api)
apiVersion: apps/v1
kind: Deployment
...
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
```
**분석 / Analysis (Level 2)**
- L1-3: `a2aGrpc.enabled` 일 때만 렌더. `$tls` 는 TLS Secret 이름 유무로 파드 직접 TLS 여부를 정한다 — 이 한 값이 볼륨 마운트(L44-46·L49-52), Ingress backend-protocol(L71)을 함께 바꾼다.
- L24: `args` 가 이미지 ENTRYPOINT(`sh /app/scripts/entrypoint.sh`)의 인자로 들어간다. 진입점은 `AGENTNEXUS_ROLE` 이 `api` 가 아니므로 마이그레이션을 건너뛰고 `exec "$@"` (`scripts/entrypoint.sh:7-11`).
- L25-30: **최소 권한 설계** — `envFrom` 으로 ConfigMap·Secret 전체를 받지 않고 5개 키만 받는다. a2a-grpc 는 상태가 없고 RPC 를 `GATEWAY_URL` 의 REST 바인딩으로 위임하므로(`app/grpc_server.py:1-4`) DB 비밀번호·서명 키가 필요 없다. 흔한 실수는 편의상 `envFrom: secretRef` 를 붙여 외부 노출 파드에 모든 시크릿을 주는 것 — 이 차트는 피했다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L27: 키 목록이 고정이라 `api.extraEnv`(예: `OTLP_ENDPOINT`, `values.yaml:28`)와 `SPAN_PROCESSOR` 가 a2a-grpc 파드에 전달되지 않는다. gRPC 수신 파드의 트레이스가 외부 Collector 로 나가지 않는다(관측 공백). gRPC 상한은 코드 상수(`app/services/a2a_grpc.py:261-263`)라 env 누락의 영향은 없다. 수정: 관측 키(`OTLP_ENDPOINT`·`SPAN_PROCESSOR`)를 목록에 추가하거나 `a2aGrpc.extraEnv` 도입.

```yaml path=deploy/helm/agentnexus/templates/a2a-grpc.yaml lines=31-62
          ports:
            - { name: grpc, containerPort: 50051 }
          readinessProbe:
            tcpSocket: { port: grpc }
            periodSeconds: 10
          livenessProbe:
            tcpSocket: { port: grpc }
            initialDelaySeconds: 10
            periodSeconds: 20
          resources: {{- toYaml .Values.a2aGrpc.resources | nindent 12 }}
          securityContext: {{- toYaml .Values.securityContext | nindent 12 }}
          volumeMounts:
            - { name: tmp, mountPath: /tmp }  # 읽기 전용 루트 — 진입점의 메트릭 디렉터리(/tmp/prometheus)
            {{- if $tls }}
            - { name: tls, mountPath: /tls, readOnly: true }
            {{- end }}
      volumes:
        - { name: tmp, emptyDir: {} }
        {{- if $tls }}
        - name: tls
          secret: { secretName: {{ .Values.a2aGrpc.tls.secretName }} }
        {{- end }}
---
apiVersion: v1
kind: Service
...
  ports:
    - { name: grpc, port: 50051, targetPort: grpc, appProtocol: grpc }
```
**분석 / Analysis (Level 2)**
- L33-39: 준비성·생존성 모두 TCP 연결만 확인. gRPC 서버가 포트를 열었는지만 본다.
- L43·L48: `readOnlyRootFilesystem: true`(`values.yaml:171`)에서 진입점의 `mkdir -p "$PROMETHEUS_MULTIPROC_DIR"`(`scripts/entrypoint.sh:4-6`, 이미지 ENV `/tmp/prometheus` — `Dockerfile:47`)가 실패하던 결함(41st r2)을 `/tmp` emptyDir 로 고쳤다. 읽기 전용 루트를 쓰는 차트에서 "쓰기 경로를 모두 emptyDir 로 열었는가"는 흔히 빠지는 점검 항목이다.
- L44-52: TLS Secret 을 `/tls` 에 읽기 전용으로 마운트 — ConfigMap 의 `A2A_GRPC_TLS_CERT=/tls/tls.crt`(`configmap.yaml:32-33`)와 경로 계약이 맞다.
- L62 `appProtocol: grpc`: 서비스 메시·Gateway API 가 HTTP/2 로 다루도록 알리는 힌트.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L33-35: 준비성이 TCP 뿐이라 a2a-grpc 가 `GATEWAY_URL`(api)에 닿지 못해도 Ready 로 남는다 — api 장애·NetworkPolicy 차단 시 클라이언트는 연결 후 모든 RPC 가 `UNAVAILABLE`. 수정: gRPC Health Checking 서비스(`grpc.health.v1`)를 노출하고 `grpc:` 프로브(K8s 1.27+) 사용, 또는 위임 대상 확인을 포함한 exec 프로브.
- [위험:낮음] `ha-values.yaml:12` 가 `replicas: 2` 를 쓰지만 a2a-grpc 에는 PDB·`topologySpreadConstraints` 가 없다(`scaling.yaml:16-27` 은 api 만). 노드 드레인 때 두 파드가 동시에 내려갈 수 있다.

```yaml path=deploy/helm/agentnexus/templates/a2a-grpc.yaml lines=63-91
{{- if .Values.a2aGrpc.ingress.enabled }}
---
apiVersion: networking.k8s.io/v1
kind: Ingress
...
  annotations:
    nginx.ingress.kubernetes.io/backend-protocol: {{ ternary "GRPCS" "GRPC" $tls }}
    {{- with .Values.a2aGrpc.ingress.annotations }}
    {{- toYaml . | nindent 4 }}
    {{- end }}
spec:
  {{- with .Values.a2aGrpc.ingress.className }}
  ingressClassName: {{ . }}
  {{- end }}
  {{- with .Values.a2aGrpc.ingress.tls }}
  tls: {{- toYaml . | nindent 4 }}
  {{- end }}
  rules:
    - host: {{ .Values.a2aGrpc.ingress.host | quote }}
```
**분석 / Analysis (Level 2)**
- L71: 파드가 TLS 면 Ingress→파드 구간도 TLS(`GRPCS`), 아니면 h2c(`GRPC`). ingress-nginx 전용 주석이라 다른 컨트롤러(kgateway 등)에서는 무시된다(values 주석 `values.yaml:117` 에 명시).
- L79-81: Ingress TLS 종료는 별도 `ingress.tls` 로 — 파드 TLS 와 독립.
**짚어볼 점 / Watch-outs**
- [위험:낮음] `ingress.tls` 를 비운 채 `ingress.enabled=true` 면 클라이언트↔Ingress 구간이 평문 gRPC 가 된다(Bearer 토큰 평문 전송). 차트 검증(`_helpers.tpl:50-63`)에 "ingress.enabled 인데 tls 비어 있음" 경고/거부가 없다. 수정: `agentnexus.validate` 에 `fail` 또는 NOTES 경고 추가.

### (2) `deploy/helm/agentnexus/templates/api.yaml`

```yaml path=deploy/helm/agentnexus/templates/api.yaml lines=1-38
{{- $fn := include "agentnexus.fullname" . -}}
apiVersion: apps/v1
kind: Deployment
...
  strategy: { type: RollingUpdate, rollingUpdate: { maxSurge: 1, maxUnavailable: 0 } }
...
      annotations:
        checksum/config: {{ include (print $.Template.BasePath "/configmap.yaml") . | sha256sum }}
...
      # 마이그레이션·시드는 initContainer 가 수행 (PostgreSQL advisory lock 으로 다중 파드 동시 기동에도 직렬화). DB 준비 전에는 재시도.
      initContainers:
        - name: migrate
          image: {{ include "agentnexus.image" (list . .Values.image) }}
          imagePullPolicy: {{ .Values.image.pullPolicy }}
          args: ["true"]
          env:
            - { name: AGENTNEXUS_ROLE, value: api }
            - { name: AUTO_MIGRATE, value: "true" }
          envFrom:
            - configMapRef: { name: {{ $fn }}-config }
            - secretRef: { name: {{ include "agentnexus.secretName" . }} }
```
**분석 / Analysis (Level 2)**
- L12: `maxUnavailable: 0` + `maxSurge: 1` — 새 파드가 Ready 가 된 뒤 이전 파드를 내린다(무중단 롤링).
- L19: ConfigMap 내용 해시를 파드 주석에 넣어 설정 변경 시 자동 롤링 — Helm 의 표준 관용구.
- L24-38: initContainer 가 `args: ["true"]` 로 진입점만 실행 → `AUTO_MIGRATE=true` 이므로 `alembic upgrade head` + `python -m app.seed` 후 `exec true`(`scripts/entrypoint.sh:7-11`). 다중 파드 동시 기동은 `pg_advisory_xact_lock(727274101)`(`migrations/env.py:30`)·`(727274102)`(`app/seed.py:376-377`)로 직렬화 — 주석의 주장과 코드가 일치한다. 본 컨테이너는 `AUTO_MIGRATE=false`(L46)로 중복 실행을 막는다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L19: 체크섬이 ConfigMap 만 덮는다. Secret(`ENCRYPTION_KEY`·`METRICS_TOKEN` 등) 교체는 파드를 다시 띄우지 않아, 운영자가 시크릿을 바꾼 뒤에도 이전 값으로 계속 동작한다. 수정: `checksum/secret` 주석 추가(existingSecret 이면 생략).
- [위험:낮음] L12 + `values.yaml:43` `accessMode: ReadWriteOnce` + `replicas: 1`: 롤링 때 새 파드가 다른 노드에 배치되면 RWO 볼륨 Multi-Attach 로 Pending, `maxUnavailable: 0` 때문에 이전 파드도 내려가지 않아 롤아웃이 시간 초과까지 멈춘다. 단일 복제본+RWO 조합이면 `strategy: Recreate` 를 쓰거나 같은 노드 affinity 를 안내.

```yaml path=deploy/helm/agentnexus/templates/api.yaml lines=39-66
      containers:
        - name: api
          image: {{ include "agentnexus.image" (list . .Values.image) }}
          imagePullPolicy: {{ .Values.image.pullPolicy }}
          args: ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "{{ .Values.api.workers }}", "--proxy-headers", "--forwarded-allow-ips", "*"]
...
          readinessProbe:
            httpGet: { path: /health, port: http }
            periodSeconds: 10
            timeoutSeconds: 3
          livenessProbe:  # 프로세스 생존만 — /health 는 DB 를 확인하므로 DB 장애 때 api 파드를 재시작시키지 않게 (준비성에만 사용)
            tcpSocket: { port: http }
```
**분석 / Analysis (Level 2)**
- L43: uvicorn 다중 워커(`api.workers`) + 프록시 헤더 신뢰. `--forwarded-allow-ips "*"` 는 **어느 출발지의** `X-Forwarded-For`/`X-Forwarded-Proto` 든 믿는다는 뜻이다.
- L52-61: 준비성 = `/health`(DB `SELECT 1` 포함), 생존성 = TCP(Epic 46 결정). DB 장애 때 파드를 재시작하지 않고 트래픽에서만 뺀다 — 재시작 폭주(crash loop)를 피하는 올바른 분리.
**짚어볼 점 / Watch-outs**
- [위험:중간] L43 `--forwarded-allow-ips "*"`: uvicorn `ProxyHeadersMiddleware` 는 신뢰 범위가 `*` 이면 `X-Forwarded-For` 의 **가장 왼쪽 값**(클라이언트가 보낸 값)을 `request.client.host` 로 쓴다(외부 근거: uvicorn 0.42 설치본 `uvicorn/middleware/proxy_headers.py:132-133` `if self.always_trust: return x_forwarded_for_hosts[0]` — 락 버전 0.54.0(`uv.lock:2272-2273`)은 오프라인이라 같은 로직으로 추정). 앱은 이 값을 **로그인 허용 IP 정책**에 쓴다(`app/api/idp.py:262-263` `ip = request.client.host …; if not identity.ip_allowed(db, ip)`). 시나리오: (a) 기본 `networkPolicy.ingressFrom: []`(`values.yaml:145`)이면 클러스터 안 아무 파드나 api Service 에 직접 `X-Forwarded-For: <허용 IP>` 를 붙여 IP 정책을 통과하고, 감사 로그의 `ip` 도 위조된다. (b) XFF 를 덮어쓰지 않고 덧붙이는(append) 프록시 뒤에서도 같다. 수정: `--forwarded-allow-ips` 를 Ingress 컨트롤러 파드 CIDR(또는 `values` 의 `api.forwardedAllowIps`)로 제한하고 기본값을 비신뢰로; 앱 쪽은 IP 정책 판정에 "신뢰 프록시 홉 수" 기준 오른쪽에서 N 번째 값을 쓰게 한다. 시험 공백: IP 정책 우회 시험 없음.

```yaml path=deploy/helm/agentnexus/templates/api.yaml lines=67-118
      volumes:
        - name: data
          {{- if .Values.persistence.enabled }}
          persistentVolumeClaim: { claimName: {{ .Values.persistence.existingClaim | default (printf "%s-data" $fn) }} }
          {{- else }}
          emptyDir: {}
          {{- end }}
...
      {{- with .Values.api.topologySpreadConstraints }}
      topologySpreadConstraints:
        {{- range . }}
        - {{- toYaml (merge (dict) . (dict "labelSelector" (dict "matchLabels" (include "agentnexus.selector" (list $ "api") | fromYaml)))) | nindent 10 }}
        {{- end }}
      {{- end }}
...
  annotations:
    helm.sh/resource-policy: keep
```
**분석 / Analysis (Level 2)**
- L81-86 (Epic 46): 사용자가 준 분산 제약마다 `labelSelector` 를 차트가 채운다. `merge (dict) . (...)` 는 사용자 값이 우선 — 사용자가 `labelSelector` 를 직접 주면 그 값이 유지된다(Sprig `merge` 는 왼쪽 우선).
- L109-110: PVC 에 `resource-policy: keep` — `helm uninstall` 해도 감사 아카이브 볼륨 보존.
- `persistence.enabled=false` 면 `emptyDir` — 파드 재시작마다 `/app/data`(감사 아카이브·첨부)가 사라진다(values 주석과 일치, 의도된 PoC 모드).

### (3) `deploy/helm/agentnexus/templates/networkpolicy.yaml`

```yaml path=deploy/helm/agentnexus/templates/networkpolicy.yaml lines=1-37
{{- if .Values.networkPolicy.enabled }}
{{- $fn := include "agentnexus.fullname" . -}}
# backend(참조 MCP·A2A·DB·Valkey·OPA) 는 api 파드에서만 수신 — compose 의 internal backend 네트워크와 같은 우회 차단 (FR-G00)
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: {{ $fn }}-backend
  labels: {{- include "agentnexus.labels" . | nindent 4 }}
spec:
  podSelector:
    matchExpressions:
      - { key: app.kubernetes.io/instance, operator: In, values: [{{ .Release.Name | quote }}] }
      - { key: app.kubernetes.io/component, operator: In, values: [mcp-servers, a2a-demo, postgresql, valkey, opa] }
  policyTypes: [Ingress]
  ingress:
    - from:
        - podSelector:
            matchLabels: {{- include "agentnexus.selector" (list . "api") | nindent 14 }}
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: {{ $fn }}-api
  labels: {{- include "agentnexus.labels" . | nindent 4 }}
spec:
  podSelector:
    matchLabels: {{- include "agentnexus.selector" (list . "api") | nindent 6 }}
  policyTypes: [Ingress]
  ingress:
    - ports: [{ port: 8000, protocol: TCP }]
      {{- if .Values.networkPolicy.ingressFrom }}
      from: {{- toYaml .Values.networkPolicy.ingressFrom | nindent 8 }}
        {{- if .Values.a2aGrpc.enabled }}
        - podSelector:  # a2a-grpc 는 RPC 를 api 의 REST 바인딩으로 위임한다
            matchLabels: {{- include "agentnexus.selector" (list . "a2a-grpc") | nindent 14 }}
        {{- end }}
      {{- end }}
```
**분석 / Analysis (Level 2)**
- L10-18: backend 계열(참조 MCP·데모 A2A·DB·Valkey·OPA) 수신을 같은 릴리스의 api 파드로 제한 — "게이트웨이를 우회해 업스트림에 직접 접근" 차단(FR-G00). compose 의 `backend: internal: true`(`docker-compose.yml:81-82`)와 같은 의도.
- L28-37: api 수신은 8000 포트만. `ingressFrom` 이 비면 `from` 이 생략되어 **모든 출발지 허용**(K8s 의미론: `from` 없는 규칙 = 전체). 비어 있지 않으면 지정 출처 + a2a-grpc(Epic 41)만.
- `policyTypes: [Ingress]` 만 — egress 는 제한하지 않는다(업스트림 SaaS 호출이 필요하므로 의도된 선택, SSRF 는 앱 egress 가드가 담당).
**짚어볼 점 / Watch-outs**
- [위험:중간] L31-36: `ingressFrom` 을 지정하면 **클러스터 내부 호출자가 자동으로 허용되지 않는다** — 차트는 a2a-grpc 만 덧붙인다. 그런데 api 로 들어오는 내부 호출이 셋 더 있다: ① api → api(오케스트레이터가 `GATEWAY_URL` = `http://<fn>-api:8000` 으로 게이트웨이 호출 — `app/services/orchestrator.py:62`, `configmap.yaml:14`; 복제본 ≥2 이면 다른 파드로 간다) ② 데모 MCP 서버의 JWKS 조회(`app/builtin/mcp_auth.py:24` → `IDP_INTERNAL_URL`) ③ 데모 A2A 에이전트의 토큰·MCP·모델 호출(`app/builtin/a2a_agent.py:71,85,99` → `GATEWAY_URL`). 차트 예시 `ci/ha-values.yaml:11` 은 `ingressFrom` 을 kgateway 네임스페이스 하나로 두고 `autoscaling.minReplicas: 2`·`demo` 기본 켜짐·`serviceMonitor` 켜짐이므로, 그대로 설치하면 채팅 실행의 게이트웨이 호출(다른 파드로 간 것)·데모 도구 호출·Prometheus 스크레이프(`exposure.yaml:54-55` `/metrics`)가 차단된다. 실증: 41st kind 시험이 이 문제로 r1 실패 후 시험 values 에 `api, mcp-servers, a2a-demo` 파드를 손으로 넣었다(`test/impl/41st/cluster-grpc.sh:123-125` 주석 "Ingress 만 열면 데모 에이전트(api 로 토큰)·시험 클라이언트(api 파드)도 막힌다"). 수정: `ingressFrom` 이 있을 때 같은 릴리스의 `api`·`mcp-servers`·`a2a-demo` 컴포넌트 podSelector 를 차트가 자동 추가하고, `metrics.serviceMonitor.enabled` 면 `metrics.namespaceSelector` 값을 받아 추가. `ha-values.yaml` 에 Prometheus·ingress-nginx 출처를 반영.

```yaml path=deploy/helm/agentnexus/templates/networkpolicy.yaml lines=38-55
{{- if .Values.a2aGrpc.enabled }}
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: {{ $fn }}-a2a-grpc
  labels: {{- include "agentnexus.labels" . | nindent 4 }}
spec:
  podSelector:
    matchLabels: {{- include "agentnexus.selector" (list . "a2a-grpc") | nindent 6 }}
  policyTypes: [Ingress]
  ingress:
    - ports: [{ port: 50051, protocol: TCP }]
      {{- with .Values.networkPolicy.ingressFrom }}
      from: {{- toYaml . | nindent 8 }}
      {{- end }}
{{- end }}
{{- end }}
```
**분석 / Analysis (Level 2)**
- a2a-grpc 수신을 50051 포트·`ingressFrom` 출처로 제한(비면 전체).
**짚어볼 점 / Watch-outs**
- [위험:낮음] HTTP 와 gRPC 가 **같은 `ingressFrom`** 을 공유한다. `ha-values.yaml:11-12` 는 HTTP 를 kgateway(`kgateway-system`)로, gRPC Ingress 는 `className: nginx` 로 두는데 출처 목록에는 kgateway 만 있다 → ingress-nginx → a2a-grpc 연결이 막혀 gRPC Ingress 가 동작하지 않는다(렌더 검사만 하는 `helm-check` 로는 드러나지 않음). 수정: `a2aGrpc.networkPolicy.ingressFrom` 을 분리하거나 ha 예시 값에 ingress-nginx 네임스페이스 추가.

### (4) `deploy/helm/agentnexus/values.yaml`

```yaml path=deploy/helm/agentnexus/values.yaml lines=48-76
config:
  logLevel: INFO
...
  mcpStdioAllowedCommands: ""
...
# 시크릿 — existingSecret 을 쓰면 아래 값은 무시. 비워 두면 설치 시 생성하고 업그레이드 때 유지(lookup).
secrets:
  existingSecret: ""
  bootstrapAdminPassword: ""
  signingKeyPem: ""
  encryptionKey: ""
  metricsToken: ""
  otlpIngestToken: ""
  a2aDemoClientSecret: ""
```
**분석 / Analysis (Level 2)**
- 시크릿 기본값은 모두 빈 문자열 — 하드코딩 시크릿 없음. 빈 값은 `secret.yaml:7-11` 이 `randAlphaNum 32`·`genPrivateKey "rsa"`·`randBytes 32` 로 생성하고, `lookup` 으로 기존 Secret 값을 유지한다(업그레이드 때 키가 바뀌어 기존 토큰·암호문이 깨지는 흔한 실수를 막음). `resource-policy: keep`(`secret.yaml:25`)으로 uninstall 후에도 보존.
- L64 `mcpStdioAllowedCommands: ""`: stdio MCP 기본 금지(허용 목록 비움) — 안전한 기본값.

```yaml path=deploy/helm/agentnexus/values.yaml lines=103-123
# 데모 참조 서버 (가상 Jira·Confluence·Dynatrace·Internal API MCP + 원격 A2A 관측 분석 에이전트)
demo:
  enabled: true
...
a2aGrpc:
  enabled: false
  replicas: 1
  publicUrl: ""               # Agent Card 에 광고할 주소 (예: grpcs://grpc.agentnexus.local:443 — 비우면 GRPC 인터페이스 없음)
  tls:
    secretName: ""            # kubernetes.io/tls Secret — 지정하면 파드가 직접 TLS 수신(A2A_GRPC_TLS_CERT·KEY), 비우면 평문(h2c)
```
**분석 / Analysis (Level 2)**
- L105 `demo.enabled: true`: 데모 Deployment(`demo.yaml`) 렌더 여부만 정한다.
**짚어볼 점 / Watch-outs**
- [위험:중간] `demo.enabled` 가 **앱의 데모 시드와 연결되어 있지 않다.** ConfigMap(`configmap.yaml:8-44`)에 `SEED_DEMO` 가 없고 앱 기본값은 `seed_demo: bool = True`(`app/core/config.py:72`), 시드의 MCP·A2A 데모 단계는 `seed_demo` 만 본다(`app/seed.py:140,165` — `app_env` 확인 없음). 따라서 에어갭 예시(`ci/airgap-values.yaml:5` `demo: { enabled: false }`)로 설치해도 initContainer 시드가 ① 존재하지 않는 `http://<fn>-a2a-demo:8200/` 를 가리키는 `trust_tier="trusted"` A2A 에이전트(`app/seed.py:179-181`) ② 같은 식의 데모 MCP 서버 행(`app/seed.py:157-159`) ③ `gateway:invoke` 스코프의 활성 워크로드 `a2a-demo`(`app/seed.py:188-192`, 시크릿은 `secret.yaml:7-8` 이 항상 생성하는 `A2A_DEMO_CLIENT_SECRET`)를 만든다. 결과: 운영 카탈로그에 죽은 서버·에이전트가 노출되고 헬스 루프가 계속 실패를 기록하며, 쓰이지 않는 상시 자격 증명(chat:use·models:read·catalog:read·data:declare — `app/core/auth.py:38`)이 남는다. 같은 네임스페이스에 그 이름의 Service 를 만들 수 있는 주체는 "trusted" 에이전트로 등록된 주소를 가로챌 수 있다(가능성). 수정: `configmap.yaml` 에 `SEED_DEMO: {{ .Values.demo.enabled | quote }}` 추가(compose prod 도 `.env.example` 에 `SEED_DEMO=false` 기본), 앱은 `app_env=prod` 이면 `seed_demo` 기본 False.
- [위험:낮음] L145 `ingressFrom: []` 기본 = api 전체 공개(클러스터 내부 전부). 위 (2)의 XFF 위조와 결합된다. 주석이 "지정 권장"이라 문서화는 되어 있다.

### (5) `Dockerfile`

```dockerfile path=Dockerfile lines=1-17
# wise-agentnexus — python-fastapi (multi-stage: dev = local 모드, runtime = prod 모드)
# 베이스 이미지는 digest 고정 — 갱신은 Dependabot(docker) PR (D17-③)
FROM python:3.12-slim@sha256:dddfd7e07f9d15aeeca61529320492139d21cac7f0070c00609243e51e4e0016 AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy PATH=/opt/venv/bin:$PATH PYTHONPATH=/app
RUN pip install --no-cache-dir uv==0.12.23
WORKDIR /app
RUN printf '#!/bin/sh\nPYTHONPATH=/app exec python -m app.cli "$@"\n' > /usr/local/bin/nexus && chmod +x /usr/local/bin/nexus
ENTRYPOINT ["sh", "/app/scripts/entrypoint.sh"]

# dev: 테스트/개발 의존성 포함, 소스는 local 모드에서 바인드 마운트
FROM base AS dev
COPY pyproject.toml uv.lock* ./
RUN uv sync --locked --no-install-project
COPY . .
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
```
**분석 / Analysis (Level 2)**
- L3: 베이스 이미지 digest 고정(Epic 42) — 태그가 같은 이름으로 다시 밀려도 빌드가 바뀌지 않는다. 갱신은 Dependabot(`.github/dependabot.yml`).
- L4-5: `PYTHONDONTWRITEBYTECODE` 로 읽기 전용 루트에서도 `.pyc` 쓰기 시도 없음. venv 를 `/opt/venv` 에 두어 `/app` 바인드 마운트(local)가 venv 를 가리지 않게 한다 — 흔한 실수(venv 를 소스 디렉터리에 두면 마운트에 덮임)를 피한 배치.
- L6: uv 버전 고정. L13-14: 의존성 레이어를 소스보다 먼저 복사해 캐시 효율. `--locked` 는 `uv.lock` 이 `pyproject.toml` 과 어긋나면 실패(재현성).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L6 `pip install uv==0.12.23` 은 버전만 고정, 해시(`--require-hashes`) 미고정 — PyPI 계정 탈취 시 같은 버전 재게시는 불가하지만 인덱스 미러 변조에는 취약. 공급망 기준을 맞추려면 `ghcr.io/astral-sh/uv:<ver>@sha256:…` 에서 `COPY --from` 으로 바이너리를 가져오는 방식이 일반적.

```dockerfile path=Dockerfile lines=19-43
FROM node:22-alpine@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402 AS web-dev
RUN npm install -g pnpm@10.12.1
WORKDIR /web
ARG PNPM_FROZEN=1
COPY web/package.json web/pnpm-lock.yaml* ./
RUN if [ "$PNPM_FROZEN" = 1 ] && [ -f pnpm-lock.yaml ]; then pnpm install --frozen-lockfile; else pnpm install --no-frozen-lockfile; fi \
    && cp pnpm-lock.yaml /tmp/pnpm-lock.resolved.yaml
...
FROM mcr.microsoft.com/playwright:v1.55.0-noble AS e2e
WORKDIR /e2e
ARG NPM_LOCKED=1
COPY web/e2e/package.json web/e2e/package-lock.json* ./
RUN if [ "$NPM_LOCKED" = 1 ] && [ -f package-lock.json ]; then npm ci --no-audit --no-fund; else npm install --no-audit --no-fund; fi \
```
**분석 / Analysis (Level 2)**
- L23-25: 기본은 잠금 파일 고정 설치, 잠금 파일이 없거나 `PNPM_FROZEN=0` 이면 풀어서 설치(잠금 갱신용 `make` 경로). 해석된 잠금을 `/tmp` 에 복사해 호스트로 꺼낼 수 있게 함.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L25·L40: 잠금 파일이 **없을 때 조용히** 비고정 설치로 내려간다 — `.dockerignore`·체크아웃 실수로 잠금이 빠지면 CI 빌드가 실패하지 않고 새 버전을 끌어온다. 수정: `PNPM_FROZEN=1` 인데 잠금이 없으면 `exit 1`.
- [위험:낮음] L36: e2e 베이스는 digest 미고정 — L2 주석("베이스 이미지는 digest 고정")과 어긋난다(42nd result 가 "오프라인 확인 불가"로 남긴 항목). e2e 는 운영 이미지에 들어가지 않으므로 영향은 시험 재현성에 한정.

```dockerfile path=Dockerfile lines=45-55
# runtime: 운영 의존성만 (uvicorn 다중 워커 → Prometheus multiprocess 집계)
FROM base AS runtime
ENV PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus UVICORN_WORKERS=2
COPY pyproject.toml uv.lock* ./
RUN uv sync --locked --no-install-project --no-dev
COPY . .
COPY --from=web-build /web/dist /app/web/dist
RUN useradd -r -u 10001 app && mkdir -p /app/data && chown -R app /app/data
USER app
EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers \"${UVICORN_WORKERS:-2}\""]
```
**분석 / Analysis (Level 2)**
- L50 `COPY . .`: 무엇이 이미지에 들어가는지는 `.dockerignore` 가 정한다 — `.env`·`.env.*`·`data/`·`.review/`·`test/`·`.github/` 제외(`.dockerignore:2-4,14,21,24`). 시크릿 파일(`.env.prod`)·키(`data/keys`)가 이미지에 구워지지 않는다(상시 점검 통과).
- L52-53: 비 root uid 10001, 쓰기 가능 경로는 `/app/data` 뿐 — 소스(`/app`)는 root 소유라 런타임 변조 불가. Helm `runAsUser: 10001`(`values.yaml:165`)과 일치.
- L55: `sh -c` + `exec` — 셸이 PID 1 로 남지 않고 uvicorn 이 SIGTERM 을 직접 받는다(흔한 실수: `exec` 없이 `sh -c` 로 띄워 graceful shutdown 이 안 됨). 이 CMD 에는 `--proxy-headers` 범위 지정이 없으므로 uvicorn 기본값(`127.0.0.1` 만 신뢰)이 적용된다 — Helm 의 `"*"` 와 대조.

### (6) `docker-compose.yml`

```yaml path=docker-compose.yml lines=1-23
# wise-agentnexus — 공통 정의 (모드별 오버레이와 함께 사용: make local-all | make prod-all)
...
x-app: &app
  build: { context: ., dockerfile: Dockerfile, target: runtime }
  image: wise-agentnexus-api:${APP_ENV:-local}
services:
  api:
    <<: *app
    ports: ["${BIND_HOST:-127.0.0.1}:${API_PORT:-8000}:8000"]
    volumes: ["appdata:/app/data"]
...
    networks: [edge, backend]
```
**분석 / Analysis (Level 2)**
- L4-6: YAML 앵커(`&app`)·병합 키(`<<:`)로 네 서비스가 같은 이미지·빌드를 공유.
- L10: 기본 바인드 `127.0.0.1` — 호스트 외부 노출은 `BIND_HOST` 를 명시해야 한다(안전한 기본값).
- L18: api 만 `edge`+`backend` 양쪽 — 게이트웨이를 거치지 않고는 backend 서비스에 닿지 못한다.

```yaml path=docker-compose.yml lines=24-82
  mcp-servers:
    <<: *app
    profiles: ["demo"]
...
      UVICORN_WORKERS: "1"  # 데모 서버는 세션·Task 를 프로세스 메모리에 둔다 — runtime 이미지 기본 2 워커면 요청이 엇갈린다
...
    volumes: ["appdata:/app/data:ro"]
...
  a2a-grpc:
    <<: *app
    command: ["python", "-m", "app.grpc_server"]
    ports: ["${BIND_HOST:-127.0.0.1}:${A2A_GRPC_PORT:-8851}:50051"]
    environment:
      AGENTNEXUS_ROLE: a2a-grpc
      GATEWAY_URL: http://api:8000
...
    networks: [edge]
...
networks:
  edge: {}
  backend:
    internal: true
```
**분석 / Analysis (Level 2)**
- L30·L48: 데모 서버는 상태를 메모리에 두므로 단일 워커 고정(39th 에서 발견한 결함 수정). 이 값이 빠지면 MCP 세션·A2A Task 가 워커 사이에서 엇갈린다.
- L33·L52: 데모 서버는 공유 볼륨을 읽기 전용으로(워크로드 자격 증명 파일·OTLP 토큰 읽기).
- L62-71: a2a-grpc 는 `edge` 만 — DB·backend 에 닿지 않는다(상태 없음과 일치).
**짚어볼 점 / Watch-outs**
- [위험:낮음] prod 오버레이가 모든 서비스에 `env_file: [".env.prod"]`(`docker-compose.prod.yml:4,9,12,15`)를 붙여, 외부에 포트를 여는 a2a-grpc 컨테이너도 `ENCRYPTION_KEY`·`SIGNING_KEY_PEM`·`DATABASE_URL` 을 환경변수로 받는다. Helm 은 5개 키만 주는데(위 (1)) compose 는 최소 권한이 아니다. a2a-grpc 프로세스가 침해되면 모든 시크릿이 노출된다. 수정: a2a-grpc 는 `.env.prod` 대신 필요한 키만 `environment:` 로.
- [위험:낮음] L65: a2a-grpc 는 TLS env(`A2A_GRPC_TLS_CERT/KEY`)가 비면 평문(h2c)으로 수신 — 기본 바인드가 127.0.0.1 이라 안전하지만, `BIND_HOST=0.0.0.0` 이면 Bearer 토큰이 평문으로 오간다. `make preflight`(`Makefile:38-44`)에 "외부 바인드 + TLS 없음" 경고가 없다.

### (7) `.github/workflows/ci.yml`

```yaml path=.github/workflows/ci.yml lines=1-32
name: ci
on:
  push: { branches: [main] }
  pull_request:
permissions:
  contents: read            # 최소 권한 — 쓰기 권한이 필요한 단계 없음 (D17-③)
concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true
# 액션은 커밋 SHA 고정(태그는 주석) — Dependabot(github-actions)이 SHA·주석을 함께 갱신 PR 로 올린다 (D19-③)
jobs:
  test:
    runs-on: ubuntu-latest
    timeout-minutes: 45
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262  # v4.4.0
      - run: test -f .env.prod || cp .env.example .env.prod
      - run: make preflight
      - run: make lint
      - run: make test
      - run: make test-pg
      - run: make web-test
      - uses: azure/setup-helm@1a275c3b69536ee54be43f2070a358922e12c8d4  # v4.3.1
      - run: make helm-check
      - run: docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.prod build
  audit:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262  # v4.4.0
      - run: test -f .env.prod || cp .env.example .env.prod
      - run: make audit
```
**분석 / Analysis (Level 2)**
- L2-4: `pull_request`(not `pull_request_target`) — 포크 PR 이 저장소 시크릿·쓰기 토큰을 얻지 못한다. L5-6 워크플로 토큰 읽기 전용.
- L7-9: 같은 ref 의 이전 실행 취소 — 러너 낭비 방지.
- L16·L23·L30: 액션 SHA 고정(Epic 50, 50th result 에 SHA ↔ 태그 대응 기록).
- L17: CI 에는 `.env.prod` 가 없으므로 템플릿 복사(값은 `CHANGE_ME`) — 빌드·시험용.
- L20-22: SQLite·PostgreSQL 양쪽 pytest + 콘솔 vitest. L24 Helm 렌더 검사. L25 prod 이미지 빌드 확인.
- L26-32: 의존성 취약점 감사 잡 분리 — `make audit` 은 `pip-audit`·`pnpm audit --audit-level high` 실패 시 잡 실패(`Makefile:63-65`, `|| true` 없음).
**짚어볼 점 / Watch-outs**
- [위험:낮음] Playwright e2e(77개, 매 게이트에서 수동 실행)가 CI 에 없다 — 콘솔 회귀는 사람이 `make e2e` 를 돌려야 잡힌다. 시험 공백.
- [위험:낮음] L25 빌드한 이미지에 대한 취약점·SBOM 스캔이 없다(`audit` 은 잠금 파일 기준). 베이스 이미지 OS 패키지 취약점은 Dependabot digest 갱신에만 의존.

## 파일 평가 / File Assessment
- 강점: (1) 최소 권한 — a2a-grpc 파드에 5개 키만, 비 root·읽기 전용 루트·`capabilities drop ALL`·SA 토큰 자동 마운트 끔(`exposure.yaml:104`). (2) 시크릿을 values 에 두지 않고 생성·`lookup` 유지·`resource-policy: keep`. (3) 공급망 — 베이스 digest·액션 SHA·`contents: read`·잠금 고정 설치·`audit` 잡.
- 위험: (1) [위험:중간] `networkpolicy.yaml:31-36` — `ingressFrom` 지정 시 내부 호출자(api↔api·데모·Prometheus) 차단, `ha-values.yaml:11` 예시가 그대로 해당. (2) [위험:중간] `api.yaml:43` `--forwarded-allow-ips "*"` → `X-Forwarded-For` 위조로 로그인 IP 정책(`app/api/idp.py:262-263`) 우회·감사 IP 위조. (3) [위험:중간] `configmap.yaml` 에 `SEED_DEMO` 누락 → `demo.enabled=false`(에어갭)에도 데모 trusted 에이전트·MCP 서버·상시 워크로드 자격 증명 시드.
- 개선 권고(영향순): (1) NetworkPolicy 에 같은 릴리스 내부 컴포넌트 자동 허용 + 메트릭 출처 값. (2) `forwardedAllowIps` 값 도입(기본 비신뢰) + 앱 IP 판정 기준 정리. (3) `SEED_DEMO` 를 `demo.enabled` 와 연결, 앱 prod 기본 False.
- 파일 위험 등급 / Risk grade: **중간** — 운영 배포 진입점에 [위험:중간] 3건(모두 설정 수준, 코드 결함과 결합 시 보안 영향 1건).
- 재읽기 권고 레벨: 3 — 차트·네트워크 정책은 K8s 의미론(`from` 생략 = 전체 허용, 정책 합집합) 이해가 필요하다.

## 1차 리뷰 대비 / vs 1st Review
| 1차 지적 (1차 보고서 위치·요지) | 2차 상태 | 근거(현재 path:line) |
|---|---|---|
| 1차 INDEX §1.3 — compose `backend` internal 네트워크로 FR-G00 우회 차단(긍정 관찰) | 유지 | `docker-compose.yml:81-82`, Helm 대응 `networkpolicy.yaml:4-18` |
| 1차 INDEX §4.6-5 — `app_env` 기본 `local`·`seed_demo` 기본 True(fail-open) | 부분 해결 | Helm 은 `APP_ENV: prod` 고정(`configmap.yaml:9`)이나 `SEED_DEMO` 미배선 — `app/core/config.py:17,72` 기본값 그대로 |
| 1차 INDEX §4.6-6 — 헬스 루프·A2A 감시기에 리스 적용 필요 | 해결(배포 관점) | 45th prod 스모크 P45-05 `lease.a2a_watch`·`lease.mcp_health` 한 워커만(`test/impl/45th/result.md`) — 코드 근거는 [app/main.py.md](app/main.py.md) |
| `.review/delegated/security-review.md` — 공급망·CI 관련 지적 없음(범위 밖) | 해당 없음 | — |
| 39th — 데모 A2A 2워커 메모리 Task 엇갈림 | 해결 | `docker-compose.yml:30,48` `UVICORN_WORKERS: "1"` |
| 41st r2 — 읽기 전용 루트에서 `/tmp/prometheus` 생성 실패 | 해결 | `a2a-grpc.yaml:43,48` |
