> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 4차 리뷰(round4, D22 hunk 한정) · 스냅샷 2026-10-08 14:27 KST · [← INDEX.md](INDEX.md) · 3차 리뷰: [round3 INFRA.md](../../round3/CODE-REVIEW-Lv2/INFRA.md)

# 11. 배포·설정 요약 리뷰 / Deploy · Config (D22 hunk)

대상: `deploy/helm/agentnexus/templates/secret.yaml`(35) · `templates/configmap.yaml`(51) · `templates/api.yaml`(122) · `templates/demo.yaml`(70) · `templates/a2a-grpc.yaml`(108) · `values.yaml`(185) · `ci/ha-values.yaml`(13) · `deploy/helm/check-rendered.py`(130) · `deploy/compose/a2a-grpc-tls.example.yml`(14, 신규) · `.env.example`(88)·`.env.local`(82)·`.env.prod`(89, 비커밋) · `.gitignore`(125)
근거 확인을 위해 함께 읽은 파일: `docker-compose.yml`·`docker-compose.prod.yml`·`Dockerfile`·`SECURITY.md`·`README.md:251-253`·`app/core/keys.py`·`app/core/config.py`

YAML·env 라 줄마다 짚지 않고 **논리 블록 단위**로 본다.

**파일 요약 / File Summary**
- 역할 / Role: Helm 차트(키 Secret·ConfigMap·Deployment 주석·프로브), compose TLS 오버레이 예시, env 템플릿, 렌더 불변식 검사.
- 진입점 거리 / Distance: 0 — 운영자가 직접 쓰는 배포 진입점(`helm install/upgrade`, `docker compose up`).
- 4차 변경 요약: D22-① ConfigMap `DB_*_TIMEOUT_MS`·env 3곳 · D22-② `secrets.signingKeyPemNext`·`encryptionKeyNext` → Secret, `config.signingKeyPreviousUntil`·`signingRevokedKids` → ConfigMap, `secrets.rotationNonce` → api·데모 파드 주석, helm-check 불변식(existingSecret 이면 nonce 필수) · D22-③ `MODEL_MAX_INFLIGHT_PER_SUBJECT` · D22-⑥ a2a-grpc readiness `failureThreshold: 6`, compose TLS 오버레이 예시, `.gitignore certs/`.
- 테스트 커버리지 흔적: `test/test_key_mgmt.py:138`(C62-06 값·템플릿·불변식), `test/test_remaining_items2.py:96`(C66-04 failureThreshold·오버레이·`.gitignore`), `make helm-check` 4 시나리오(`test/impl/62nd/result.md`, `66th`).

## 블록 리뷰 / Block Walk (Level 2)

### (1) 키 Secret — `templates/secret.yaml`

```yaml path=deploy/helm/agentnexus/templates/secret.yaml lines=1-22
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
{{- with $s.signingKeyPemNext }}{{- $_ := set $keep "SIGNING_KEY_PEM_NEXT" . -}}{{- end }}
{{- with $s.encryptionKeyNext }}{{- $_ := set $keep "ENCRYPTION_KEY_NEXT" . -}}{{- end }}
{{- if .Values.postgresql.enabled }}
{{- $pw := .Values.postgresql.password | default (get $old "DB_PASSWORD" | default "" | b64dec) | default (randAlphaNum 24) -}}
{{- $_ := set $keep "DB_PASSWORD" $pw -}}
{{- $_ := set $keep "DATABASE_URL" (printf "postgresql+psycopg://%s:%s@%s-postgresql:5432/%s" .Values.postgresql.username $pw $fn .Values.postgresql.database) -}}
{{- else }}
{{- $_ := set $keep "DATABASE_URL" .Values.database.url -}}
{{- end }}
```
**분석 / Analysis (Level 2)**
- L1: `existingSecret` 이면 이 Secret 을 렌더하지 않는다(운영자가 외부 Secret 을 관리).
- L4: `lookup` — 클러스터에 이미 있는 Secret 을 읽어 **업그레이드 때 키를 보존**한다. `helm template`(GitOps)에서는 `lookup` 이 빈 값이라 매 렌더마다 새 키가 생긴다(3차 L27 — 기록 유지).
- L10-11: 현재 서명·암호화 키는 values → 기존 Secret → 새로 생성 순.
- L12-15(D21-③·D22-②): 이전·다음 키는 `with` — **values 에 있을 때만** Secret 에 넣는다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] **L6** 이전 서명 키(`SIGNING_KEY_PEM_PREVIOUS`)는 env 모드에서 **백업·감사 보고서 서명 검증의 유일한 보관처**다(`app/core/keys.py:164`). 그런데 L12 는 `lookup` 으로 보존하지 않으므로, 회전 때 `--set-file secrets.signingKeyPemPrevious=…` 로 넣었다가 다음 `helm upgrade` 에서 그 인자를 빠뜨리면 조용히 사라진다 → 그 키로 서명한 백업 복원이 "알 수 없는 서명 키(kid)" 로 거부. values 주석도 "토큰 수명 뒤 비움"(`values.yaml:81`)이라 지우라고 안내한다(SECURITY.md:57-58 은 남기라고 함). 최소 수정: L12 를 L10 처럼 `default (get $old …)` 로 보존, 주석 정정. 다음 키(L14-15)는 2단계에서 **비워야** 하므로 지금처럼 `with` 가 맞다.

