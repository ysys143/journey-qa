# journey-qa

[English](README.md) | 한국어

페르소나 여정 인수 검사(acceptance QA)를 하는 에이전트 스킬이다. 격리된 깨끗한 환경과 가상의 팀에서 시작해 제품의 여정을 처음부터 걷는다(예: 설치와 첫 권한 사용자, 새 사용자 온보딩, 운영 중인 팀). 그 결과로 문서·제품·UX 결함, 공개해도 되는 증거, 문서용 스크린샷을 남긴다.

스킬 진입점은 `SKILL.md`이다. 개념은 `docs/concepts.md`, 절차는 `docs/workflow.md`에 있다.

## 구조

```
SKILL.md                    진입점: 모드 선택, 0단계 점검표, 게이트 명령, 사람 관문
docs/
  concepts.md               페르소나, 여정, 환경, 모드, 게이트, 누출 방어, 사람 관문
  workflow.md               0~7단계, 계획 -> 실행 -> 게이트 -> 수리 반복, 오케스트레이션 규칙, codify/run
  prod-access.md            운영 데이터 읽기 규칙
  engines.md                어디서 어떤 실행 엔진을 쓰는가(기본값 `--engine auto`), 로그인 상태 저장·주입·교환·만료
  decisions/                결정 기록(선택의 근거가 된 측정)
  rationale.md              규칙마다 막으려는 일반적 실패와 강제되는 위치
scenarios/
  schema.json               시나리오 형식
  validate.py               스키마와 교차 참조 검증(uv run, PyYAML은 인라인 의존성)
  example/onboarding.yaml   제품 무관 예시
  selftest/                 검증기 대조군
runners/
  README.md                 run 모드: 인터프리터, 백엔드, 훅, 실패 증거, 기준 이미지
  run.sh                    진입점(검증, 변환, 실행, 기준 이미지 비교)
  run-engine.mjs  lib/      엔진 무관 인터프리터와 페이지 내 함수
  ego/  playwright/         엔진 백엔드
  selftest/                 가상 데모 앱으로 하는 스모크 테스트
gates/
  SPEC.md                   게이트 규격(종료 코드, 규칙, 예외, 자체 검사)
  LIMITS.md                 측정한 한계
  leakscan.py               텍스트와 덤프의 누출
  image_scan.py             이미지 누출(형제 innerText + OCR)
  png_meta.py               PNG 이름과 청크
  docs_images.py            문서-이미지 대응, 로캘 쌍
  img_diff.py               엔진별 기준 이미지와의 마스크 픽셀 비교
  sql_assert.py             DB 신원 단언(sqlite, psql)
  manifest.py               게이트 고정과 검증
  policy.default.json       기본 정책
  selftest/                 대조군, --sabotage
capture/
  SPEC.md                   촬영 규격
  strip_png.py              청크 허용 목록으로 메타데이터 제거
  redact.py                 기록 가림
  evidence.py               실패 증거를 가리고 검사, 통과하지 못하면 삭제
  render_terminal.py        가린 기록을 터미널 이미지로 렌더
fixtures/
  README.md                 합성 데이터 순서와 생성기 인터페이스
  roster.schema.json        명부 형식
  roster.example.json       가상 인물 5명
  roster_check.py           명부 검증기(+ selftest/)
  reference-shape.template.md  운영 참조의 형태를 서술하는 양식
  realism-checklist.md      판정자 점검표
adapters/
  README.md                 어댑터 계약
  <product>/                함께 배포되는 예시 어댑터. 자기 어댑터는 대상 프로젝트에 둔다:
                            <project>/.journey-qa/adapters/<product>/ (또는 --adapter로 임의 경로)
check.sh                    레포의 모든 검사(JQA_ADAPTER_DIRS로 다른 곳의 어댑터 추가)
templates/
  report.md  issue-draft.md  decision-log.md  gate-verdict.schema.json  prod-query.sql
  examples/workflow-phase.js
```

## 검사 실행

```
./check.sh                                           # 아래 전부. 종료 코드 0이면 통과
JQA_DENYLIST=<레포 밖의 실제 금지 목록> ./check.sh     # 레포를 실제 식별자로도 검사
JQA_ADAPTER_DIRS=<dir>[:<dir>...] ./check.sh         # 레포 밖에 둔 어댑터도 검사
```

