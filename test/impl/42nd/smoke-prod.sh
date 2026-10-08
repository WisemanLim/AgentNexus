#!/bin/sh
# impl 42nd — prod 리허설: 라이선스·고지 파일이 이미지·번들에 포함되고 서빙되는지 (읽기만).
set -u
BASE="http://127.0.0.1:8800"
EXTRA=""; for f in compose/*.yml; do EXTRA="$EXTRA -f $f"; done
DC="docker compose -p wise-agentnexus-prod -f docker-compose.yml -f docker-compose.prod.yml $EXTRA --env-file .env.prod"
pass=0; fail=0
check() { if [ "$2" = "$3" ]; then echo "PASS $1 ($2)"; pass=$((pass+1)); else echo "FAIL $1 (got=$2 want=$3)"; fail=$((fail+1)); fi; }
check P42-01-image-license "$($DC exec -T api sh -c 'md5sum /app/LICENSE | cut -c1-8; test -f /app/NOTICE && test -f /app/THIRD_PARTY_LICENSES.md && echo files' | tr '\n' ':')" "3b83ef96:files:"
check P42-02-font-license-served "$(curl -s -o /dev/null -w '%{http_code}' $BASE/licenses/Pretendard-OFL-1.1.txt):$(curl -s $BASE/licenses/Pretendard-OFL-1.1.txt | grep -c 'SIL Open Font License')" "200:1"
check P42-03-bundle-legal-comments "$($DC exec -T api sh -c 'grep -l "@license\|Copyright" /app/web/dist/_app/*.js | wc -l' | tr -d ' ' | awk '{print ($1 > 0) ? "kept" : "stripped"}')" "kept"
check P42-04-uv-pinned "$($DC exec -T api uv --version | cut -d' ' -f2)" "0.12.23"
echo "total=$((pass+fail)) pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