### (2) ConfigMap — `templates/configmap.yaml`

```yaml path=deploy/helm/agentnexus/templates/configmap.yaml lines=28-35
  SCHEDULER_INTERVAL_S: {{ .Values.config.schedulerIntervalS | quote }}
  DB_STATEMENT_TIMEOUT_MS: {{ .Values.config.dbStatementTimeoutMs | quote }}
  DB_LOCK_TIMEOUT_MS: {{ .Values.config.dbLockTimeoutMs | quote }}
  DB_IDLE_TX_TIMEOUT_MS: {{ .Values.config.dbIdleTxTimeoutMs | quote }}
  SIGNING_KEY_PREVIOUS_UNTIL: {{ .Values.config.signingKeyPreviousUntil | quote }}
  SIGNING_REVOKED_KIDS: {{ .Values.config.signingRevokedKids | quote }}
  MODEL_MAX_INFLIGHT_PER_SUBJECT: {{ .Values.config.modelMaxInflightPerSubject | quote }}
  RETENTION_INTERVAL_S: {{ .Values.config.retentionIntervalS | quote }}
```
**분석 / Analysis (Level 2)**
- L29-34(D22-①②③): 비밀이 아닌 값은 ConfigMap 으로 — kid 는 공개 정보라 `SIGNING_REVOKED_KIDS` 를 ConfigMap 에 두어도 된다. ConfigMap 은 `checksum/config`(`templates/api.yaml:19`)로 바뀌면 api 파드가 재기동하므로 `SIGNING_KEY_PREVIOUS_UNTIL`·폐기 kid 변경은 별도 조치 없이 반영된다.
- 이 ConfigMap 을 마이그레이션 initContainer 도 읽는다(`templates/api.yaml:36-38`) — 엔진이 세션 상한을 갖지만 `migrations/env.py:31-32` 가 트랜잭션 안에서 끈다.

### (3) 재기동 nonce — `templates/api.yaml`·`templates/demo.yaml`·`check-rendered.py`·`ci/ha-values.yaml`

