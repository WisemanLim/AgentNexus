# 보안 정책 / Security Policy

## 지원 버전 / Supported versions

| 버전 | 지원 |
|------|------|
| 최신 릴리스(`main`) | 보안 수정 제공 |
| 이전 릴리스 | 최신으로 업그레이드 후 확인 |

## 취약점 보고 / Reporting a vulnerability

- 공개 이슈로 올리지 말고 저장소의 **비공개 취약점 보고(GitHub Security Advisories → "Report a vulnerability")** 를 사용하세요.
  Please do not open a public issue; use the repository's private vulnerability reporting.
- 포함할 내용: 영향 받는 버전·구성(local/prod·Helm), 재현 절차, 영향(데이터·권한), 가능하면 수정 제안.
- 실제 자격 증명·개인정보·운영 데이터는 보고서에 넣지 마세요(가린 값으로).
- 가정: 접수 확인 3 영업일, 영향 평가 10 영업일 안 목표 — 운영 조직이 확정하면 이 줄을 바꾼다.

## 범위 / Scope

게이트웨이(MCP·A2A·Unified Model API)·내장 IdP·관리 API·콘솔·CLI(`nexus`)·Helm 차트·compose 구성.
데모 참조 서버(`app/builtin/*`)와 시험 코드는 운영 배포 대상이 아니다(운영은 `demo.enabled=false` 권장).

## 운영 시 기본 조치 / Hardening checklist

- `.env.prod` 의 `CHANGE_ME` 를 모두 실제 값(시크릿 관리자 주입)으로 — `make deploy` 는 남아 있으면 거부한다.
- TLS 는 Ingress(또는 메시)에서 종료, gRPC 바인딩은 `A2A_GRPC_TLS_CERT`·`A2A_GRPC_TLS_KEY` 또는 Helm `a2aGrpc.tls.secretName`.
- NetworkPolicy `ingressFrom` 으로 api·a2a-grpc 수신 출처를 Ingress 컨트롤러로 제한, 클러스터 egress 정책으로 내부망·메타데이터 접근 제한.
- 의존성·이미지 갱신은 Dependabot(`.github/dependabot.yml`) PR 과 CI(`.github/workflows/ci.yml`)의 감사 단계로 확인.
- Helm `api.forwardedAllowIps` 를 Ingress 컨트롤러 파드 CIDR 로 좁힌다(기본 `*` — 넓으면 다른 파드가 `X-Forwarded-For` 로 IP 허용 목록·감사 IP 를 속인다).
- 데모가 필요 없으면 `demo.enabled=false` — 데모 행(참조 MCP·데모 A2A·워크로드)도 시드하지 않는다.
- 워크플로 액션은 커밋 SHA 로 고정되어 있다(태그 주석) — 갱신은 Dependabot PR 로만.
- 에어갭 번들은 `cosign sign-blob --key … manifest.json > manifest.json.sig` 로 서명한다. 공개키는 번들과 **다른 경로**로 전달하고 `nexus bundle import --cosign-key <공개키>` 로 검증한다 — 번들 안의 키는 번들과 함께 바뀔 수 있어 쓰지 않는다(59th). `--allow-unsigned` 는 PoC 전용.
- `nexus install compose --cosign-key` 는 이미지를 레지스트리에서 받아(pull) 그 다이제스트로 서명을 검증하고, 빌드·다시 받기 없이 그 이미지로 기동한다(59th) — 레지스트리 이름이 아닌 로컬 빌드 태그는 검증할 수 없어 중단한다.

## 키 회전 / Key rotation (58th·59th)

서명 키(RS256 — 토큰·JWKS·백업·감사 보고서 서명)와 암호화 키(Fernet — 시크릿·MFA 비밀·로그 원문·A2A 웹훅 토큰)는
관리 명령으로 수동 회전한다. 플랫폼 관리자(admin 역할 + 토큰 스코프 `admin:write`)만, 확인 문구 `ROTATE`, 감사 `keys.rotate`·`keys.reencrypt`.

**파일 모드**(`SIGNING_KEY_PEM`·`ENCRYPTION_KEY` 비움 → `data/keys`):

