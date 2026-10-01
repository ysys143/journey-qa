# journey-qa

[English](README.md) | 한국어

가상의 팀이 제품을 처음 쓰는 과정을 그대로 재현해 검수하는 에이전트 스킬이다(페르소나 여정 인수 검사). 격리된 깨끗한 환경에서 가상의 팀원들이 실제 사용자처럼 제품을 설치하고, 새 사용자를 초대하고, 팀으로 운영하는 과정을 처음부터 끝까지 직접 수행한다. 그 과정에서 문서·제품·UX 결함, 공개해도 되는 증거, 문서용 스크린샷을 남긴다.

스킬은 `SKILL.md`에서 시작한다. 핵심 개념은 `docs/concepts.md`, 진행 절차는 `docs/workflow.md`, 터미널 단계의 규칙은 `docs/terminal.md`에 정리돼 있다.

## 디렉터리 구성

```
SKILL.md                    시작 지점: 모드 고르기, 사전 점검표, 게이트 실행 명령, 사람이 결정할 지점
docs/
  concepts.md               페르소나, 여정, 환경, 모드, 게이트, 정보 유출 방지, 사람이 결정할 지점
  workflow.md               0~7단계, 계획 -> 실행 -> 검사 -> 수정 반복, 에이전트 운영 규칙, codify/run
  prod-access.md            운영 데이터를 읽을 때 지킬 규칙
  engines.md                상황별 실행 엔진 선택(기본값 `--engine auto`), 로그인 상태 저장·주입·공유·만료 처리
  terminal.md               터미널 단계: exec·pty·tmux 고르기, 비교표, 드라이버별 사용법, 주의할 점
  decisions/                결정 기록(선택의 근거가 된 측정값)
  rationale.md              규칙별로 막으려는 실패와 그 규칙을 강제하는 위치
scenarios/
  schema.json               시나리오 형식
  validate.py               스키마와 참조 관계 검증(uv run, PyYAML은 스크립트 안에 의존성 선언)
  example/                  특정 제품에 묶이지 않은 예시: onboarding.yaml, first-run.yaml(exec·pty·tmux 단계)
  selftest/                 검증기 테스트 케이스
runners/
  README.md                 run 모드: 인터프리터, 백엔드, 터미널 드라이버, 훅, 실패 증거, 기준 이미지
  run.sh                    실행 진입점(검증, 변환, 실행, 기준 이미지 비교)
  run-engine.mjs  lib/      엔진에 독립적인 인터프리터와 페이지 안에서 도는 함수. lib/terminal.mjs가 터미널 단계를 실행
  ego/  playwright/         엔진별 백엔드
  terminal/                 터미널 드라이버(exec, pty, tmux)와 실행 기록 정규화
  selftest/                 터미널·오케스트레이션 스모크 테스트와, 가상 데모 앱으로 돌리는 브라우저 스모크 테스트
gates/
  SPEC.md                   게이트 명세(종료 코드, 규칙, 예외, 자체 검사)
  LIMITS.md                 측정으로 확인한 한계
  leakscan.py               텍스트·덤프의 정보 유출 검사
  image_scan.py             이미지의 정보 유출 검사(촬영 시 저장한 화면 텍스트 + OCR)
  png_meta.py               PNG 파일 이름과 메타데이터 청크 검사
  docs_images.py            문서와 이미지의 대응, 언어별 문서 쌍 검사
  img_diff.py               변하는 영역을 가린 뒤 엔진별 기준 이미지와 픽셀 비교
  sql_assert.py             DB 계정 정보 검증(sqlite, psql)
  manifest.py               게이트 파일 해시 고정과 변조 확인
  policy.default.json       기본 정책
  selftest/                 테스트 케이스, --sabotage
capture/
  SPEC.md                   촬영 명세
  strip_png.py              허용된 청크만 남겨 메타데이터 제거
  redact.py                 실행 기록의 민감 정보 가리기
  evidence.py               실패 증거를 가리고 검사한 뒤, 통과하지 못하면 삭제
  render_terminal.py        가린 실행 기록을 터미널 화면 이미지로 렌더링
fixtures/
  README.md                 합성 데이터 생성 순서와 생성기 인터페이스
  roster.schema.json        가상 팀 명단 형식
  roster.example.json       가상 인물 5명
  roster_check.py           명단 검증기(+ selftest/)
  reference-shape.template.md  실제 운영 화면의 모양을 기록하는 양식
  realism-checklist.md      화면이 자연스러운지 판정할 때 쓰는 점검표
adapters/
  README.md                 어댑터 작성 규약
  <product>/                함께 제공되는 예시 어댑터. 직접 만든 어댑터는 대상 프로젝트에 둔다:
                            <project>/.journey-qa/adapters/<product>/ (또는 --adapter로 원하는 경로)
check.sh                    레포의 전체 검사(JQA_ADAPTER_DIRS로 다른 위치의 어댑터도 포함)
templates/
  report.md  issue-draft.md  decision-log.md  gate-verdict.schema.json  prod-query.sql
  examples/workflow-phase.js
```

## 검사 실행