```yaml path=deploy/helm/agentnexus/templates/api.yaml lines=18-24
      annotations:
        checksum/config: {{ include (print $.Template.BasePath "/configmap.yaml") . | sha256sum }}
        checksum/secret: {{ include (print $.Template.BasePath "/secret.yaml") . | sha256sum }}  # 시크릿 교체도 재기동 (D20-③)
        {{- with .Values.secrets.rotationNonce }}
        agentnexus/rotation-nonce: {{ . | quote }}  # existingSecret 키 교체 — 값을 바꿔 재기동 (D22-②)
        {{- end }}
        {{- with .Values.api.podAnnotations }}{{ toYaml . | nindent 8 }}{{- end }}
```
```yaml path=deploy/helm/agentnexus/templates/demo.yaml lines=20-24
      annotations:
        checksum/secret: {{ include (print $root.Template.BasePath "/secret.yaml") $root | sha256sum }}  # 데모 워크로드 시크릿 교체 시 재기동 (D20-③)
        {{- with $root.Values.secrets.rotationNonce }}
        agentnexus/rotation-nonce: {{ . | quote }}
        {{- end }}
```
```python path=deploy/helm/check-rendered.py lines=118-123
    if name.endswith("-api") and by_kind.get("Secret") and \
            "checksum/secret" not in (w["spec"]["template"]["metadata"].get("annotations") or {}):
        errors.append(f"Deployment/{name}: checksum/secret 없음 — 시크릿 교체가 재기동으로 이어지지 않음")
    if name.endswith("-api") and not by_kind.get("Secret") and \
            "agentnexus/rotation-nonce" not in (w["spec"]["template"]["metadata"].get("annotations") or {}):
        errors.append(f"Deployment/{name}: existingSecret 인데 rotation-nonce 없음 — 키 교체 뒤 재기동 수단 없음 (ci/ha)")
```
```yaml path=deploy/helm/agentnexus/ci/ha-values.yaml lines=6-6
secrets: { existingSecret: agentnexus-prod, rotationNonce: "2026-10-08-1" }  # 외부 Secret 키 교체 뒤 값을 바꿔 재기동
```
**분석 / Analysis (Level 2)**
- `existingSecret` 이면 차트 Secret 이 렌더되지 않아 `checksum/secret` 이 상수다 — 외부 Secret 의 키를 바꿔도 파드가 재기동하지 않는다(3차 L27). `rotationNonce` 값을 바꾸면 파드 템플릿 주석이 바뀌어 롤링 재기동이 일어난다(D22-②).
- helm-check(L121-123): `existingSecret`(렌더된 Secret 없음)인데 api 에 nonce 주석이 없으면 실패 — HA 참조 프로파일(`ci/ha-values.yaml:6`)이 nonce 를 갖게 강제한다. 판정이 "렌더된 Secret 이 하나도 없음" 이라 `postgresql.enabled=false` + existingSecret 조합에서만 정확하다(차트 Secret 은 PG 비밀번호도 담으므로 현재 구조에서는 맞다).
- a2a-grpc 는 Secret 을 읽지 않아(ConfigMap 키만, `templates/a2a-grpc.yaml:33-36`) nonce 가 필요 없다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] **L7** env 2단계 회전은 **두 번 모두** 파드가 새 env 를 읽어야 성립한다. 절차는 2단계에만 "existingSecret 이면 nonce 를 바꿔 재기동"(`SECURITY.md:55`)이라 적고, 1단계는 "→ 재배포."(`SECURITY.md:51`)뿐이다. existingSecret 에서 1단계에 nonce 를 안 바꾸면 `helm upgrade` 가 같은 파드 템플릿을 만들어 **아무 파드도 다음 키를 읽지 않는다** → 2단계 롤링 중 옛 파드가 새 kid 토큰을 401, 새 키 암호문을 복호화 실패 — D22-② 가 없애려던 3차 L7 이 권장 경로(existingSecret)에서 그대로 재현된다. 이전 키를 지우는 마지막 재배포도 마찬가지. 최소 수정: SECURITY.md 1단계·5단계에도 nonce 변경 명시(또는 `kubectl rollout restart deploy/<api>` 안내).

### (4) a2a-grpc readiness — `templates/a2a-grpc.yaml`

```yaml path=deploy/helm/agentnexus/templates/a2a-grpc.yaml lines=47-56
          {{- else }}  # grpc.health.v1 — readiness 는 위임 대상 api 의 /health 까지 (D20-③)
          readinessProbe:
            grpc: { port: 50051, service: readiness }
            periodSeconds: 10
            failureThreshold: 6  # DB 장애조치 순간(수십 초) 전 파드가 함께 NotReady 로 빠지지 않게 (D22-⑥)
          livenessProbe:
            grpc: { port: 50051 }
            initialDelaySeconds: 10
            periodSeconds: 20
          {{- end }}
```
**분석 / Analysis (Level 2)**
- L51(D22-⑥): readiness 실패 6회(10초 간격 → 약 60초) 뒤 NotReady. readiness 는 위임 대상 api `/health`(DB 포함)까지 보므로(D20-③ 결정), DB 장애조치 수십 초 동안 a2a-grpc 전 파드가 함께 빠지던 문제(3차 L29)를 완화한다. 앱 쪽 2초 결과 공유(`app/services/a2a_grpc.py:314-322`)와 합쳐 프로브 부하도 작다.
- 맞바꿈: api 가 실제로 죽었을 때도 a2a-grpc 가 최대 60초 Ready 로 남아 요청을 받는다(그 요청은 위임 실패로 오류). 의도된 선택.

### (5) values — `values.yaml`

