> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [cli_Abstract.md](cli_Abstract.md)

### 03. app/cli/__main__.py
**파일 요약 / File Summary**
- 역할 / Role: `python -m app.cli` 실행 진입점. Dockerfile 이 만드는 `/usr/local/bin/nexus` 래퍼(`PYTHONPATH=/app exec python -m app.cli "$@"`)가 이 파일을 실행한다.
- 진입점 거리 / Distance: 0
- LOC: 3
- 주요 export: 없음(실행 스크립트)
- 의존(내부): `app.cli.nexus.main`
- 외부 패키지: 없음(간접 typer)
- 부수효과 / Side effects: import 즉시 CLI 실행(sys.argv 파싱)
- 동시성 / Concurrency: none
- 테스트 커버리지 흔적: 직접 없음 — `test/test_cli.py` 는 `typer.testing.CliRunner` 로 `nexus.app` 을 직접 호출하고(`test/test_cli.py:9,26-27`) `nexus.HTTP` 에 TestClient 를 주입한다(`test/test_cli.py:19-20`).

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/cli/__main__.py lines=1-3
from app.cli.nexus import main

main()
```
**분석 / Analysis (Level 2)**
- L1: `nexus.main` 은 `app(prog_name="nexus")` 를 호출한다(`app/cli/nexus.py:746-747`). `prog_name` 을 지정해 도움말에 `python -m app.cli` 대신 `nexus` 가 나온다.
- L3: `if __name__ == "__main__":` 가드가 없다. `__main__.py` 는 `-m` 실행 시에만 로드되는 것이 관례라 실무상 문제는 드물지만, 누군가 `import app.cli.__main__` 을 하면 즉시 CLI 가 실행되고 `typer.Exit`/`SystemExit` 가 발생한다.
**짚어볼 점 / Watch-outs**
- 종료 코드는 typer 가 `SystemExit` 로 전달한다. `nexus.py:750-752` 의 `sys.exit(0)` 은 `nexus.py` 를 직접 실행할 때만 쓰이며 이 경로와 무관하다.

## 파일 평가 / File Assessment
- 강점: (1) 단일 책임. (2) Docker 래퍼와 결합해 컨테이너 안에서 `nexus` 명령을 제공(`nexus install code --local`, `app/cli/nexus.py:347`).
- 위험: (1) import 시 부수효과(가드 없음) — 영향 낮음.
- 개선 권고(영향순): (1) 관례상 무해하지만 `if __name__ == "__main__": main()` 으로 감싸면 도구(문서 생성기·커버리지 수집)의 우발적 import 를 막는다.
- 재읽기 권고 레벨: 4 — 읽을 내용이 거의 없다.