1. `nexus keys status` — 현재 kid·JWKS kid·암호화 키 수.
2. `nexus keys rotate --kind signing --yes` — 새 kid. 직전 키는 서명 토큰 최대 수명 + 10분 동안 검증·JWKS 에 남고,
   공개키는 `signing.archive.pem` 에 영구 보관(백업·보고서 서명 검증). 같은 볼륨의 다른 워커·파드는 `keys/generation` 으로 즉시 반영.
3. `nexus keys rotate --kind encryption --yes` — 새 키로 바꾸고 저장 암호문을 즉시 재암호화(500행 배치). 결과 `failed` 가 0 인지 확인.
4. 10분 뒤(MCP 요청 상태 토큰 최대 수명) `nexus keys reencrypt --drop-previous` — 이전 암호화 키 제거.

**env 모드**(Secret 주입 — 앱이 회전하지 않음, API 는 409 `keys_env_managed`):

1. 새 키 생성(`openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048` · `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`).
2. **1단계 — 다음 키 배포**: `SIGNING_KEY_PEM_NEXT` = 새 키(공개키 PEM 도 됨), `ENCRYPTION_KEY_NEXT` = 새 키 → 재배포
   (Helm `existingSecret` 이면 `secrets.rotationNonce` 값도 바꿔야 파드가 새 값을 읽는다 — 이 단계와 아래 3·5단계 모두).
   모든 파드가 새 kid 를 JWKS·검증에, 새 암호화 키를 복호화에 쓴다(아직 서명·암호화는 옛 키) (62nd).
3. **2단계 — 올림**: `SIGNING_KEY_PEM` = 새 키, `SIGNING_KEY_PEM_PREVIOUS` = 이전 키(개인·공개 PEM, 여러 개 이어 붙임),
   `ENCRYPTION_KEY` = 새 키, `ENCRYPTION_KEY_PREVIOUS` = 이전 키(쉼표), `*_NEXT` 비움 → 재배포. 1단계 덕분에 롤링 중
   옛·새 파드가 서로의 토큰·암호문을 받는다. Helm `existingSecret` 이면 체크섬이 그대로이므로 `secrets.rotationNonce` 값을 바꿔 재기동.
4. 롤아웃이 끝난 뒤 `nexus keys reencrypt` → `failed` 0·`key_fingerprint` 가 새 키인지 확인. 아직 다음 키가 남은 1단계 파드는
   409 `rotation_in_progress` 로 거부한다(옛 키로 다시 쓰지 않게, 65th).
5. `SIGNING_KEY_PREVIOUS_UNTIL`(ISO 8601) 에 서명 토큰 최대 수명이 지나는 시각을 넣는다 — 그 뒤 이전 키는 토큰 검증·JWKS 에서
   빠지고 백업·보고서 서명 검증에만 남는다. 이전 암호화 키는 재암호화 실패 0 확인 뒤 비우고 재배포.
6. 키가 유출됐으면 그 kid 를 `SIGNING_REVOKED_KIDS`(쉼표)에 — 토큰·백업·보고서 어디서도 검증하지 않는다(파일 모드 보관본 포함).
   **현재 키**를 폐기하려면 먼저 위 절차로 회전한다 — 현재 kid 가 목록에 있으면 키 적재가 실패해 서비스가 멈춘다(유출 키로 계속
   통과시키지 않기 위한 의도된 동작, 65th).

한도·예약의 Lua 스크립트는 서버 시계(`TIME`)를 쓴다 — Valkey 또는 Redis 7 이상(스크립트 효과 복제).

검토 기록: `.review/`(위임 보안·결함 리뷰, 심층 리뷰, 라인 리뷰) · 수정 판정 `test/impl/39th/scenario.md`.
2차 검토: `.review/round2/`(위임 리뷰·Lv2 라인 리뷰) · 수정 판정 `.review/round2/VERIFY.md` · 시험 `test/impl/51st`.
3차 검토: `.review/round3/`(위임 리뷰·Lv2 라인 리뷰, D20·D21 변경분) · 수정 판정 `.review/round3/VERIFY.md` · 시험 `test/impl/59th`.
4차 검토: `.review/round4/`(위임 리뷰·Lv2 라인 리뷰, D22 변경분) · 수정 판정 `.review/round4/VERIFY.md` · 시험 `test/impl/65th`.