```yaml path=deploy/helm/agentnexus/values.yaml lines=59-66
  mcpConsentTimeoutS: 60      # MCP sampling·elicitation 동의 대기 (무응답 자동 거부)
  schedulerIntervalS: 60      # 경보 평가·실행 복구 (리스로 1개 파드만)
  dbStatementTimeoutMs: 120000  # PostgreSQL 문장 상한 (0 = 끔, D22-①)
  dbLockTimeoutMs: 15000        # 잠금 대기 상한
  dbIdleTxTimeoutMs: 0          # 트랜잭션 연 채 쉬는 연결 종료 — 스트림 요청 때문에 기본 끔
  signingKeyPreviousUntil: ""   # env 이전 서명 키의 토큰 검증 끝(ISO 8601, D22-②)
  signingRevokedKids: ""        # 폐기 kid(쉼표) — 유출 키는 어디서도 검증하지 않음
  modelMaxInflightPerSubject: 32  # 주체별 동시 모델 요청 상한 (0 = 끔, D22-③)
```
```yaml path=deploy/helm/agentnexus/values.yaml lines=75-85
# 시크릿 — existingSecret 을 쓰면 아래 값은 무시. 비워 두면 설치 시 생성하고 업그레이드 때 유지(lookup).
secrets:
  existingSecret: ""
  bootstrapAdminPassword: ""
  signingKeyPem: ""
  encryptionKey: ""
  signingKeyPemPrevious: ""     # 키 회전 — 직전 서명 키 PEM (토큰 수명 뒤 비움, D21-③)
  encryptionKeyPrevious: ""     # 키 회전 — 직전 암호화 키(쉼표 구분, 재암호화 뒤 비움)
  signingKeyPemNext: ""         # 2단계 회전 — 다음 서명 키(먼저 배포: JWKS·검증만, D22-②)
  encryptionKeyNext: ""         # 2단계 회전 — 다음 암호화 키(복호화만)
  rotationNonce: ""             # existingSecret 의 키를 바꿨을 때 값을 바꾸면 api·데모 파드 재기동 (체크섬이 그대로라서)
```
**분석 / Analysis (Level 2)**
- L61-63: 문장 120초·잠금 15초·유휴 0 — `app/core/config.py:21-24` 기본값과 같다. 운영 영향은 [db.py.md](app/db.py.md) M2(보존·아카이브)·L14(0021)·L16(시드) 참고.
- L66: 진행 중 모델 요청 32 — 사용자 단위(L4, [llm.py.md](app/services/llm.py.md)).
- L81: 주석 "(토큰 수명 뒤 비움, D21-③)" — L6 와 같이 D22-② 이후에는 틀린 안내.
- 참고: `config.rateLimitBackend: redis`(`values.yaml:52`)와 외부 Valkey(`ci/ha-values.yaml:5`)를 쓸 때, D22-③ Lua 가 `TIME` 뒤 쓰기를 하므로 Redis/Valkey **5 이상**이 전제다(차트 내장은 Valkey 8). 문서에 없음 — 등급 없음.

### (6) compose TLS 오버레이 — `deploy/compose/a2a-grpc-tls.example.yml`

