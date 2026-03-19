"""
OASIS 시뮬레이션 매니저
Twitter와 Reddit 이중 플랫폼 병렬 시뮬레이션 관리
사전 설정 스크립트 + LLM 지능형 설정 파라미터 생성 사용
"""

import os
import json
import shutil
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ..config import Config
from ..utils.logger import get_logger
from .zep_entity_reader import ZepEntityReader, FilteredEntities
from .oasis_profile_generator import OasisProfileGenerator, OasisAgentProfile
from .simulation_config_generator import SimulationConfigGenerator, SimulationParameters

logger = get_logger('mirofish.simulation')


class SimulationStatus(str, Enum):
    """시뮬레이션 상태"""
    CREATED = "created"
    PREPARING = "preparing"
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"      # 시뮬레이션이 수동으로 중지됨
    COMPLETED = "completed"  # 시뮬레이션이 정상적으로 완료됨
    FAILED = "failed"


class PlatformType(str, Enum):
    """플랫폼 타입"""
    TWITTER = "twitter"
    REDDIT = "reddit"


@dataclass
class SimulationState:
    """시뮬레이션 상태"""
    simulation_id: str
    project_id: str
    graph_id: str

    # 플랫폼 활성화 상태
    enable_twitter: bool = True
    enable_reddit: bool = True

    # 상태
    status: SimulationStatus = SimulationStatus.CREATED

    # 준비 단계 데이터
    entities_count: int = 0
    profiles_count: int = 0
    entity_types: List[str] = field(default_factory=list)

    # 설정 생성 정보
    config_generated: bool = False
    config_reasoning: str = ""

    # 런타임 데이터
    current_round: int = 0
    twitter_status: str = "not_started"
    reddit_status: str = "not_started"

    # 타임스탬프
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    # 오류 정보
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """전체 상태 딕셔너리 (내부 사용)"""
        return {
            "simulation_id": self.simulation_id,
            "project_id": self.project_id,
            "graph_id": self.graph_id,
            "enable_twitter": self.enable_twitter,
            "enable_reddit": self.enable_reddit,
            "status": self.status.value,
            "entities_count": self.entities_count,
            "profiles_count": self.profiles_count,
            "entity_types": self.entity_types,
            "config_generated": self.config_generated,
            "config_reasoning": self.config_reasoning,
            "current_round": self.current_round,
            "twitter_status": self.twitter_status,
            "reddit_status": self.reddit_status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": self.error,
        }

    def to_simple_dict(self) -> Dict[str, Any]:
        """간소화된 상태 딕셔너리 (API 반환용)"""
        return {
            "simulation_id": self.simulation_id,
            "project_id": self.project_id,
            "graph_id": self.graph_id,
            "status": self.status.value,
            "entities_count": self.entities_count,
            "profiles_count": self.profiles_count,
            "entity_types": self.entity_types,
            "config_generated": self.config_generated,
            "error": self.error,
        }


