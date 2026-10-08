# impl 58th — 키 회전 (Epic 58 · D21-③ · 관리 명령으로 수동)

## 범위

| 항목 | 구현 |
|------|------|
| 서명 키 | `POST /api/v1/admin/keys/rotate {"kind":"signing","confirm":"ROTATE"}` — 새 kid, 이전 키들은 각자 유예(서명 토큰 최대 수명 + 10분) 동안 JWKS·토큰 검증에 남고, 공개키는 `signing.archive.pem` 에 영구 보관(백업·감사 보고서 서명 검증) · 유예 안에 여러 번 회전해도 그 사이 토큰이 끊기지 않음 |
| 암호화 키 | `kind: encryption` — 새 키 + 저장 암호문(시크릿·MFA 비밀·로그 원문) 즉시 재암호화, 이전 키는 `MultiFernet` 복호화용으로 남김 · `POST /api/v1/admin/keys/reencrypt?drop_previous=true` 로 실패 0 이면 이전 키 제거 |
| 다른 워커 | 회전은 `keys/generation` 파일로 알림 — 호출마다 stat 1회, 바뀌면 다시 읽음(캐시를 직접 비우지 않아도) |
| env 모드 | 키를 env(Helm Secret)로 주면 앱은 회전하지 않음(409 `keys_env_managed`) — 새 키로 Secret 교체 + `SIGNING_KEY_PEM_PREVIOUS`·`ENCRYPTION_KEY_PREVIOUS` 에 이전 키 → 재기동(54th checksum) → `reencrypt` |
| 권한·감사 | 플랫폼 관리자만(`admin` 역할) · 확인 `ROTATE` · 감사 `keys.rotate`·`keys.reencrypt` · CLI `nexus keys status|rotate --kind|reencrypt [--drop-previous]` |
| env 키 | `SIGNING_KEY_PEM_PREVIOUS`·`ENCRYPTION_KEY_PREVIOUS` — `.env.local`·`.env.prod`·`.env.example`(선택 — 비움), Helm `secrets.signingKeyPemPrevious`·`encryptionKeyPrevious`(지정했을 때만 Secret 에) |

## 수용 기준 → 시험 케이스

| # | 수용 기준 | 시험 | 방식 |
|---|-----------|------|------|
| C58-01 | 새 kid · JWKS 에 두 kid · 회전 전 토큰 유예 중 200 · 새 토큰은 새 kid · 유예 끝 → 이전 토큰 거부·JWKS 1개 · 회전 전 장기 서명은 보관 공개키로 검증 | `test_key_rotation.py::test_signing_rotation` | pytest |
| C58-02 | 회전 → 재암호화(실패 0)·키 2 · 값 그대로·암호문 바뀜 · drop → 키 1 · 값 그대로 | `test_encryption_rotation` | pytest |
| C58-03 | 운영자 403 `admin_only` · 확인 없음 400 · env 키 409 | `test_rotation_guards` | pytest |
| C58-04 | 다른 워커 회전(캐시 그대로) → 다음 호출이 새 kid·이전 kid 는 JWKS 에 | `test_other_worker_picks_up_rotation` | pytest |
| C58-05 | CLI status·rotate | `test_cli_keys` | pytest |
| P58 | prod(2 workers·공유 키 볼륨) — 회전 뒤 두 워커 모두 새 kid 로 검증·이전 토큰 유효·시크릿 복호화 | 완료 게이트(60th 스모크) | live |