```yaml path=deploy/compose/a2a-grpc-tls.example.yml lines=1-14
# a2a-grpc 직접 TLS 수신 — prod 오버레이 예시 (D22-⑥). `compose/` 가 아니라 여기에 둔다(Makefile 이 compose/*.yml 을 자동 포함).
# A2A_GRPC_TLS_CERT·KEY 는 컨테이너 안 파일 경로 — 인증서를 볼륨으로 넣어야 한다(경로만 주면 파일이 없어 재시작을 반복).
# 사용:
#   mkdir -p certs/a2a-grpc && cp tls.crt tls.key certs/a2a-grpc/   # 운영자가 준비한 인증서 (저장소에 커밋하지 않음)
#   docker compose -p wise-agentnexus-prod -f docker-compose.yml -f docker-compose.prod.yml \
#     -f compose/valkey.yml -f deploy/compose/a2a-grpc-tls.example.yml --env-file .env.prod up -d a2a-grpc
# TLS 수신이면 헬스 검사는 TCP 연결만 본다(`--probe` — gRPC 헬스는 TLS 를 말하지 못한다).
services:
  a2a-grpc:
    volumes:
      - ./certs/a2a-grpc:/certs:ro
    environment:
      A2A_GRPC_TLS_CERT: /certs/tls.crt
      A2A_GRPC_TLS_KEY: /certs/tls.key
```
```yaml path=docker-compose.prod.yml lines=14-19
  a2a-grpc:  # 엣지에 노출되는 상태 없는 위임 서비스 — .env.prod 전체(서명 키·DB 비밀번호 등) 대신 필요한 값만 (D20-③)
    environment:
      APP_ENV: ${APP_ENV:-prod}
      LOG_LEVEL: ${LOG_LEVEL:-INFO}
      A2A_GRPC_TLS_CERT: ${A2A_GRPC_TLS_CERT:-}
      A2A_GRPC_TLS_KEY: ${A2A_GRPC_TLS_KEY:-}
```
**분석 / Analysis (Level 2)**
- 3차 L28(경로만 넘기고 인증서 볼륨이 없어 재시작 반복)에 대한 예시. `compose/` 가 아니라 `deploy/compose/` 에 둔 이유(L1): Makefile 이 `compose/*.yml` 을 자동 포함하므로, 예시가 기본 기동에 섞이지 않게.
- compose 의 `environment` 맵은 파일 순서대로 **키 단위 병합** — prod 파일의 빈 `A2A_GRPC_TLS_CERT`·`KEY`(`docker-compose.prod.yml:18-19`)를 오버레이 값이 덮는다.
- 읽기 전용(`:ro`) 마운트, 저장소에 커밋 금지(`.gitignore:124-125`).
**짚어볼 점 / Watch-outs**
- [위험:낮음] **L18** 컨테이너는 uid 10001 로 돈다(`Dockerfile:55-56`). 운영자가 L4 대로 `cp tls.crt tls.key certs/a2a-grpc/` 하면 키 파일은 보통 호스트 사용자 소유 0600 이라 컨테이너가 **읽지 못한다** → 기동 실패·재시작 반복(예시가 막으려던 증상 그대로). 최소 수정: 예시 주석에 `chown 10001 certs/a2a-grpc/tls.key && chmod 0400 …`(또는 그룹 읽기) 추가, SELinux 호스트면 `:ro,z`.

### (7) env 템플릿 — `.env.example`(·`.env.local`·`.env.prod` 같은 키)

```bash path=.env.example lines=12-32
# PostgreSQL 세션 상한 ms (0 = 끔, D22-①) — 문장·잠금 대기. idle-in-transaction 은 스트림 요청 때문에 기본 끔
DB_STATEMENT_TIMEOUT_MS=120000
DB_LOCK_TIMEOUT_MS=15000
DB_IDLE_TX_TIMEOUT_MS=0
# 주체별 동시 모델 요청 상한 (0 = 끔, D22-③) — 큰 요청을 동시에 열어 팀 예산 예약을 잠그지 않게
MODEL_MAX_INFLIGHT_PER_SUBJECT=32

# ── Identity (Epic 2) — 내장 IdP issuer = PUBLIC_BASE_URL/idp. 키는 Secret Manager 로 주입 권장(CHANGE_ME=자동 생성)
# 운영 도메인(https://…)으로 교체 — 비시크릿, 리허설 기본값
PUBLIC_BASE_URL=http://127.0.0.1:8000
BOOTSTRAP_ADMIN_PASSWORD=CHANGE_ME
SIGNING_KEY_PEM=CHANGE_ME
ENCRYPTION_KEY=CHANGE_ME
# 키 회전(env 주입일 때) — 직전 키. 재암호화·토큰 만료 뒤 비운다 (선택, D21-③)
SIGNING_KEY_PEM_PREVIOUS=
ENCRYPTION_KEY_PREVIOUS=
# 2단계 회전(env, D22-②) — 다음 키를 먼저 배포(검증·복호화만) → 현재 키로 올림. 이전 서명 키 토큰 검증 끝(ISO 8601)·폐기 kid(쉼표)
SIGNING_KEY_PEM_NEXT=
ENCRYPTION_KEY_NEXT=
SIGNING_KEY_PREVIOUS_UNTIL=
SIGNING_REVOKED_KIDS=
```
**분석 / Analysis (Level 2)**
- D22 키 9개가 세 파일에 같은 이름으로 있다 — `.env.example` 과 `.env.prod` 의 키 집합이 같음을 확인했다(값은 `CHANGE_ME`·빈 값, 실제 시크릿 없음). `.env.local` 은 SQLite 라 `DB_*_TIMEOUT_MS` 가 무시된다(`app/db.py:52`).
- `_configured`(`app/core/keys.py:48-51`)가 `CHANGE_ME` 를 "미설정" 으로 보므로, 템플릿을 그대로 써도 키는 `data_dir/keys` 에 자동 생성된다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] **L6** L25 "직전 키. 재암호화·토큰 만료 뒤 비운다" 는 D21-③ 문구 그대로다. D22-② 이후 env 이전 **서명** 키는 토큰 검증 끝(`SIGNING_KEY_PREVIOUS_UNTIL`) 뒤에도 백업·보고서 검증용으로 남겨야 한다(`SECURITY.md:57-58`). 비우라는 것은 이전 **암호화** 키뿐이다. 세 파일 주석을 나눠 적어야 한다.