`check.sh`가 돌리는 것:

- 게이트 자체 검사(코어와 모든 어댑터 컨텍스트)와 `--sabotage`
- 매니페스트 검증(gates, capture, runners, 모든 어댑터)
- 어댑터 위치 결정 자체 검사(레포 밖에 복사한 어댑터 포함)
- 시나리오 검증기 대조군, 명부 검증기 대조군
- 엔진 선택 자체 검사, 러너 모듈 전체 문법 검사
- 레포 자체 누출 검사

자체 검사에 걸린 것 중 문장이 패턴을 설명하기 때문에 생긴 것만 그 어댑터의 `self-scan.accepted`에 이유와 함께 적는다. `check.sh`는 검사하는 어댑터마다 이 파일을 읽는다. 목록에 있는데 더 이상 아무것에도 맞지 않는 항목도 실패로 친다.

필요한 것:

- Python 3.10 이상
- `tesseract`(이미지 OCR)
- `rsvg-convert`(터미널 이미지와 OCR 픽스처 재생성)
- `uv`(YAML 시나리오 검증)
- "DejaVu Sans Mono" 글꼴(터미널 렌더링, 글자 폭이 이 글꼴 기준)
- Node.js 18 이상(러너)
- `psql`(psql 엔진을 쓸 때만)

시나리오를 실행하려면 엔진 백엔드도 하나 이상 있어야 한다. `ego-browser`(실제 사용자 프로필, 화면 있는 촬영)나 Playwright 설치(격리, 헤드리스 또는 화면 있음, 병렬 실행 가능) 중 하나다. 기본값 `--engine auto`는 쓸 수 있는 엔진을 감지하고 실행 위치에 맞춰 고른다(`docs/engines.md`).

## 코어와 어댑터

코어(`adapters/` 밖의 전부)는 어떤 제품도 모른다. 제품마다 다른 것은 모두 어댑터에 넣는다.

| 코어가 제공 | 어댑터가 제공 |
|---|---|
| 텍스트·이미지·메타데이터·문서-이미지 게이트. 모든 규칙, 파일 분류, 정규화, 경로 검사, 정책 기반 예외 | `policy.json`: 추가 규칙, 비밀 키 이름, 예외(각각 통과·실패 대조군 포함), 가림 규칙 |
| SQL 단언 실행기와 명부 CTE, 제공자별 1:1:1 예시 | 제품 테이블에 대한 `sql/identity.sql` 단언 |
| 촬영 규격, 터미널 렌더링, 가림, 메타데이터 제거 | 제품 고유의 가림 규칙 |
| 합성 데이터 순서, 생성기 인터페이스 계약, 현실감 점검표, 참조 형태 양식 | 제품 차트·표의 데이터 계약, 필요하면 생성기 |
| 단계, 게이트 종류, 반복 구조, 워크플로 골격, 결정 기록 형식 | 환경 구성, 제품 사실, 이전 실행의 결함 |
| 명부 형식, 검증기, 예시 | 저장 형태 값(역할, 요금제, 제공자)과 `privileged_roles` |
| 시나리오 형식과 검증기 | 여정 시나리오 |
| run 모드 러너, 엔진 백엔드, 마스크, 기준 이미지 비교, 실패 증거 검사 | 선택자 맵, 러너 훅(준비, 제품 검사, 정리), 제품 토큰과 호스트 관련 사실 |

어댑터 파일 구성, 어댑터를 찾는 위치(명시한 경로, 대상 프로젝트, 함께 배포되는 예시 순), 작성 절차는 `adapters/README.md`에 있다. `adapters/example-app/`은 가상 제품으로 만든 예시 어댑터다.

## 앞으로 할 일

아직 없는 것:

- run 모드의 터미널 단계. 러너는 브라우저를 다루고, 터미널 여정은 pty 드라이버가 더 필요하다.
- codify 자동화. 지금은 에이전트가 explore 기록을 읽고 시나리오를 직접 쓴다.
- 제품별 합성 데이터 생성기. 코어에는 인터페이스 계약만 있다.

## 라이선스

MIT. `LICENSE` 참고.