class SimulationManager:
    """
    시뮬레이션 매니저

    핵심 기능:
    1. Zep 그래프에서 엔티티 읽기 및 필터링
    2. OASIS Agent Profile 생성
    3. LLM을 사용하여 시뮬레이션 설정 파라미터 지능적으로 생성
    4. 사전 설정 스크립트에 필요한 모든 파일 준비
    """

    # 시뮬레이션 데이터 저장 디렉토리
    SIMULATION_DATA_DIR = os.path.join(
        os.path.dirname(__file__),
        '../../uploads/simulations'
    )

    def __init__(self):
        # 디렉토리 존재 확인
        os.makedirs(self.SIMULATION_DATA_DIR, exist_ok=True)

        # 메모리 내 시뮬레이션 상태 캐시
        self._simulations: Dict[str, SimulationState] = {}

    def _get_simulation_dir(self, simulation_id: str) -> str:
        """시뮬레이션 데이터 디렉토리 가져오기"""
        sim_dir = os.path.join(self.SIMULATION_DATA_DIR, simulation_id)
        os.makedirs(sim_dir, exist_ok=True)
        return sim_dir

    def _save_simulation_state(self, state: SimulationState):
        """시뮬레이션 상태를 파일에 저장"""
        sim_dir = self._get_simulation_dir(state.simulation_id)
        state_file = os.path.join(sim_dir, "state.json")

        state.updated_at = datetime.now().isoformat()

        with open(state_file, 'w', encoding='utf-8') as f:
            json.dump(state.to_dict(), f, ensure_ascii=False, indent=2)

        self._simulations[state.simulation_id] = state

    def _load_simulation_state(self, simulation_id: str) -> Optional[SimulationState]:
        """파일에서 시뮬레이션 상태 로드"""
        if simulation_id in self._simulations:
            return self._simulations[simulation_id]

        sim_dir = self._get_simulation_dir(simulation_id)
        state_file = os.path.join(sim_dir, "state.json")

        if not os.path.exists(state_file):
            return None

        with open(state_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        state = SimulationState(
            simulation_id=simulation_id,
            project_id=data.get("project_id", ""),
            graph_id=data.get("graph_id", ""),
            enable_twitter=data.get("enable_twitter", True),
            enable_reddit=data.get("enable_reddit", True),
            status=SimulationStatus(data.get("status", "created")),
            entities_count=data.get("entities_count", 0),
            profiles_count=data.get("profiles_count", 0),
            entity_types=data.get("entity_types", []),
            config_generated=data.get("config_generated", False),
            config_reasoning=data.get("config_reasoning", ""),
            current_round=data.get("current_round", 0),
            twitter_status=data.get("twitter_status", "not_started"),
            reddit_status=data.get("reddit_status", "not_started"),
            created_at=data.get("created_at", datetime.now().isoformat()),
            updated_at=data.get("updated_at", datetime.now().isoformat()),
            error=data.get("error"),
        )

        self._simulations[simulation_id] = state
        return state

    def create_simulation(
        self,
        project_id: str,
        graph_id: str,
        enable_twitter: bool = True,
        enable_reddit: bool = True,
    ) -> SimulationState:
        """
        새로운 시뮬레이션 생성

        Args:
            project_id: 프로젝트 ID
            graph_id: Zep 그래프 ID
            enable_twitter: Twitter 시뮬레이션 활성화 여부
            enable_reddit: Reddit 시뮬레이션 활성화 여부

        Returns:
            SimulationState
        """
        import uuid
        simulation_id = f"sim_{uuid.uuid4().hex[:12]}"

        state = SimulationState(
            simulation_id=simulation_id,
            project_id=project_id,
            graph_id=graph_id,
            enable_twitter=enable_twitter,
            enable_reddit=enable_reddit,
            status=SimulationStatus.CREATED,
        )

        self._save_simulation_state(state)
        logger.info(f"시뮬레이션 생성: {simulation_id}, project={project_id}, graph={graph_id}")

        return state

    def prepare_simulation(
        self,
        simulation_id: str,
        simulation_requirement: str,
        document_text: str,
        defined_entity_types: Optional[List[str]] = None,
        use_llm_for_profiles: bool = True,
        progress_callback: Optional[callable] = None,
        parallel_profile_count: int = 3
    ) -> SimulationState:
        """
        시뮬레이션 환경 준비 (전 과정 자동화)

        단계:
        1. Zep 그래프에서 엔티티 읽기 및 필터링
        2. 각 엔티티에 대한 OASIS Agent Profile 생성 (선택적 LLM 강화, 병렬 처리 지원)
        3. LLM을 사용하여 시뮬레이션 설정 파라미터 지능적으로 생성 (시간, 활동도, 발언 빈도 등)
        4. 설정 파일 및 Profile 파일 저장
        5. 사전 설정 스크립트를 시뮬레이션 디렉토리에 복사

        Args:
            simulation_id: 시뮬레이션 ID
            simulation_requirement: 시뮬레이션 요구사항 설명 (LLM 설정 생성용)
            document_text: 원본 문서 내용 (LLM 배경 이해용)
            defined_entity_types: 사전 정의된 엔티티 타입 (선택)
            use_llm_for_profiles: LLM을 사용하여 상세 캐릭터 설정 생성 여부
            progress_callback: 진행 콜백 함수 (stage, progress, message)
            parallel_profile_count: 병렬로 생성할 캐릭터 설정 수, 기본값 3

        Returns:
            SimulationState
        """
        state = self._load_simulation_state(simulation_id)
        if not state:
            raise ValueError(f"시뮬레이션이 존재하지 않습니다: {simulation_id}")

        try:
            state.status = SimulationStatus.PREPARING
            self._save_simulation_state(state)

            sim_dir = self._get_simulation_dir(simulation_id)

            # ========== 단계1: 엔티티 읽기 및 필터링 ==========
            if progress_callback:
                progress_callback("reading", 0, "Zep 그래프 연결 중...")

            reader = ZepEntityReader()

            if progress_callback:
                progress_callback("reading", 30, "노드 데이터 읽는 중...")

            filtered = reader.filter_defined_entities(
                graph_id=state.graph_id,
                defined_entity_types=defined_entity_types,
                enrich_with_edges=True
            )

            state.entities_count = filtered.filtered_count
            state.entity_types = list(filtered.entity_types)

            if progress_callback:
                progress_callback(
                    "reading", 100,
                    f"완료, 총 {filtered.filtered_count}개 엔티티",
                    current=filtered.filtered_count,
                    total=filtered.filtered_count
                )

            if filtered.filtered_count == 0:
                state.status = SimulationStatus.FAILED
                state.error = "조건에 맞는 엔티티를 찾을 수 없습니다. 그래프가 올바르게 구축되었는지 확인해주세요"
                self._save_simulation_state(state)
                return state

            # 사물/장소/개념 등 소셜 행위자 불가 엔티티 필터링 (시뮬레이션 전용)
            _checker = OasisProfileGenerator.__new__(OasisProfileGenerator)
            social_entities = []
            skipped = []
            for e in filtered.entities:
                etype = e.get_entity_type() or "Entity"
                if _checker._is_inanimate_entity_type(etype):
                    skipped.append(f"{e.name}({etype})")
                else:
                    social_entities.append(e)
            if skipped:
                logger.warning(f"시뮬레이션 제외 엔티티 {len(skipped)}개: {', '.join(skipped)}")
            filtered.entities = social_entities

            # ========== 단계2: Agent Profile 생성 ==========
            total_entities = len(filtered.entities)

            if progress_callback:
                progress_callback(
                    "generating_profiles", 0,
                    "생성 시작 중...",
                    current=0,
                    total=total_entities
                )

            # graph_id를 전달하여 Zep 검색 기능을 활성화하고 더 풍부한 컨텍스트 획득
            generator = OasisProfileGenerator(graph_id=state.graph_id)

            def profile_progress(current, total, msg):
                if progress_callback:
                    progress_callback(
                        "generating_profiles",
                        int(current / total * 100),
                        msg,
                        current=current,
                        total=total,
                        item_name=msg
                    )

            # 실시간 저장을 위한 파일 경로 설정 (Reddit JSON 형식 우선)
            realtime_output_path = None
            realtime_platform = "reddit"
            if state.enable_reddit:
                realtime_output_path = os.path.join(sim_dir, "reddit_profiles.json")
                realtime_platform = "reddit"
            elif state.enable_twitter:
                realtime_output_path = os.path.join(sim_dir, "twitter_profiles.csv")
                realtime_platform = "twitter"

            profiles = generator.generate_profiles_from_entities(
                entities=filtered.entities,
                use_llm=use_llm_for_profiles,
                progress_callback=profile_progress,
                graph_id=state.graph_id,  # Zep 검색을 위해 graph_id 전달
                parallel_count=parallel_profile_count,  # 병렬 생성 수
                realtime_output_path=realtime_output_path,  # 실시간 저장 경로
                output_platform=realtime_platform  # 출력 형식
            )

            state.profiles_count = len(profiles)

            # Profile 파일 저장 (Twitter는 CSV 형식, Reddit은 JSON 형식)
            # Reddit은 생성 과정에서 이미 실시간 저장되었으므로 여기서 한 번 더 저장하여 완전성 보장
            if progress_callback:
                progress_callback(
                    "generating_profiles", 95,
                    "Profile 파일 저장 중...",
                    current=total_entities,
                    total=total_entities
                )

            if state.enable_reddit:
                generator.save_profiles(
                    profiles=profiles,
                    file_path=os.path.join(sim_dir, "reddit_profiles.json"),
                    platform="reddit"
                )

            if state.enable_twitter:
                # Twitter는 CSV 형식 사용! OASIS의 요구 사항
                generator.save_profiles(
                    profiles=profiles,
                    file_path=os.path.join(sim_dir, "twitter_profiles.csv"),
                    platform="twitter"
                )

            if progress_callback:
                progress_callback(
                    "generating_profiles", 100,
                    f"완료, 총 {len(profiles)}개 Profile",
                    current=len(profiles),
                    total=len(profiles)
                )

            # ========== 단계3: LLM 지능형 시뮬레이션 설정 생성 ==========
            if progress_callback:
                progress_callback(
                    "generating_config", 0,
                    "시뮬레이션 요구사항 분석 중...",
                    current=0,
                    total=3
                )

            config_generator = SimulationConfigGenerator()

            if progress_callback:
                progress_callback(
                    "generating_config", 30,
                    "LLM 호출하여 설정 생성 중...",
                    current=1,
                    total=3
                )

            sim_params = config_generator.generate_config(
                simulation_id=simulation_id,
                project_id=state.project_id,
                graph_id=state.graph_id,
                simulation_requirement=simulation_requirement,
                document_text=document_text,
                entities=filtered.entities,
                enable_twitter=state.enable_twitter,
                enable_reddit=state.enable_reddit
            )

            if progress_callback:
                progress_callback(
                    "generating_config", 70,
                    "설정 파일 저장 중...",
                    current=2,
                    total=3
                )

            # 설정 파일 저장
            config_path = os.path.join(sim_dir, "simulation_config.json")
            with open(config_path, 'w', encoding='utf-8') as f:
                f.write(sim_params.to_json())

            state.config_generated = True
            state.config_reasoning = sim_params.generation_reasoning

            if progress_callback:
                progress_callback(
                    "generating_config", 100,
                    "설정 생성 완료",
                    current=3,
                    total=3
                )

            # 참고: 실행 스크립트는 backend/scripts/ 디렉토리에 유지되며, 시뮬레이션 디렉토리로 복사하지 않음
            # 시뮬레이션 시작 시 simulation_runner가 scripts/ 디렉토리에서 스크립트를 실행

            # 상태 업데이트
            state.status = SimulationStatus.READY
            self._save_simulation_state(state)

            logger.info(f"시뮬레이션 준비 완료: {simulation_id}, "
                       f"entities={state.entities_count}, profiles={state.profiles_count}")

            return state

        except Exception as e:
            logger.error(f"시뮬레이션 준비 실패: {simulation_id}, error={str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            state.status = SimulationStatus.FAILED
            state.error = str(e)
            self._save_simulation_state(state)
            raise

    def get_simulation(self, simulation_id: str) -> Optional[SimulationState]:
        """시뮬레이션 상태 가져오기"""
        return self._load_simulation_state(simulation_id)

    def list_simulations(self, project_id: Optional[str] = None) -> List[SimulationState]:
        """모든 시뮬레이션 목록 조회"""
        simulations = []

        if os.path.exists(self.SIMULATION_DATA_DIR):
            for sim_id in os.listdir(self.SIMULATION_DATA_DIR):
                # 숨김 파일 (.DS_Store 등) 및 디렉토리가 아닌 파일 건너뛰기
                sim_path = os.path.join(self.SIMULATION_DATA_DIR, sim_id)
                if sim_id.startswith('.') or not os.path.isdir(sim_path):
                    continue

                state = self._load_simulation_state(sim_id)
                if state:
                    if project_id is None or state.project_id == project_id:
                        simulations.append(state)

        return simulations

    def get_profiles(self, simulation_id: str, platform: str = "reddit") -> List[Dict[str, Any]]:
        """시뮬레이션의 Agent Profile 가져오기"""
        state = self._load_simulation_state(simulation_id)
        if not state:
            raise ValueError(f"시뮬레이션이 존재하지 않습니다: {simulation_id}")

        sim_dir = self._get_simulation_dir(simulation_id)
        profile_path = os.path.join(sim_dir, f"{platform}_profiles.json")

        if not os.path.exists(profile_path):
            return []

        with open(profile_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def get_simulation_config(self, simulation_id: str) -> Optional[Dict[str, Any]]:
        """시뮬레이션 설정 가져오기"""
        sim_dir = self._get_simulation_dir(simulation_id)
        config_path = os.path.join(sim_dir, "simulation_config.json")

        if not os.path.exists(config_path):
            return None

        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def get_run_instructions(self, simulation_id: str) -> Dict[str, str]:
        """실행 안내 가져오기"""
        sim_dir = self._get_simulation_dir(simulation_id)
        config_path = os.path.join(sim_dir, "simulation_config.json")
        scripts_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../scripts'))

        return {
            "simulation_dir": sim_dir,
            "scripts_dir": scripts_dir,
            "config_file": config_path,
            "commands": {
                "twitter": f"python {scripts_dir}/run_twitter_simulation.py --config {config_path}",
                "reddit": f"python {scripts_dir}/run_reddit_simulation.py --config {config_path}",
                "parallel": f"python {scripts_dir}/run_parallel_simulation.py --config {config_path}",
            },
            "instructions": (
                f"1. conda 환경 활성화: conda activate MiroFish\n"
                f"2. 시뮬레이션 실행 (스크립트 위치: {scripts_dir}):\n"
                f"   - Twitter만 실행: python {scripts_dir}/run_twitter_simulation.py --config {config_path}\n"
                f"   - Reddit만 실행: python {scripts_dir}/run_reddit_simulation.py --config {config_path}\n"
                f"   - 양 플랫폼 병렬 실행: python {scripts_dir}/run_parallel_simulation.py --config {config_path}"
            )
        }