### (8) `.gitignore`

```bash path=.gitignore lines=1-10
# ===== common / 공통 (env·secrets·logs·editor) =====
.env
.env.*
!.env.example
*.local
# local 모드 env 는 시크릿 없음 → 커밋(CI 의 make test 용). .env.prod 는 비커밋 / commit .env.local, never .env.prod
!.env.local
*.pem
*.key
secrets.*
```
```bash path=.gitignore lines=124-125
# a2a-grpc TLS 인증서 — deploy/compose/a2a-grpc-tls.example.yml (D22-⑥, 커밋 금지)
certs/
```
**분석 / Analysis (Level 2)**
- 키 파일(`*.key`·`*.pem`)은 이미 무시되고, L125 `certs/` 가 인증서(`tls.crt`)까지 막는다. 앞에 `/` 가 없어 **어느 깊이의** `certs/` 디렉터리든 무시한다 — 지금 저장소에 `certs` 디렉터리는 없어 영향 없음(시험 픽스처를 `certs/` 로 만들면 조용히 빠진다는 점만 기억, 등급 없음).

## 파일 평가 / File Assessment
- 강점: (1) existingSecret 재기동 수단(nonce)과 그것을 강제하는 렌더 불변식(`deploy/helm/check-rendered.py:121-123`). (2) 다음 키·폐기 kid·검증 끝이 Secret/ConfigMap 으로 성격에 맞게 나뉨(`templates/secret.yaml:14-15`, `templates/configmap.yaml:32-33`). (3) TLS 오버레이를 자동 포함 경로 밖에 둔 배치(`deploy/compose/a2a-grpc-tls.example.yml:1`).
- 위험: (1) [위험:낮음] L7 — existingSecret 1단계 nonce 누락 시 2단계 패턴 무력화(`SECURITY.md:51`). (2) [위험:낮음] L6 — 이전 서명 키 보존·안내 불일치(`templates/secret.yaml:12`, `values.yaml:81`, `.env.example:25`). (3) [위험:낮음] L18 — TLS 키 파일 소유자.
- 개선 권고(영향순): (1) 회전 절차 모든 단계에 재기동 명시. (2) 이전 서명 키 `lookup` 보존·주석 정정. (3) 오버레이 권한 안내.
- 파일 위험 등급 / Risk grade: **낮음** — [위험:낮음]만(운영 절차 공백 위주).
- 재읽기 권고 레벨: 3 — 블록 단위로 충분. 키 회전을 실제로 수행하기 전에는 `SECURITY.md:48-59` 와 이 문서 (1)·(3)을 함께 읽을 것.

## 3차 리뷰 대비 / vs 3rd Review
| 3차 지적 | 4차 상태 | 근거(현재 path:line) |
|---|---|---|
| L7 env 회전 롤링 중 옛 파드 | 코드·차트 해결 · 1단계 nonce 문서(L7) | `templates/secret.yaml:14-15`, `SECURITY.md:51-55` |
| L27 existingSecret 체크섬 상수 | 해결(nonce + 불변식) · GitOps 키 재생성은 기록 유지 | `templates/api.yaml:21-23`, `check-rendered.py:121-123` |
| L28 compose a2a-grpc TLS 볼륨 | 예시 추가 · 파일 소유자(L18) | `deploy/compose/a2a-grpc-tls.example.yml:8-14` |
| L29 readiness 가 DB 장애조치에 함께 빠짐 | 완화(`failureThreshold: 6`) | `templates/a2a-grpc.yaml:51` |
| (D21-⑤ 기록) DB 세션 상한 배포 값 | 반영 | `templates/configmap.yaml:29-31`, `values.yaml:61-63` |
