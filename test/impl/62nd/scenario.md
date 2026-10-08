# impl 62nd — 키 관리 보강 (Epic 62 · D22-②)

## 범위 (3차 검토 Lv2 L1·L2·L3·L6·L7·L27 — 59th 에서 '기록'으로 둔 것)

| 항목 | 내용 |
|------|------|
| generation | `keys/generation` 의 **내용(카운터)** 으로 세대 판단 — mtime 해상도가 거친 파일시스템에서도 같은 틱의 두 회전 구분 |
| 회전 잠금 | `rotate_signing`·`rotate_encryption`·`drop_previous_encryption` 은 `keys/rotate.lock`(flock) 안에서 — 동시 회전이 새 키를 잃지 않음 |
| env 2단계 | `SIGNING_KEY_PEM_NEXT`·`ENCRYPTION_KEY_NEXT` — 다음 키를 먼저 모든 파드에 배포(JWKS·검증·복호화만) → 현재 키로 올림. 롤링 중 401·복호화 실패 없음 |
| env 이전 키 | 공개키 PEM 허용 · `SIGNING_KEY_PREVIOUS_UNTIL`(ISO) 뒤 토큰 검증·JWKS 에서 빠지고 백업·보고서 검증에만 · `SIGNING_REVOKED_KIDS` 는 어디서도(파일 보관본 포함) |
| 감사 | 회전 직후 `keys.rotate`, 재암호화는 별도 `keys.reencrypt` — 재암호화가 실패해도 회전 기록이 남음 · env 키는 전용 예외 `keys.EnvManaged` |
| Helm | `secrets.signingKeyPemNext`·`encryptionKeyNext` · `config.signingKeyPreviousUntil`·`signingRevokedKids` · `secrets.rotationNonce` → api·데모 파드 `agentnexus/rotation-nonce`(existingSecret 키 교체 재기동) · helm-check 불변식(ci/ha) |

## 수용 기준 → 시험 케이스

| # | 수용 기준 | 시험 | 방식 |
|---|-----------|------|------|
| C62-01 | `_bump` 마다 +1 · mtime 그대로 내용만 바뀌어도 새 세대 | `test_key_mgmt.py::test_generation_is_content_counter` | pytest |
| C62-02 | 두 스레드 동시 회전 → 직전 kid 2개 서로 다름·둘 다 유예 중 | `test_concurrent_rotation_keeps_every_key` | pytest |
| C62-03 | 현재 A + 다음 B 파드가 B 서명 토큰 검증·B 암호문 복호화, 서명·암호화는 A · JWKS 첫 kid = A | `test_env_next_keys` | pytest |
| C62-04 | 공개키만 준 이전 키로 토큰 검증 · 검증 끝 뒤 토큰 거부·JWKS 제외·백업 서명 통과 · 폐기 kid 는 백업도 거부 | `test_env_previous_public_until_and_revoked` | pytest |
| C62-05 | 재암호화 예외에도 `keys.rotate` 감사 1건 · env 키 회전은 `EnvManaged` | `test_rotation_audited_before_reencrypt` | pytest |
| C62-06 | 값·템플릿·불변식 · helm-check 4 시나리오 + nonce 없는 ha 렌더는 FAIL | `test_helm_rotation_nonce` + `make helm-check` | pytest·helm |
| 회귀 | 58th 회전·59th 수정·배포 설정 | `test_key_rotation.py`·`test_review3_fixes.py`·`test_deploy.py`·`test_admin.py`·`test_ops.py` | pytest |