```
./check.sh                                           # 아래 검사 전부. 종료 코드 0이면 통과
JQA_DENYLIST=<레포 밖에 둔 실제 금지 목록> ./check.sh   # 레포에 실제 식별자가 있는지도 검사
JQA_ADAPTER_DIRS=<dir>[:<dir>...] ./check.sh         # 레포 밖에 둔 어댑터도 함께 검사
```

`check.sh`가 실행하는 검사:

- 게이트 자체 검사(코어와 모든 어댑터)와 `--sabotage`(게이트를 망가뜨렸을 때 검사가 이를 잡는지 확인)
- 매니페스트 검증(gates, capture, runners, 모든 어댑터)
- 어댑터 위치를 찾는 규칙 검사(레포 밖에 복사한 어댑터 포함)
- 시나리오 검증기와 명단 검증기의 테스트 케이스
- 엔진 자동 선택 검사
- 터미널 드라이버·정규화·렌더러 검사, 브라우저 없이 터미널 단계만으로 이루어진 시나리오를 `runners/run.sh`로 실행하는 검사
- 러너 모듈 전체의 문법 검사
- 레포 자체의 정보 유출 검사

레포 자체 검사에서 걸렸지만, 실제 유출이 아니라 문서가 패턴을 설명하느라 걸린 경우만 해당 어댑터의 `self-scan.accepted`에 이유와 함께 등록한다. `check.sh`는 검사하는 어댑터마다 이 파일을 읽는다. 등록돼 있지만 더 이상 걸리지 않는 항목이 있어도 실패로 처리한다.

필요한 도구:

- Python 3.10 이상
- `tesseract`(이미지 OCR)
- `rsvg-convert`(터미널 이미지와 OCR 테스트 이미지 재생성)
- `uv`(YAML 시나리오 검증)
- "DejaVu Sans Mono" 글꼴(터미널 이미지 렌더링. 글자 폭을 이 글꼴 기준으로 계산함)
- Node.js 18 이상(러너)
- `tmux` 3.2 이상(tmux 단계와 `check.sh`의 터미널 검사에 필요. 없으면 그 검사가 실패한다)
- `psql`(psql 엔진을 쓸 때만)

브라우저 단계가 있는 시나리오를 실행하려면 엔진 백엔드가 하나 이상 있어야 한다(터미널 단계만 있는 시나리오에는 필요 없다). 실제 사용자 프로필로 화면을 띄워 촬영하는 `ego-browser`, 또는 격리된 환경에서 헤드리스나 화면 모드로 돌고 병렬 실행도 되는 Playwright 중 하나면 된다. 기본값 `--engine auto`는 쓸 수 있는 엔진을 찾아 실행 환경에 맞게 고른다(`docs/engines.md`).

## 코어와 어댑터

코어(`adapters/` 밖의 전부)는 특정 제품을 전제하지 않는다. 제품마다 달라지는 내용은 모두 어댑터에 넣는다.

| 코어가 제공하는 것 | 어댑터가 제공하는 것 |
|---|---|
| 텍스트·이미지·메타데이터·문서 이미지 게이트. 검사 규칙, 파일 분류, 정규화, 경로 검사, 정책 기반 예외 처리 | `policy.json`: 추가 규칙, 비밀 값으로 볼 키 이름, 예외(통과·실패 테스트 케이스를 반드시 함께 둠), 가림 규칙 |
| SQL 검증 실행기, 명단을 쿼리에 넣는 장치, 제공자별 1:1:1 대응 예시 | 제품 테이블에 맞춘 `sql/identity.sql` 검증 쿼리 |
| 촬영 명세, 터미널 이미지 렌더링, 민감 정보 가리기, 메타데이터 제거 | 제품 고유의 가림 규칙 |
| 합성 데이터 생성 순서, 생성기 인터페이스, 자연스러움 점검표, 운영 화면 기록 양식 | 제품 차트·표가 데이터를 그리는 규칙, 필요하면 생성기 |
| 단계, 게이트 종류, 반복 구조, 워크플로 골격, 결정 기록 형식 | 환경 구성, 제품에 관한 사실, 이전 실행에서 찾은 결함 |
| 명단 형식, 검증기, 예시 | 제품이 실제로 저장하는 값(역할, 요금제, 제공자)과 `privileged_roles` |
| 시나리오 형식과 검증기 | 여정 시나리오 |
| run 모드 러너, 엔진 백엔드, 마스크, 기준 이미지 비교, 실패 증거 검사 | 선택자 맵, 러너 훅(준비, 제품별 확인, 정리), 제품의 토큰·호스트 관련 사실 |

어댑터에 들어가는 파일, 어댑터를 찾는 순서(직접 지정한 경로 → 대상 프로젝트 → 함께 제공되는 예시), 작성 방법은 `adapters/README.md`에 있다. `adapters/example-app/`은 가상의 제품으로 만든 예시 어댑터다.

## 앞으로 할 일

아직 지원하지 않는 것:

- codify 자동화. 지금은 에이전트가 explore 기록을 읽고 시나리오를 직접 작성한다.
- 제품별 합성 데이터 생성기. 코어에는 인터페이스 규약만 있다.

## 라이선스

MIT. 자세한 내용은 `LICENSE`를 참고한다.
