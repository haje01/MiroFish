# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 개요

MiroFish는 다중 에이전트 AI 예측 시뮬레이션 플랫폼입니다. 문서를 업로드하면 GraphRAG로 지식 그래프를 구축하고, OASIS 엔진으로 수천 개의 에이전트가 트위터/레딧 환경에서 시뮬레이션하며, ReACT 패턴의 Report Agent가 보고서를 생성합니다.

## 주요 명령어

### 전체 설치 및 실행

```bash
cp .env.example .env          # 환경 변수 설정 (.env 편집 필요)
npm run setup:all              # 프론트엔드 + 백엔드 의존성 설치
npm run dev                    # 백엔드(5001) + 프론트엔드(3000) 동시 실행
```

### 백엔드

```bash
cd backend
uv sync                        # 의존성 설치
uv run python run.py           # 서버 실행 (포트 5001)
```

### 프론트엔드

```bash
cd frontend
npm install
npm run dev                    # 개발 서버 (포트 3000)
npm run build                  # 프로덕션 빌드
```

### Docker

```bash
docker compose up -d           # 전체 스택 실행
# 프론트엔드: localhost:3000, 백엔드: localhost:5001
```

## 아키텍처

### 전체 흐름

```
사용자 파일 업로드
  → Step1: 온톨로지 생성 + GraphRAG 그래프 구축 (Zep Cloud에 저장)
  → Step2: 에이전트 프로파일 생성 + 시뮬레이션 설정
  → Step3: OASIS 시뮬레이션 실행 (트위터/레딧 병렬)
  → Step4: Report Agent가 Zep 검색 도구로 보고서 생성
  → Step5: 보고서 에이전트와 대화 / 에이전트 인터뷰
```

### 백엔드 (`backend/app/`)

- **`api/`** — Flask 블루프린트 3개: `graph.py`, `simulation.py`, `report.py`
- **`services/`** — 비즈니스 로직 계층:
  - `ontology_generator.py` → `graph_builder.py` → `zep_graph_memory_updater.py`: 그래프 구축 파이프라인
  - `oasis_profile_generator.py` + `simulation_config_generator.py` → `simulation_runner.py`: 시뮬레이션 파이프라인
  - `zep_tools.py`: InsightForge/PanoramaSearch/QuickSearch/InterviewAgents 4가지 검색 도구
  - `report_agent.py`: ReACT 루프 기반 보고서 생성 (zep_tools 사용)
  - `simulation_ipc.py`: 시뮬레이션 프로세스와의 IPC (SSE 로그 스트리밍)
  - `simulation_manager.py`: 활성 시뮬레이션 상태 관리
- **`models/`** — `project.py`: 프로젝트 JSON 파일 영속화 / `task.py`: 비동기 태스크 상태 추적
- **`utils/`** — `llm_client.py`: OpenAI 호환 LLM 래퍼 / `file_parser.py`: PDF·MD·TXT 파싱

### 프론트엔드 (`frontend/src/`)

- **`views/`** — 페이지 단위: `MainView.vue`(Step1~5 흐름 관리), `SimulationRunView.vue`, `ReportView.vue`, `InteractionView.vue`
- **`components/`** — Step별 컴포넌트: `Step1GraphBuild` → `Step2EnvSetup` → `Step3Simulation` → `Step4Report` → `Step5Interaction`
- **`api/`** — Axios 래퍼: 기본 타임아웃 5분, 최대 3회 지수 백오프 재시도
- `vite.config.js`: `/api` 경로를 `localhost:5001`로 프록시

### 비동기 작업 패턴

그래프 구축, 시뮬레이션 실행, 보고서 생성은 모두 장시간 작업입니다.
- 백엔드: `TaskManager`로 태스크 ID 발급 후 스레드에서 실행
- 프론트엔드: 폴링으로 `/status/<task_id>` 확인

### 외부 의존성

| 서비스 | 용도 | 환경 변수 |
|--------|------|-----------|
| OpenAI 호환 LLM | 온톨로지·프로파일·보고서 생성 | `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL_NAME` |
| Zep Cloud | 에이전트 메모리 그래프 저장/검색 | `ZEP_API_KEY` |
| LLM Boost (선택) | 빠른 보조 작업용 별도 LLM | `LLM_BOOST_*` |

## 개발 시 주의사항

- **파이썬 실행**: `python`/`python3` 대신 `uv run python` 사용
- **LLM 프롬프트**: 영어로 작성 (CLAUDE.md 글로벌 규칙)
- **업로드 파일**: `backend/uploads/` 에 저장, `backend/uploads/simulations/`에 시뮬레이션 데이터
- **`oasis_profile_generator.py`의 `gender_map`**: `"男"/"女"` 등 중국어 키는 중국어 데이터 처리용이므로 유지
- **Step4Report.vue의 파싱 정규식**: `zep_tools.py`의 `to_text()` 출력 형식과 쌍으로 연결되어 있어 한쪽 변경 시 양쪽 동시 수정 필요

## 프로젝트 관리

- PLAN.md / PROGRESS.md: 다중 에이전트 작업 시 계획·진행 상태 기록
- 새 에이전트 시작 시 두 파일을 먼저 확인
