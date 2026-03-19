"""
시뮬레이션 관련 API 라우트
Step2: Zep 엔티티 읽기 및 필터링, OASIS 시뮬레이션 준비 및 실행 (전체 자동화)
"""

import os
import traceback
from flask import request, jsonify, send_file

from . import simulation_bp
from ..config import Config
from ..services.zep_entity_reader import ZepEntityReader
from ..services.zep_graph_memory_updater import is_system_instruction_content
from ..services.oasis_profile_generator import OasisProfileGenerator
from ..services.simulation_manager import SimulationManager, SimulationStatus
from ..services.simulation_runner import SimulationRunner, RunnerStatus
from ..utils.logger import get_logger
from ..models.project import ProjectManager

logger = get_logger('mirofish.api.simulation')


# Interview 프롬프트 최적화 접두사
# 이 접두사를 추가하면 Agent가 도구를 호출하지 않고 텍스트로 직접 응답
INTERVIEW_PROMPT_PREFIX = "당신의 캐릭터 설정, 모든 과거 기억과 행동을 바탕으로, 어떠한 도구도 사용하지 말고 텍스트로 직접 답해주세요: "


def optimize_interview_prompt(prompt: str) -> str:
    """
    Interview 질문 최적화, Agent가 도구를 호출하지 않도록 접두사 추가

    Args:
        prompt: 원본 질문

    Returns:
        최적화된 질문
    """
    if not prompt:
        return prompt
    # 접두사 중복 추가 방지
    if prompt.startswith(INTERVIEW_PROMPT_PREFIX):
        return prompt
    return f"{INTERVIEW_PROMPT_PREFIX}{prompt}"


# ============== 엔티티 읽기 인터페이스 ==============

@simulation_bp.route('/entities/<graph_id>', methods=['GET'])
def get_graph_entities(graph_id: str):
    """
    그래프의 모든 엔티티 가져오기 (필터링됨)

    미리 정의된 엔티티 유형에 맞는 노드만 반환 (Labels가 Entity만이 아닌 노드)

    Query 파라미터:
        entity_types: 쉼표로 구분된 엔티티 유형 목록 (선택 사항, 추가 필터링용)
        enrich: 관련 엣지 정보 조회 여부 (기본값 true)
    """
    try:
        if not Config.ZEP_API_KEY:
            return jsonify({
                "success": False,
                "error": "ZEP_API_KEY가 설정되지 않음"
            }), 500

        entity_types_str = request.args.get('entity_types', '')
        entity_types = [t.strip() for t in entity_types_str.split(',') if t.strip()] if entity_types_str else None
        enrich = request.args.get('enrich', 'true').lower() == 'true'

        logger.info(f"그래프 엔티티 가져오기: graph_id={graph_id}, entity_types={entity_types}, enrich={enrich}")

        reader = ZepEntityReader()
        result = reader.filter_defined_entities(
            graph_id=graph_id,
            defined_entity_types=entity_types,
            enrich_with_edges=enrich
        )

        return jsonify({
            "success": True,
            "data": result.to_dict()
        })

    except Exception as e:
        logger.error(f"그래프 엔티티 가져오기 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/entities/<graph_id>/<entity_uuid>', methods=['GET'])
def get_entity_detail(graph_id: str, entity_uuid: str):
    """단일 엔티티 상세 정보 가져오기"""
    try:
        if not Config.ZEP_API_KEY:
            return jsonify({
                "success": False,
                "error": "ZEP_API_KEY가 설정되지 않음"
            }), 500

        reader = ZepEntityReader()
        entity = reader.get_entity_with_context(graph_id, entity_uuid)

        if not entity:
            return jsonify({
                "success": False,
                "error": f"엔티티가 없음: {entity_uuid}"
            }), 404

        return jsonify({
            "success": True,
            "data": entity.to_dict()
        })

    except Exception as e:
        logger.error(f"엔티티 상세 정보 가져오기 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/entities/<graph_id>/by-type/<entity_type>', methods=['GET'])
def get_entities_by_type(graph_id: str, entity_type: str):
    """지정된 유형의 모든 엔티티 가져오기"""
    try:
        if not Config.ZEP_API_KEY:
            return jsonify({
                "success": False,
                "error": "ZEP_API_KEY가 설정되지 않음"
            }), 500

        enrich = request.args.get('enrich', 'true').lower() == 'true'

        reader = ZepEntityReader()
        entities = reader.get_entities_by_type(
            graph_id=graph_id,
            entity_type=entity_type,
            enrich_with_edges=enrich
        )

        return jsonify({
            "success": True,
            "data": {
                "entity_type": entity_type,
                "count": len(entities),
                "entities": [e.to_dict() for e in entities]
            }
        })

    except Exception as e:
        logger.error(f"엔티티 가져오기 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== 시뮬레이션 관리 인터페이스 ==============

@simulation_bp.route('/create', methods=['POST'])
def create_simulation():
    """
    새 시뮬레이션 생성

    참고: max_rounds 등의 파라미터는 LLM이 지능적으로 생성하므로 수동 설정 불필요

    요청 (JSON):
        {
            "project_id": "proj_xxxx",      // 필수
            "graph_id": "mirofish_xxxx",    // 선택 사항, 미제공 시 project에서 가져옴
            "enable_twitter": true,          // 선택 사항, 기본값 true
            "enable_reddit": true            // 선택 사항, 기본값 true
        }

    반환:
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "project_id": "proj_xxxx",
                "graph_id": "mirofish_xxxx",
                "status": "created",
                "enable_twitter": true,
                "enable_reddit": true,
                "created_at": "2025-12-01T10:00:00"
            }
        }
    """
    try:
        data = request.get_json() or {}

        project_id = data.get('project_id')
        if not project_id:
            return jsonify({
                "success": False,
                "error": "project_id를 제공해주세요"
            }), 400

        project = ProjectManager.get_project(project_id)
        if not project:
            return jsonify({
                "success": False,
                "error": f"프로젝트가 없음: {project_id}"
            }), 404

        graph_id = data.get('graph_id') or project.graph_id
        if not graph_id:
            return jsonify({
                "success": False,
                "error": "프로젝트 그래프가 아직 구축되지 않았습니다. /api/graph/build를 먼저 호출해주세요"
            }), 400

        manager = SimulationManager()
        state = manager.create_simulation(
            project_id=project_id,
            graph_id=graph_id,
            enable_twitter=data.get('enable_twitter', True),
            enable_reddit=data.get('enable_reddit', True),
        )

        return jsonify({
            "success": True,
            "data": state.to_dict()
        })

    except Exception as e:
        logger.error(f"시뮬레이션 생성 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


def _check_simulation_prepared(simulation_id: str) -> tuple:
    """
    시뮬레이션 준비 완료 여부 확인

    확인 조건:
    1. state.json이 존재하고 status가 "ready"
    2. 필요 파일 존재: reddit_profiles.json, twitter_profiles.csv, simulation_config.json

    참고: 실행 스크립트(run_*.py)는 backend/scripts/ 디렉토리에 유지, 시뮬레이션 디렉토리에 복사하지 않음

    Args:
        simulation_id: 시뮬레이션 ID

    Returns:
        (is_prepared: bool, info: dict)
    """
    import os
    from ..config import Config

    simulation_dir = os.path.join(Config.OASIS_SIMULATION_DATA_DIR, simulation_id)

    # 디렉토리 존재 확인
    if not os.path.exists(simulation_dir):
        return False, {"reason": "시뮬레이션 디렉토리가 없음"}

    # 필수 파일 목록 (스크립트 제외, 스크립트는 backend/scripts/에 위치)
    required_files = [
        "state.json",
        "simulation_config.json",
        "reddit_profiles.json",
        "twitter_profiles.csv"
    ]
    
    # 파일 존재 확인
    existing_files = []
    missing_files = []
    for f in required_files:
        file_path = os.path.join(simulation_dir, f)
        if os.path.exists(file_path):
            existing_files.append(f)
        else:
            missing_files.append(f)

    if missing_files:
        return False, {
            "reason": "필수 파일 누락",
            "missing_files": missing_files,
            "existing_files": existing_files
        }

    # state.json의 상태 확인
    state_file = os.path.join(simulation_dir, "state.json")
    try:
        import json
        with open(state_file, 'r', encoding='utf-8') as f:
            state_data = json.load(f)

        status = state_data.get("status", "")
        config_generated = state_data.get("config_generated", False)

        # 상세 로그
        logger.debug(f"시뮬레이션 준비 상태 확인: {simulation_id}, status={status}, config_generated={config_generated}")

        # config_generated=True이고 파일이 있으면 준비 완료로 간주
        # 다음 상태는 모두 준비 완료를 의미:
        # - ready: 준비 완료, 실행 가능
        # - preparing: config_generated=True이면 완료됨
        # - running: 실행 중, 준비는 이미 완료됨
        # - completed: 실행 완료, 준비는 이미 완료됨
        # - stopped: 중지됨, 준비는 이미 완료됨
        # - failed: 실행 실패 (하지만 준비는 완료됨)
        prepared_statuses = ["ready", "preparing", "running", "completed", "stopped", "failed"]
        if status in prepared_statuses and config_generated:
            # 파일 통계 정보 가져오기
            profiles_file = os.path.join(simulation_dir, "reddit_profiles.json")
            config_file = os.path.join(simulation_dir, "simulation_config.json")

            profiles_count = 0
            if os.path.exists(profiles_file):
                with open(profiles_file, 'r', encoding='utf-8') as f:
                    profiles_data = json.load(f)
                    profiles_count = len(profiles_data) if isinstance(profiles_data, list) else 0

            # 상태가 preparing이지만 파일이 완료된 경우 자동으로 ready로 업데이트
            if status == "preparing":
                try:
                    state_data["status"] = "ready"
                    from datetime import datetime
                    state_data["updated_at"] = datetime.now().isoformat()
                    with open(state_file, 'w', encoding='utf-8') as f:
                        json.dump(state_data, f, ensure_ascii=False, indent=2)
                    logger.info(f"시뮬레이션 상태 자동 업데이트: {simulation_id} preparing -> ready")
                    status = "ready"
                except Exception as e:
                    logger.warning(f"상태 자동 업데이트 실패: {e}")

            logger.info(f"시뮬레이션 {simulation_id} 확인 결과: 준비 완료 (status={status}, config_generated={config_generated})")
            return True, {
                "status": status,
                "entities_count": state_data.get("entities_count", 0),
                "profiles_count": profiles_count,
                "entity_types": state_data.get("entity_types", []),
                "config_generated": config_generated,
                "created_at": state_data.get("created_at"),
                "updated_at": state_data.get("updated_at"),
                "existing_files": existing_files
            }
        else:
            logger.warning(f"시뮬레이션 {simulation_id} 확인 결과: 미준비 (status={status}, config_generated={config_generated})")
            return False, {
                "reason": f"상태가 준비 목록에 없거나 config_generated가 false임: status={status}, config_generated={config_generated}",
                "status": status,
                "config_generated": config_generated
            }

    except Exception as e:
        return False, {"reason": f"상태 파일 읽기 실패: {str(e)}"}


@simulation_bp.route('/prepare', methods=['POST'])
def prepare_simulation():
    """
    시뮬레이션 환경 준비 (비동기 작업, LLM이 모든 파라미터 지능적 생성)

    시간이 걸리는 작업으로, 인터페이스는 즉시 task_id를 반환,
    GET /api/simulation/prepare/status 로 진행 상태 조회

    특징:
    - 완료된 준비 작업 자동 감지, 중복 생성 방지
    - 이미 준비 완료된 경우 기존 결과 직접 반환
    - 강제 재생성 지원 (force_regenerate=true)

    단계:
    1. 완료된 준비 작업 존재 여부 확인
    2. Zep 그래프에서 엔티티 읽기 및 필터링
    3. 각 엔티티에 대한 OASIS Agent Profile 생성 (재시도 메커니즘 포함)
    4. LLM이 시뮬레이션 설정 지능적 생성 (재시도 메커니즘 포함)
    5. 설정 파일 및 사전 설정 스크립트 저장

    요청 (JSON):
        {
            "simulation_id": "sim_xxxx",                   // 필수, 시뮬레이션 ID
            "entity_types": ["Student", "PublicFigure"],  // 선택 사항, 엔티티 유형 지정
            "use_llm_for_profiles": true,                 // 선택 사항, LLM으로 프로필 생성 여부
            "parallel_profile_count": 5,                  // 선택 사항, 병렬 프로필 생성 수, 기본값 5
            "force_regenerate": false                     // 선택 사항, 강제 재생성, 기본값 false
        }

    반환:
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "task_id": "task_xxxx",           // 새 작업 시 반환
                "status": "preparing|ready",
                "message": "준비 작업 시작됨|기존 준비 작업 있음",
                "already_prepared": true|false    // 이미 준비 완료 여부
            }
        }
    """
    import threading
    import os
    from ..models.task import TaskManager, TaskStatus
    from ..config import Config

    try:
        data = request.get_json() or {}

        simulation_id = data.get('simulation_id')
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": "simulation_id를 제공해주세요"
            }), 400

        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)

        if not state:
            return jsonify({
                "success": False,
                "error": f"시뮬레이션이 없음: {simulation_id}"
            }), 404

        # 강제 재생성 여부 확인
        force_regenerate = data.get('force_regenerate', False)
        logger.info(f"/prepare 요청 처리 시작: simulation_id={simulation_id}, force_regenerate={force_regenerate}")

        # 이미 준비 완료 여부 확인 (중복 생성 방지)
        if not force_regenerate:
            logger.debug(f"시뮬레이션 {simulation_id} 준비 완료 여부 확인 중...")
            is_prepared, prepare_info = _check_simulation_prepared(simulation_id)
            logger.debug(f"확인 결과: is_prepared={is_prepared}, prepare_info={prepare_info}")
            if is_prepared:
                logger.info(f"시뮬레이션 {simulation_id} 이미 준비 완료, 중복 생성 건너뜀")
                return jsonify({
                    "success": True,
                    "data": {
                        "simulation_id": simulation_id,
                        "status": "ready",
                        "message": "기존 준비 작업이 있으므로 재생성 불필요",
                        "already_prepared": True,
                        "prepare_info": prepare_info
                    }
                })
            else:
                logger.info(f"시뮬레이션 {simulation_id} 미준비, 준비 작업 시작")

        # 프로젝트에서 필요 정보 가져오기
        project = ProjectManager.get_project(state.project_id)
        if not project:
            return jsonify({
                "success": False,
                "error": f"프로젝트가 없음: {state.project_id}"
            }), 404

        # 시뮬레이션 요구사항 가져오기
        simulation_requirement = project.simulation_requirement or ""
        if not simulation_requirement:
            return jsonify({
                "success": False,
                "error": "프로젝트에 시뮬레이션 요구사항 설명 없음 (simulation_requirement)"
            }), 400

        # 문서 텍스트 가져오기
        document_text = ProjectManager.get_extracted_text(state.project_id) or ""
        
        entity_types_list = data.get('entity_types')
        use_llm_for_profiles = data.get('use_llm_for_profiles', True)
        parallel_profile_count = data.get('parallel_profile_count', 5)
        
        # ========== 동기 엔티티 수 가져오기 (백그라운드 작업 시작 전) ==========
        # 프론트엔드가 prepare 호출 후 즉시 예상 Agent 총 수를 가져올 수 있게 함
        try:
            logger.info(f"동기 엔티티 수 가져오기: graph_id={state.graph_id}")
            reader = ZepEntityReader()
            # 빠르게 엔티티 읽기 (엣지 정보 불필요, 수량만 통계)
            filtered_preview = reader.filter_defined_entities(
                graph_id=state.graph_id,
                defined_entity_types=entity_types_list,
                enrich_with_edges=False  # 엣지 정보 불가져옴, 속도 향상
            )
            # 엔티티 수를 상태에 저장 (프론트엔드에서 즉시 조회 가능)
            state.entities_count = filtered_preview.filtered_count
            state.entity_types = list(filtered_preview.entity_types)
            logger.info(f"예상 엔티티 수: {filtered_preview.filtered_count}, 유형: {filtered_preview.entity_types}")
        except Exception as e:
            logger.warning(f"동기 엔티티 수 가져오기 실패 (백그라운드 작업에서 재시도): {e}")
            # 실패해도 후속 프로세스에 영향 없음, 백그라운드 작업에서 다시 가져옴

        # 비동기 작업 생성
        task_manager = TaskManager()
        task_id = task_manager.create_task(
            task_type="simulation_prepare",
            metadata={
                "simulation_id": simulation_id,
                "project_id": state.project_id
            }
        )

        # 시뮬레이션 상태 업데이트 (사전 조회한 엔티티 수 포함)
        state.status = SimulationStatus.PREPARING
        manager._save_simulation_state(state)

        # 백그라운드 작업 정의
        def run_prepare():
            try:
                task_manager.update_task(
                    task_id,
                    status=TaskStatus.PROCESSING,
                    progress=0,
                    message="시뮬레이션 환경 준비 시작..."
                )

                # 시뮬레이션 준비 (진행 상태 콜백 포함)
                # 단계 진행 상태 상세 저장
                stage_details = {}

                def progress_callback(stage, progress, message, **kwargs):
                    # 전체 진행률 계산
                    stage_weights = {
                        "reading": (0, 20),           # 0-20%
                        "generating_profiles": (20, 70),  # 20-70%
                        "generating_config": (70, 90),    # 70-90%
                        "copying_scripts": (90, 100)       # 90-100%
                    }

                    start, end = stage_weights.get(stage, (0, 100))
                    current_progress = int(start + (end - start) * progress / 100)

                    # 상세 진행 정보 구성
                    stage_names = {
                        "reading": "그래프 엔티티 읽기",
                        "generating_profiles": "Agent 프로필 생성",
                        "generating_config": "시뮬레이션 설정 생성",
                        "copying_scripts": "시뮬레이션 스크립트 준비"
                    }

                    stage_index = list(stage_weights.keys()).index(stage) + 1 if stage in stage_weights else 1
                    total_stages = len(stage_weights)

                    # 단계 상세 업데이트
                    stage_details[stage] = {
                        "stage_name": stage_names.get(stage, stage),
                        "stage_progress": progress,
                        "current": kwargs.get("current", 0),
                        "total": kwargs.get("total", 0),
                        "item_name": kwargs.get("item_name", "")
                    }

                    # 상세 진행 정보 구성
                    detail = stage_details[stage]
                    progress_detail_data = {
                        "current_stage": stage,
                        "current_stage_name": stage_names.get(stage, stage),
                        "stage_index": stage_index,
                        "total_stages": total_stages,
                        "stage_progress": progress,
                        "current_item": detail["current"],
                        "total_items": detail["total"],
                        "item_description": message
                    }

                    # 간결한 메시지 구성
                    if detail["total"] > 0:
                        detailed_message = (
                            f"[{stage_index}/{total_stages}] {stage_names.get(stage, stage)}: "
                            f"{detail['current']}/{detail['total']} - {message}"
                        )
                    else:
                        detailed_message = f"[{stage_index}/{total_stages}] {stage_names.get(stage, stage)}: {message}"
                    
                    task_manager.update_task(
                        task_id,
                        progress=current_progress,
                        message=detailed_message,
                        progress_detail=progress_detail_data
                    )
                
                result_state = manager.prepare_simulation(
                    simulation_id=simulation_id,
                    simulation_requirement=simulation_requirement,
                    document_text=document_text,
                    defined_entity_types=entity_types_list,
                    use_llm_for_profiles=use_llm_for_profiles,
                    progress_callback=progress_callback,
                    parallel_profile_count=parallel_profile_count
                )
                
                # 작업 완료
                task_manager.complete_task(
                    task_id,
                    result=result_state.to_simple_dict()
                )

            except Exception as e:
                logger.error(f"시뮬레이션 준비 실패: {str(e)}")
                task_manager.fail_task(task_id, str(e))

                # 시뮬레이션 상태를 실패로 업데이트
                state = manager.get_simulation(simulation_id)
                if state:
                    state.status = SimulationStatus.FAILED
                    state.error = str(e)
                    manager._save_simulation_state(state)

        # 백그라운드 스레드 시작
        thread = threading.Thread(target=run_prepare, daemon=True)
        thread.start()

        return jsonify({
            "success": True,
            "data": {
                "simulation_id": simulation_id,
                "task_id": task_id,
                "status": "preparing",
                "message": "준비 작업 시작됨, /api/simulation/prepare/status 로 진행 상태 조회",
                "already_prepared": False,
                "expected_entities_count": state.entities_count,  # 예상 Agent 총 수
                "entity_types": state.entity_types  # 엔티티 유형 목록
            }
        })

    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 404

    except Exception as e:
        logger.error(f"준비 작업 시작 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/prepare/status', methods=['POST'])
def get_prepare_status():
    """
    준비 작업 진행 상태 조회

    두 가지 조회 방법 지원:
    1. task_id로 진행 중인 작업 진행률 조회
    2. simulation_id로 완료된 준비 작업 존재 여부 확인

    요청 (JSON):
        {
            "task_id": "task_xxxx",          // 선택 사항, prepare가 반환한 task_id
            "simulation_id": "sim_xxxx"      // 선택 사항, 시뮬레이션 ID (완료된 준비 확인용)
        }

    반환:
        {
            "success": true,
            "data": {
                "task_id": "task_xxxx",
                "status": "processing|completed|ready",
                "progress": 45,
                "message": "...",
                "already_prepared": true|false,  // 완료된 준비 작업 존재 여부
                "prepare_info": {...}            // 준비 완료 시 상세 정보
            }
        }
    """
    from ..models.task import TaskManager
    
    try:
        data = request.get_json() or {}
        
        task_id = data.get('task_id')
        simulation_id = data.get('simulation_id')
        
        # simulation_id가 있으면 먼저 준비 완료 여부 확인
        if simulation_id:
            is_prepared, prepare_info = _check_simulation_prepared(simulation_id)
            if is_prepared:
                return jsonify({
                    "success": True,
                    "data": {
                        "simulation_id": simulation_id,
                        "status": "ready",
                        "progress": 100,
                        "message": "기존 준비 작업이 있음",
                        "already_prepared": True,
                        "prepare_info": prepare_info
                    }
                })

        # task_id가 없으면 오류 반환
        if not task_id:
            if simulation_id:
                # simulation_id가 있지만 준비 미완료
                return jsonify({
                    "success": True,
                    "data": {
                        "simulation_id": simulation_id,
                        "status": "not_started",
                        "progress": 0,
                        "message": "준비 미시작, /api/simulation/prepare 를 호출해 시작해주세요",
                        "already_prepared": False
                    }
                })
            return jsonify({
                "success": False,
                "error": "task_id 또는 simulation_id를 제공해주세요"
            }), 400

        task_manager = TaskManager()
        task = task_manager.get_task(task_id)

        if not task:
            # 작업이 없지만 simulation_id가 있으면 준비 완료 여부 확인
            if simulation_id:
                is_prepared, prepare_info = _check_simulation_prepared(simulation_id)
                if is_prepared:
                    return jsonify({
                        "success": True,
                        "data": {
                            "simulation_id": simulation_id,
                            "task_id": task_id,
                            "status": "ready",
                            "progress": 100,
                            "message": "작업 완료 (준비 작업이 이미 존재함)",
                            "already_prepared": True,
                            "prepare_info": prepare_info
                        }
                    })

            return jsonify({
                "success": False,
                "error": f"작업이 없음: {task_id}"
            }), 404

        task_dict = task.to_dict()
        task_dict["already_prepared"] = False

        return jsonify({
            "success": True,
            "data": task_dict
        })

    except Exception as e:
        logger.error(f"작업 상태 조회 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@simulation_bp.route('/<simulation_id>', methods=['GET'])
def get_simulation(simulation_id: str):
    """시뮬레이션 상태 가져오기"""
    try:
        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)

        if not state:
            return jsonify({
                "success": False,
                "error": f"시뮬레이션이 없음: {simulation_id}"
            }), 404

        result = state.to_dict()

        # 시뮬레이션이 준비됐으면 실행 안내 추가
        if state.status == SimulationStatus.READY:
            result["run_instructions"] = manager.get_run_instructions(simulation_id)
        
        return jsonify({
            "success": True,
            "data": result
        })
        
    except Exception as e:
        logger.error(f"시뮬레이션 상태 가져오기 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/list', methods=['GET'])
def list_simulations():
    """
    모든 시뮬레이션 목록 조회

    Query 파라미터:
        project_id: 프로젝트 ID로 필터링 (선택 사항)
    """
    try:
        project_id = request.args.get('project_id')

        manager = SimulationManager()
        simulations = manager.list_simulations(project_id=project_id)

        return jsonify({
            "success": True,
            "data": [s.to_dict() for s in simulations],
            "count": len(simulations)
        })

    except Exception as e:
        logger.error(f"시뮬레이션 목록 조회 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


def _get_report_id_for_simulation(simulation_id: str) -> str:
    """
    시뮬레이션에 해당하는 최신 report_id 가져오기

    reports 디렉토리를 순회하여 simulation_id와 일치하는 report를 찾고,
    여러 개 있으면 가장 최신의 것을 반환 (created_at 기준 정렬)

    Args:
        simulation_id: 시뮬레이션 ID

    Returns:
        report_id 또는 None
    """
    import json
    from datetime import datetime

    # reports 디렉토리 경로: backend/uploads/reports
    # __file__은 app/api/simulation.py, backend/까지 두 단계 올라가야 함
    reports_dir = os.path.join(os.path.dirname(__file__), '../../uploads/reports')
    if not os.path.exists(reports_dir):
        return None

    matching_reports = []

    try:
        for report_folder in os.listdir(reports_dir):
            report_path = os.path.join(reports_dir, report_folder)
            if not os.path.isdir(report_path):
                continue

            meta_file = os.path.join(report_path, "meta.json")
            if not os.path.exists(meta_file):
                continue

            try:
                with open(meta_file, 'r', encoding='utf-8') as f:
                    meta = json.load(f)

                if meta.get("simulation_id") == simulation_id:
                    matching_reports.append({
                        "report_id": meta.get("report_id"),
                        "created_at": meta.get("created_at", ""),
                        "status": meta.get("status", "")
                    })
            except Exception:
                continue

        if not matching_reports:
            return None

        # 생성 시간 역순 정렬, 최신 항목 반환
        matching_reports.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return matching_reports[0].get("report_id")

    except Exception as e:
        logger.warning(f"시뮬레이션 {simulation_id}의 report 찾기 실패: {e}")
        return None


@simulation_bp.route('/history', methods=['GET'])
def get_simulation_history():
    """
    과거 시뮬레이션 목록 조회 (프로젝트 상세 포함)

    홈페이지 과거 프로젝트 표시용, 프로젝트 이름, 설명 등 풍부한 정보 포함 시뮬레이션 목록 반환

    Query 파라미터:
        limit: 반환 수량 제한 (기본값 20)

    반환:
        {
            "success": true,
            "data": [
                {
                    "simulation_id": "sim_xxxx",
                    "project_id": "proj_xxxx",
                    "project_name": "프로젝트명",
                    "simulation_requirement": "만약...",
                    "status": "completed",
                    "entities_count": 68,
                    "profiles_count": 68,
                    "entity_types": ["Student", "Professor", ...],
                    "created_at": "2024-12-10",
                    "updated_at": "2024-12-10",
                    "total_rounds": 120,
                    "current_round": 120,
                    "report_id": "report_xxxx",
                    "version": "v1.0.2"
                },
                ...
            ],
            "count": 7
        }
    """
    try:
        limit = request.args.get('limit', 20, type=int)

        manager = SimulationManager()
        simulations = manager.list_simulations()[:limit]

        # 시뮬레이션 데이터 강화, Simulation 파일에서만 읽기
        enriched_simulations = []
        for sim in simulations:
            sim_dict = sim.to_dict()

            # 시뮬레이션 설정 정보 가져오기 (simulation_config.json에서 simulation_requirement 읽기)
            config = manager.get_simulation_config(sim.simulation_id)
            if config:
                sim_dict["simulation_requirement"] = config.get("simulation_requirement", "")
                time_config = config.get("time_config", {})
                sim_dict["total_simulation_hours"] = time_config.get("total_simulation_hours", 0)
                # 추천 라운드 수 (대체값)
                recommended_rounds = int(
                    time_config.get("total_simulation_hours", 0) * 60 /
                    max(time_config.get("minutes_per_round", 60), 1)
                )
            else:
                sim_dict["simulation_requirement"] = ""
                sim_dict["total_simulation_hours"] = 0
                recommended_rounds = 0

            # 실행 상태 가져오기 (run_state.json에서 사용자가 설정한 실제 라운드 수 읽기)
            run_state = SimulationRunner.get_run_state(sim.simulation_id)
            if run_state:
                sim_dict["current_round"] = run_state.current_round
                sim_dict["runner_status"] = run_state.runner_status.value
                # 사용자가 설정한 total_rounds 사용, 없으면 추천 라운드 수 사용
                sim_dict["total_rounds"] = run_state.total_rounds if run_state.total_rounds > 0 else recommended_rounds
            else:
                sim_dict["current_round"] = 0
                sim_dict["runner_status"] = "idle"
                sim_dict["total_rounds"] = recommended_rounds

            # 연결된 프로젝트의 파일 목록 가져오기 (최대 3개)
            project = ProjectManager.get_project(sim.project_id)
            if project and hasattr(project, 'files') and project.files:
                sim_dict["files"] = [
                    {"filename": f.get("filename", "알 수 없는 파일")}
                    for f in project.files[:3]
                ]
            else:
                sim_dict["files"] = []

            # 연결된 report_id 가져오기 (해당 시뮬레이션의 최신 report 찾기)
            sim_dict["report_id"] = _get_report_id_for_simulation(sim.simulation_id)

            # 버전 번호 추가
            sim_dict["version"] = "v1.0.2"

            # 날짜 형식 변환
            try:
                created_date = sim_dict.get("created_at", "")[:10]
                sim_dict["created_date"] = created_date
            except:
                sim_dict["created_date"] = ""

            enriched_simulations.append(sim_dict)
        
        return jsonify({
            "success": True,
            "data": enriched_simulations,
            "count": len(enriched_simulations)
        })
        
    except Exception as e:
        logger.error(f"과거 시뮬레이션 가져오기 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/profiles', methods=['GET'])
def get_simulation_profiles(simulation_id: str):
    """
    시뮬레이션의 Agent Profile 가져오기

    Query 파라미터:
        platform: 플랫폼 유형 (reddit/twitter, 기본값 reddit)
    """
    try:
        platform = request.args.get('platform', 'reddit')

        manager = SimulationManager()
        profiles = manager.get_profiles(simulation_id, platform=platform)

        return jsonify({
            "success": True,
            "data": {
                "platform": platform,
                "count": len(profiles),
                "profiles": profiles
            }
        })

    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 404

    except Exception as e:
        logger.error(f"Profile 가져오기 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/profiles/realtime', methods=['GET'])
def get_simulation_profiles_realtime(simulation_id: str):
    """
    시뮬레이션의 Agent Profile 실시간 가져오기 (생성 과정 중 실시간 진행 상태 확인용)

    /profiles 인터페이스와의 차이점:
    - 파일 직접 읽기, SimulationManager 거치지 않음
    - 생성 과정 중 실시간 확인에 적합
    - 추가 메타데이터 반환 (파일 수정 시간, 생성 중 여부 등)

    Query 파라미터:
        platform: 플랫폼 유형 (reddit/twitter, 기본값 reddit)

    반환:
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "platform": "reddit",
                "count": 15,
                "total_expected": 93,  // 예상 총 수 (있는 경우)
                "is_generating": true,  // 생성 중 여부
                "file_exists": true,
                "file_modified_at": "2025-12-04T18:20:00",
                "profiles": [...]
            }
        }
    """
    import json
    import csv
    from datetime import datetime

    try:
        platform = request.args.get('platform', 'reddit')

        # 시뮬레이션 디렉토리 가져오기
        sim_dir = os.path.join(Config.OASIS_SIMULATION_DATA_DIR, simulation_id)

        if not os.path.exists(sim_dir):
            return jsonify({
                "success": False,
                "error": f"시뮬레이션이 없음: {simulation_id}"
            }), 404

        # 파일 경로 결정
        if platform == "reddit":
            profiles_file = os.path.join(sim_dir, "reddit_profiles.json")
        else:
            profiles_file = os.path.join(sim_dir, "twitter_profiles.csv")
        
        # 파일 존재 확인
        file_exists = os.path.exists(profiles_file)
        profiles = []
        file_modified_at = None

        if file_exists:
            # 파일 수정 시간 가져오기
            file_stat = os.stat(profiles_file)
            file_modified_at = datetime.fromtimestamp(file_stat.st_mtime).isoformat()

            try:
                if platform == "reddit":
                    with open(profiles_file, 'r', encoding='utf-8') as f:
                        profiles = json.load(f)
                else:
                    with open(profiles_file, 'r', encoding='utf-8') as f:
                        reader = csv.DictReader(f)
                        profiles = list(reader)
            except (json.JSONDecodeError, Exception) as e:
                logger.warning(f"profiles 파일 읽기 실패 (쓰기 중일 수 있음): {e}")
                profiles = []

        # 생성 중 여부 확인 (state.json으로 판단)
        is_generating = False
        total_expected = None

        state_file = os.path.join(sim_dir, "state.json")
        if os.path.exists(state_file):
            try:
                with open(state_file, 'r', encoding='utf-8') as f:
                    state_data = json.load(f)
                    status = state_data.get("status", "")
                    is_generating = status == "preparing"
                    total_expected = state_data.get("entities_count")
            except Exception:
                pass

        return jsonify({
            "success": True,
            "data": {
                "simulation_id": simulation_id,
                "platform": platform,
                "count": len(profiles),
                "total_expected": total_expected,
                "is_generating": is_generating,
                "file_exists": file_exists,
                "file_modified_at": file_modified_at,
                "profiles": profiles
            }
        })

    except Exception as e:
        logger.error(f"Profile 실시간 가져오기 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/config/realtime', methods=['GET'])
def get_simulation_config_realtime(simulation_id: str):
    """
    시뮬레이션 설정 실시간 가져오기 (생성 과정 중 실시간 진행 상태 확인용)

    /config 인터페이스와의 차이점:
    - 파일 직접 읽기, SimulationManager 거치지 않음
    - 생성 과정 중 실시간 확인에 적합
    - 추가 메타데이터 반환 (파일 수정 시간, 생성 중 여부 등)
    - 설정이 아직 완성되지 않아도 부분 정보 반환 가능

    반환:
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "file_exists": true,
                "file_modified_at": "2025-12-04T18:20:00",
                "is_generating": true,  // 생성 중 여부
                "generation_stage": "generating_config",  // 현재 생성 단계
                "config": {...}  // 설정 내용 (있는 경우)
            }
        }
    """
    import json
    from datetime import datetime

    try:
        # 시뮬레이션 디렉토리 가져오기
        sim_dir = os.path.join(Config.OASIS_SIMULATION_DATA_DIR, simulation_id)

        if not os.path.exists(sim_dir):
            return jsonify({
                "success": False,
                "error": f"시뮬레이션이 없음: {simulation_id}"
            }), 404

        # 설정 파일 경로
        config_file = os.path.join(sim_dir, "simulation_config.json")
        
        # 파일 존재 확인
        file_exists = os.path.exists(config_file)
        config = None
        file_modified_at = None
        
        if file_exists:
            # 파일 수정 시간 가져오기
            file_stat = os.stat(config_file)
            file_modified_at = datetime.fromtimestamp(file_stat.st_mtime).isoformat()

            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
            except (json.JSONDecodeError, Exception) as e:
                logger.warning(f"config 파일 읽기 실패 (쓰기 중일 수 있음): {e}")
                config = None

        # 생성 중 여부 확인 (state.json으로 판단)
        is_generating = False
        generation_stage = None
        config_generated = False

        state_file = os.path.join(sim_dir, "state.json")
        if os.path.exists(state_file):
            try:
                with open(state_file, 'r', encoding='utf-8') as f:
                    state_data = json.load(f)
                    status = state_data.get("status", "")
                    is_generating = status == "preparing"
                    config_generated = state_data.get("config_generated", False)

                    # 현재 단계 판단
                    if is_generating:
                        if state_data.get("profiles_generated", False):
                            generation_stage = "generating_config"
                        else:
                            generation_stage = "generating_profiles"
                    elif status == "ready":
                        generation_stage = "completed"
            except Exception:
                pass

        # 반환 데이터 구성
        response_data = {
            "simulation_id": simulation_id,
            "file_exists": file_exists,
            "file_modified_at": file_modified_at,
            "is_generating": is_generating,
            "generation_stage": generation_stage,
            "config_generated": config_generated,
            "config": config
        }

        # 설정이 있으면 주요 통계 정보 추출
        if config:
            response_data["summary"] = {
                "total_agents": len(config.get("agent_configs", [])),
                "simulation_hours": config.get("time_config", {}).get("total_simulation_hours"),
                "initial_posts_count": len(config.get("event_config", {}).get("initial_posts", [])),
                "hot_topics_count": len(config.get("event_config", {}).get("hot_topics", [])),
                "has_twitter_config": "twitter_config" in config,
                "has_reddit_config": "reddit_config" in config,
                "generated_at": config.get("generated_at"),
                "llm_model": config.get("llm_model")
            }

        return jsonify({
            "success": True,
            "data": response_data
        })

    except Exception as e:
        logger.error(f"Config 실시간 가져오기 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/config', methods=['GET'])
def get_simulation_config(simulation_id: str):
    """
    시뮬레이션 설정 가져오기 (LLM이 지능적으로 생성한 전체 설정)

    반환 포함:
        - time_config: 시간 설정 (시뮬레이션 시간, 라운드, 피크/저점 시간대)
        - agent_configs: 각 Agent의 활동 설정 (활성도, 발언 빈도, 입장 등)
        - event_config: 이벤트 설정 (초기 게시물, 핫 토픽)
        - platform_configs: 플랫폼 설정
        - generation_reasoning: LLM의 설정 추론 설명
    """
    try:
        manager = SimulationManager()
        config = manager.get_simulation_config(simulation_id)

        if not config:
            return jsonify({
                "success": False,
                "error": f"시뮬레이션 설정이 없음, /prepare 인터페이스를 먼저 호출해주세요"
            }), 404
        
        return jsonify({
            "success": True,
            "data": config
        })
        
    except Exception as e:
        logger.error(f"설정 가져오기 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/config/download', methods=['GET'])
def download_simulation_config(simulation_id: str):
    """시뮬레이션 설정 파일 다운로드"""
    try:
        manager = SimulationManager()
        sim_dir = manager._get_simulation_dir(simulation_id)
        config_path = os.path.join(sim_dir, "simulation_config.json")

        if not os.path.exists(config_path):
            return jsonify({
                "success": False,
                "error": "설정 파일이 없음, /prepare 인터페이스를 먼저 호출해주세요"
            }), 404

        return send_file(
            config_path,
            as_attachment=True,
            download_name="simulation_config.json"
        )

    except Exception as e:
        logger.error(f"설정 다운로드 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/script/<script_name>/download', methods=['GET'])
def download_simulation_script(script_name: str):
    """
    시뮬레이션 실행 스크립트 파일 다운로드 (공통 스크립트, backend/scripts/ 위치)

    script_name 선택 가능 값:
        - run_twitter_simulation.py
        - run_reddit_simulation.py
        - run_parallel_simulation.py
        - action_logger.py
    """
    try:
        # 스크립트는 backend/scripts/ 디렉토리에 위치
        scripts_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../scripts'))

        # 스크립트 이름 검증
        allowed_scripts = [
            "run_twitter_simulation.py",
            "run_reddit_simulation.py",
            "run_parallel_simulation.py",
            "action_logger.py"
        ]

        if script_name not in allowed_scripts:
            return jsonify({
                "success": False,
                "error": f"알 수 없는 스크립트: {script_name}, 선택 가능: {allowed_scripts}"
            }), 400

        script_path = os.path.join(scripts_dir, script_name)

        if not os.path.exists(script_path):
            return jsonify({
                "success": False,
                "error": f"스크립트 파일이 없음: {script_name}"
            }), 404

        return send_file(
            script_path,
            as_attachment=True,
            download_name=script_name
        )

    except Exception as e:
        logger.error(f"스크립트 다운로드 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== Profile 생성 인터페이스 (단독 사용) ==============

@simulation_bp.route('/generate-profiles', methods=['POST'])
def generate_profiles():
    """
    그래프에서 직접 OASIS Agent Profile 생성 (시뮬레이션 생성 없이)

    요청 (JSON):
        {
            "graph_id": "mirofish_xxxx",     // 필수
            "entity_types": ["Student"],      // 선택 사항
            "use_llm": true,                  // 선택 사항
            "platform": "reddit"              // 선택 사항
        }
    """
    try:
        data = request.get_json() or {}

        graph_id = data.get('graph_id')
        if not graph_id:
            return jsonify({
                "success": False,
                "error": "graph_id를 제공해주세요"
            }), 400

        entity_types = data.get('entity_types')
        use_llm = data.get('use_llm', True)
        platform = data.get('platform', 'reddit')

        reader = ZepEntityReader()
        filtered = reader.filter_defined_entities(
            graph_id=graph_id,
            defined_entity_types=entity_types,
            enrich_with_edges=True
        )

        if filtered.filtered_count == 0:
            return jsonify({
                "success": False,
                "error": "조건에 맞는 엔티티를 찾을 수 없음"
            }), 400

        generator = OasisProfileGenerator()
        profiles = generator.generate_profiles_from_entities(
            entities=filtered.entities,
            use_llm=use_llm
        )

        if platform == "reddit":
            profiles_data = [p.to_reddit_format() for p in profiles]
        elif platform == "twitter":
            profiles_data = [p.to_twitter_format() for p in profiles]
        else:
            profiles_data = [p.to_dict() for p in profiles]

        return jsonify({
            "success": True,
            "data": {
                "platform": platform,
                "entity_types": list(filtered.entity_types),
                "count": len(profiles_data),
                "profiles": profiles_data
            }
        })

    except Exception as e:
        logger.error(f"Profile 생성 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== 시뮬레이션 실행 제어 인터페이스 ==============

@simulation_bp.route('/start', methods=['POST'])
def start_simulation():
    """
    시뮬레이션 실행 시작

    요청 (JSON):
        {
            "simulation_id": "sim_xxxx",          // 필수, 시뮬레이션 ID
            "platform": "parallel",                // 선택 사항: twitter / reddit / parallel (기본값)
            "max_rounds": 100,                     // 선택 사항: 최대 시뮬레이션 라운드 수, 너무 긴 시뮬레이션 제한용
            "enable_graph_memory_update": false,   // 선택 사항: Agent 활동을 Zep 그래프 메모리에 동적 업데이트 여부
            "force": false                         // 선택 사항: 강제 재시작 (실행 중인 시뮬레이션 중지 및 로그 정리)
        }

    force 파라미터 관련:
        - 활성화 시, 시뮬레이션이 실행 중이거나 완료된 경우 먼저 중지하고 실행 로그 정리
        - 정리 내용: run_state.json, actions.jsonl, simulation.log 등
        - 설정 파일(simulation_config.json)과 profile 파일은 정리하지 않음
        - 시뮬레이션을 재실행해야 하는 경우에 사용

    enable_graph_memory_update 관련:
        - 활성화 시, 시뮬레이션 중 모든 Agent의 활동(게시, 댓글, 좋아요 등)이 Zep 그래프에 실시간 업데이트
        - 그래프가 시뮬레이션 과정을 "기억"하게 하여 이후 분석이나 AI 대화에 활용 가능
        - 시뮬레이션과 연결된 프로젝트에 유효한 graph_id 필요
        - 배치 업데이트 메커니즘 사용, API 호출 횟수 감소

    반환:
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "runner_status": "running",
                "process_pid": 12345,
                "twitter_running": true,
                "reddit_running": true,
                "started_at": "2025-12-01T10:00:00",
                "graph_memory_update_enabled": true,  // 그래프 메모리 업데이트 활성화 여부
                "force_restarted": true               // 강제 재시작 여부
            }
        }
    """
    try:
        data = request.get_json() or {}

        simulation_id = data.get('simulation_id')
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": "simulation_id를 제공해주세요"
            }), 400

        platform = data.get('platform', 'parallel')
        max_rounds = data.get('max_rounds')  # 선택 사항: 최대 시뮬레이션 라운드 수
        enable_graph_memory_update = data.get('enable_graph_memory_update', False)  # 선택 사항: 그래프 메모리 업데이트 활성화 여부
        force = data.get('force', False)  # 선택 사항: 강제 재시작

        # max_rounds 파라미터 검증
        if max_rounds is not None:
            try:
                max_rounds = int(max_rounds)
                if max_rounds <= 0:
                    return jsonify({
                        "success": False,
                        "error": "max_rounds는 양의 정수여야 함"
                    }), 400
            except (ValueError, TypeError):
                return jsonify({
                    "success": False,
                    "error": "max_rounds는 유효한 정수여야 함"
                }), 400

        if platform not in ['twitter', 'reddit', 'parallel']:
            return jsonify({
                "success": False,
                "error": f"유효하지 않은 플랫폼 유형: {platform}, 선택 가능: twitter/reddit/parallel"
            }), 400

        # 시뮬레이션 준비 완료 여부 확인
        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)

        if not state:
            return jsonify({
                "success": False,
                "error": f"시뮬레이션이 없음: {simulation_id}"
            }), 404

        force_restarted = False

        # 상태 지능적 처리: 준비 완료된 경우 재시작 허용
        if state.status != SimulationStatus.READY:
            # 준비 완료 여부 확인
            is_prepared, prepare_info = _check_simulation_prepared(simulation_id)

            if is_prepared:
                # 준비 완료, 실행 중인 프로세스 확인
                if state.status == SimulationStatus.RUNNING:
                    # 시뮬레이션 프로세스가 실제로 실행 중인지 확인
                    run_state = SimulationRunner.get_run_state(simulation_id)
                    if run_state and run_state.runner_status.value == "running":
                        # 프로세스가 실제로 실행 중
                        if force:
                            # 강제 모드: 실행 중인 시뮬레이션 중지
                            logger.info(f"강제 모드: 실행 중인 시뮬레이션 중지 {simulation_id}")
                            try:
                                SimulationRunner.stop_simulation(simulation_id)
                            except Exception as e:
                                logger.warning(f"시뮬레이션 중지 시 경고: {str(e)}")
                        else:
                            return jsonify({
                                "success": False,
                                "error": f"시뮬레이션이 실행 중입니다. /stop 인터페이스로 먼저 중지하거나 force=true를 사용해 강제 재시작하세요"
                            }), 400

                # 강제 모드인 경우 실행 로그 정리
                if force:
                    logger.info(f"강제 모드: 시뮬레이션 로그 정리 {simulation_id}")
                    cleanup_result = SimulationRunner.cleanup_simulation_logs(simulation_id)
                    if not cleanup_result.get("success"):
                        logger.warning(f"로그 정리 시 경고: {cleanup_result.get('errors')}")
                    force_restarted = True

                # 프로세스가 없거나 종료된 경우 상태를 ready로 재설정
                logger.info(f"시뮬레이션 {simulation_id} 준비 완료, 상태를 ready로 재설정 (이전 상태: {state.status.value})")
                state.status = SimulationStatus.READY
                manager._save_simulation_state(state)
            else:
                # 준비 미완료
                return jsonify({
                    "success": False,
                    "error": f"시뮬레이션 미준비, 현재 상태: {state.status.value}, /prepare 인터페이스를 먼저 호출해주세요"
                }), 400

        # 그래프 ID 가져오기 (그래프 메모리 업데이트용)
        graph_id = None
        if enable_graph_memory_update:
            # 시뮬레이션 상태 또는 프로젝트에서 graph_id 가져오기
            graph_id = state.graph_id
            if not graph_id:
                # 프로젝트에서 가져오기 시도
                project = ProjectManager.get_project(state.project_id)
                if project:
                    graph_id = project.graph_id

            if not graph_id:
                return jsonify({
                    "success": False,
                    "error": "그래프 메모리 업데이트 활성화에는 유효한 graph_id가 필요합니다. 프로젝트의 그래프가 구축되었는지 확인해주세요"
                }), 400

            logger.info(f"그래프 메모리 업데이트 활성화: simulation_id={simulation_id}, graph_id={graph_id}")

        # 시뮬레이션 시작
        run_state = SimulationRunner.start_simulation(
            simulation_id=simulation_id,
            platform=platform,
            max_rounds=max_rounds,
            enable_graph_memory_update=enable_graph_memory_update,
            graph_id=graph_id
        )

        # 시뮬레이션 상태 업데이트
        state.status = SimulationStatus.RUNNING
        manager._save_simulation_state(state)

        response_data = run_state.to_dict()
        if max_rounds:
            response_data['max_rounds_applied'] = max_rounds
        response_data['graph_memory_update_enabled'] = enable_graph_memory_update
        response_data['force_restarted'] = force_restarted
        if enable_graph_memory_update:
            response_data['graph_id'] = graph_id

        return jsonify({
            "success": True,
            "data": response_data
        })

    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400

    except Exception as e:
        logger.error(f"시뮬레이션 시작 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/stop', methods=['POST'])
def stop_simulation():
    """
    시뮬레이션 중지

    요청 (JSON):
        {
            "simulation_id": "sim_xxxx"  // 필수, 시뮬레이션 ID
        }

    반환:
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "runner_status": "stopped",
                "completed_at": "2025-12-01T12:00:00"
            }
        }
    """
    try:
        data = request.get_json() or {}

        simulation_id = data.get('simulation_id')
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": "simulation_id를 제공해주세요"
            }), 400

        run_state = SimulationRunner.stop_simulation(simulation_id)

        # 시뮬레이션 상태 업데이트
        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)
        if state:
            state.status = SimulationStatus.PAUSED
            manager._save_simulation_state(state)
        
        return jsonify({
            "success": True,
            "data": run_state.to_dict()
        })
        
    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400
        
    except Exception as e:
        logger.error(f"시뮬레이션 중지 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== 실시간 상태 모니터링 인터페이스 ==============

@simulation_bp.route('/<simulation_id>/run-status', methods=['GET'])
def get_run_status(simulation_id: str):
    """
    시뮬레이션 실행 실시간 상태 가져오기 (프론트엔드 폴링용)

    반환:
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "runner_status": "running",
                "current_round": 5,
                "total_rounds": 144,
                "progress_percent": 3.5,
                "simulated_hours": 2,
                "total_simulation_hours": 72,
                "twitter_running": true,
                "reddit_running": true,
                "twitter_actions_count": 150,
                "reddit_actions_count": 200,
                "total_actions_count": 350,
                "started_at": "2025-12-01T10:00:00",
                "updated_at": "2025-12-01T10:30:00"
            }
        }
    """
    try:
        run_state = SimulationRunner.get_run_state(simulation_id)

        if not run_state:
            return jsonify({
                "success": True,
                "data": {
                    "simulation_id": simulation_id,
                    "runner_status": "idle",
                    "current_round": 0,
                    "total_rounds": 0,
                    "progress_percent": 0,
                    "twitter_actions_count": 0,
                    "reddit_actions_count": 0,
                    "total_actions_count": 0,
                }
            })

        return jsonify({
            "success": True,
            "data": run_state.to_dict()
        })

    except Exception as e:
        logger.error(f"실행 상태 가져오기 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/run-status/detail', methods=['GET'])
def get_run_status_detail(simulation_id: str):
    """
    시뮬레이션 실행 상세 상태 가져오기 (모든 동작 포함)

    프론트엔드 실시간 동적 표시용

    Query 파라미터:
        platform: 플랫폼 필터링 (twitter/reddit, 선택 사항)

    반환:
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "runner_status": "running",
                "current_round": 5,
                ...
                "all_actions": [
                    {
                        "round_num": 5,
                        "timestamp": "2025-12-01T10:30:00",
                        "platform": "twitter",
                        "agent_id": 3,
                        "agent_name": "Agent Name",
                        "action_type": "CREATE_POST",
                        "action_args": {"content": "..."},
                        "result": null,
                        "success": true
                    },
                    ...
                ],
                "twitter_actions": [...],  # Twitter 플랫폼의 모든 동작
                "reddit_actions": [...]    # Reddit 플랫폼의 모든 동작
            }
        }
    """
    try:
        run_state = SimulationRunner.get_run_state(simulation_id)
        platform_filter = request.args.get('platform')
        
        if not run_state:
            return jsonify({
                "success": True,
                "data": {
                    "simulation_id": simulation_id,
                    "runner_status": "idle",
                    "all_actions": [],
                    "twitter_actions": [],
                    "reddit_actions": []
                }
            })
        
        # 전체 동작 목록 가져오기
        all_actions = SimulationRunner.get_all_actions(
            simulation_id=simulation_id,
            platform=platform_filter
        )

        # 플랫폼별 동작 가져오기
        twitter_actions = SimulationRunner.get_all_actions(
            simulation_id=simulation_id,
            platform="twitter"
        ) if not platform_filter or platform_filter == "twitter" else []

        reddit_actions = SimulationRunner.get_all_actions(
            simulation_id=simulation_id,
            platform="reddit"
        ) if not platform_filter or platform_filter == "reddit" else []

        # 현재 라운드의 동작 가져오기 (recent_actions는 최신 라운드만 표시)
        current_round = run_state.current_round
        recent_actions = SimulationRunner.get_all_actions(
            simulation_id=simulation_id,
            platform=platform_filter,
            round_num=current_round
        ) if current_round > 0 else []

        # 기본 상태 정보 가져오기
        result = run_state.to_dict()
        result["all_actions"] = [a.to_dict() for a in all_actions]
        result["twitter_actions"] = [a.to_dict() for a in twitter_actions]
        result["reddit_actions"] = [a.to_dict() for a in reddit_actions]
        result["rounds_count"] = len(run_state.rounds)
        # recent_actions는 현재 최신 라운드의 두 플랫폼 내용만 표시
        result["recent_actions"] = [a.to_dict() for a in recent_actions]

        return jsonify({
            "success": True,
            "data": result
        })

    except Exception as e:
        logger.error(f"상세 상태 가져오기 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/actions', methods=['GET'])
def get_simulation_actions(simulation_id: str):
    """
    시뮬레이션의 Agent 동작 이력 가져오기

    Query 파라미터:
        limit: 반환 수량 (기본값 100)
        offset: 오프셋 (기본값 0)
        platform: 플랫폼 필터링 (twitter/reddit)
        agent_id: Agent ID 필터링
        round_num: 라운드 번호 필터링

    반환:
        {
            "success": true,
            "data": {
                "count": 100,
                "actions": [...]
            }
        }
    """
    try:
        limit = request.args.get('limit', 100, type=int)
        offset = request.args.get('offset', 0, type=int)
        platform = request.args.get('platform')
        agent_id = request.args.get('agent_id', type=int)
        round_num = request.args.get('round_num', type=int)
        
        actions = SimulationRunner.get_actions(
            simulation_id=simulation_id,
            limit=limit,
            offset=offset,
            platform=platform,
            agent_id=agent_id,
            round_num=round_num
        )
        
        return jsonify({
            "success": True,
            "data": {
                "count": len(actions),
                "actions": [a.to_dict() for a in actions]
            }
        })
        
    except Exception as e:
        logger.error(f"동작 이력 가져오기 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/timeline', methods=['GET'])
def get_simulation_timeline(simulation_id: str):
    """
    시뮬레이션 타임라인 가져오기 (라운드별 요약)

    프론트엔드 진행 표시줄 및 타임라인 뷰 표시용

    Query 파라미터:
        start_round: 시작 라운드 (기본값 0)
        end_round: 종료 라운드 (기본값 전체)

    각 라운드의 요약 정보 반환
    """
    try:
        start_round = request.args.get('start_round', 0, type=int)
        end_round = request.args.get('end_round', type=int)

        timeline = SimulationRunner.get_timeline(
            simulation_id=simulation_id,
            start_round=start_round,
            end_round=end_round
        )

        return jsonify({
            "success": True,
            "data": {
                "rounds_count": len(timeline),
                "timeline": timeline
            }
        })

    except Exception as e:
        logger.error(f"타임라인 가져오기 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/agent-stats', methods=['GET'])
def get_agent_stats(simulation_id: str):
    """
    각 Agent의 통계 정보 가져오기

    프론트엔드 Agent 활성도 순위, 동작 분포 등 표시용
    """
    try:
        stats = SimulationRunner.get_agent_stats(simulation_id)

        return jsonify({
            "success": True,
            "data": {
                "agents_count": len(stats),
                "stats": stats
            }
        })

    except Exception as e:
        logger.error(f"Agent 통계 가져오기 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== 데이터베이스 조회 인터페이스 ==============

@simulation_bp.route('/<simulation_id>/posts', methods=['GET'])
def get_simulation_posts(simulation_id: str):
    """
    시뮬레이션의 게시물 가져오기

    Query 파라미터:
        platform: 플랫폼 유형 (twitter/reddit)
        limit: 반환 수량 (기본값 50)
        offset: 오프셋

    게시물 목록 반환 (SQLite 데이터베이스에서 읽기)
    """
    try:
        platform = request.args.get('platform', 'reddit')
        limit = request.args.get('limit', 50, type=int)
        offset = request.args.get('offset', 0, type=int)
        
        sim_dir = os.path.join(
            os.path.dirname(__file__),
            f'../../uploads/simulations/{simulation_id}'
        )
        
        db_file = f"{platform}_simulation.db"
        db_path = os.path.join(sim_dir, db_file)
        
        if not os.path.exists(db_path):
            return jsonify({
                "success": True,
                "data": {
                    "platform": platform,
                    "count": 0,
                    "posts": [],
                    "message": "데이터베이스가 없음, 시뮬레이션이 아직 실행되지 않았을 수 있음"
                }
            })
        
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                SELECT * FROM post 
                ORDER BY created_at DESC 
                LIMIT ? OFFSET ?
            """, (limit, offset))
            
            all_posts = [dict(row) for row in cursor.fetchall()]
            # OASIS 지시문이 게시물 내용에 섞인 경우 필터링
            posts = [
                p for p in all_posts
                if not is_system_instruction_content(p.get("content", "") or p.get("text", ""))
            ]

            cursor.execute("SELECT COUNT(*) FROM post")
            total = cursor.fetchone()[0]
            
        except sqlite3.OperationalError:
            posts = []
            total = 0
        
        conn.close()
        
        return jsonify({
            "success": True,
            "data": {
                "platform": platform,
                "total": total,
                "count": len(posts),
                "posts": posts
            }
        })
        
    except Exception as e:
        logger.error(f"게시물 가져오기 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/comments', methods=['GET'])
def get_simulation_comments(simulation_id: str):
    """
    시뮬레이션의 댓글 가져오기 (Reddit 전용)

    Query 파라미터:
        post_id: 게시물 ID 필터링 (선택 사항)
        limit: 반환 수량
        offset: 오프셋
    """
    try:
        post_id = request.args.get('post_id')
        limit = request.args.get('limit', 50, type=int)
        offset = request.args.get('offset', 0, type=int)
        
        sim_dir = os.path.join(
            os.path.dirname(__file__),
            f'../../uploads/simulations/{simulation_id}'
        )
        
        db_path = os.path.join(sim_dir, "reddit_simulation.db")
        
        if not os.path.exists(db_path):
            return jsonify({
                "success": True,
                "data": {
                    "count": 0,
                    "comments": []
                }
            })
        
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        try:
            if post_id:
                cursor.execute("""
                    SELECT * FROM comment 
                    WHERE post_id = ?
                    ORDER BY created_at DESC 
                    LIMIT ? OFFSET ?
                """, (post_id, limit, offset))
            else:
                cursor.execute("""
                    SELECT * FROM comment 
                    ORDER BY created_at DESC 
                    LIMIT ? OFFSET ?
                """, (limit, offset))
            
            all_comments = [dict(row) for row in cursor.fetchall()]
            # OASIS 지시문이 댓글 내용에 섞인 경우 필터링
            comments = [
                c for c in all_comments
                if not is_system_instruction_content(c.get("content", "") or c.get("text", ""))
            ]

        except sqlite3.OperationalError:
            comments = []

        conn.close()

        return jsonify({
            "success": True,
            "data": {
                "count": len(comments),
                "comments": comments
            }
        })
        
    except Exception as e:
        logger.error(f"댓글 가져오기 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== Interview 인터뷰 인터페이스 ==============

@simulation_bp.route('/interview', methods=['POST'])
def interview_agent():
    """
    단일 Agent 인터뷰

    참고: 이 기능은 시뮬레이션 환경이 실행 중 상태여야 함 (시뮬레이션 루프 완료 후 명령 대기 모드)

    요청 (JSON):
        {
            "simulation_id": "sim_xxxx",       // 필수, 시뮬레이션 ID
            "agent_id": 0,                     // 필수, Agent ID
            "prompt": "이 사안에 대해 어떻게 생각하세요?",  // 필수, 인터뷰 질문
            "platform": "twitter",             // 선택 사항, 플랫폼 지정 (twitter/reddit)
                                               // 미지정 시: 양 플랫폼 시뮬레이션 동시 인터뷰
            "timeout": 60                      // 선택 사항, 타임아웃 (초), 기본값 60
        }

    반환 (platform 미지정, 양 플랫폼 모드):
        {
            "success": true,
            "data": {
                "agent_id": 0,
                "prompt": "이 사안에 대해 어떻게 생각하세요?",
                "result": {
                    "agent_id": 0,
                    "prompt": "...",
                    "platforms": {
                        "twitter": {"agent_id": 0, "response": "...", "platform": "twitter"},
                        "reddit": {"agent_id": 0, "response": "...", "platform": "reddit"}
                    }
                },
                "timestamp": "2025-12-08T10:00:01"
            }
        }

    반환 (platform 지정 시):
        {
            "success": true,
            "data": {
                "agent_id": 0,
                "prompt": "이 사안에 대해 어떻게 생각하세요?",
                "result": {
                    "agent_id": 0,
                    "response": "저는...",
                    "platform": "twitter",
                    "timestamp": "2025-12-08T10:00:00"
                },
                "timestamp": "2025-12-08T10:00:01"
            }
        }
    """
    try:
        data = request.get_json() or {}

        simulation_id = data.get('simulation_id')
        agent_id = data.get('agent_id')
        prompt = data.get('prompt')
        platform = data.get('platform')  # 선택 사항: twitter/reddit/None
        timeout = data.get('timeout', 60)

        if not simulation_id:
            return jsonify({
                "success": False,
                "error": "simulation_id를 제공해주세요"
            }), 400

        if agent_id is None:
            return jsonify({
                "success": False,
                "error": "agent_id를 제공해주세요"
            }), 400

        if not prompt:
            return jsonify({
                "success": False,
                "error": "prompt (인터뷰 질문)를 제공해주세요"
            }), 400

        # platform 파라미터 검증
        if platform and platform not in ("twitter", "reddit"):
            return jsonify({
                "success": False,
                "error": "platform 파라미터는 'twitter' 또는 'reddit'만 가능"
            }), 400

        # 환경 상태 확인
        if not SimulationRunner.check_env_alive(simulation_id):
            return jsonify({
                "success": False,
                "error": "시뮬레이션 환경이 실행 중이 아니거나 종료됨. 시뮬레이션이 완료되고 명령 대기 모드에 있는지 확인해주세요."
            }), 400

        # prompt 최적화, Agent가 도구를 호출하지 않도록 접두사 추가
        optimized_prompt = optimize_interview_prompt(prompt)
        
        result = SimulationRunner.interview_agent(
            simulation_id=simulation_id,
            agent_id=agent_id,
            prompt=optimized_prompt,
            platform=platform,
            timeout=timeout
        )

        return jsonify({
            "success": result.get("success", False),
            "data": result
        })
        
    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400
        
    except TimeoutError as e:
        return jsonify({
            "success": False,
            "error": f"Interview 응답 대기 타임아웃: {str(e)}"
        }), 504

    except Exception as e:
        logger.error(f"Interview 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/interview/batch', methods=['POST'])
def interview_agents_batch():
    """
    다수 Agent 일괄 인터뷰

    참고: 이 기능은 시뮬레이션 환경이 실행 중 상태여야 함

    요청 (JSON):
        {
            "simulation_id": "sim_xxxx",       // 필수, 시뮬레이션 ID
            "interviews": [                    // 필수, 인터뷰 목록
                {
                    "agent_id": 0,
                    "prompt": "A에 대해 어떻게 생각하세요?",
                    "platform": "twitter"      // 선택 사항, 해당 Agent의 인터뷰 플랫폼 지정
                },
                {
                    "agent_id": 1,
                    "prompt": "B에 대해 어떻게 생각하세요?"  // platform 미지정 시 기본값 사용
                }
            ],
            "platform": "reddit",              // 선택 사항, 기본 플랫폼 (각 항목의 platform으로 덮어쓰기)
                                               // 미지정 시: 양 플랫폼 시뮬레이션에서 각 Agent를 동시에 인터뷰
            "timeout": 120                     // 선택 사항, 타임아웃 (초), 기본값 120
        }

    반환:
        {
            "success": true,
            "data": {
                "interviews_count": 2,
                "result": {
                    "interviews_count": 4,
                    "results": {
                        "twitter_0": {"agent_id": 0, "response": "...", "platform": "twitter"},
                        "reddit_0": {"agent_id": 0, "response": "...", "platform": "reddit"},
                        "twitter_1": {"agent_id": 1, "response": "...", "platform": "twitter"},
                        "reddit_1": {"agent_id": 1, "response": "...", "platform": "reddit"}
                    }
                },
                "timestamp": "2025-12-08T10:00:01"
            }
        }
    """
    try:
        data = request.get_json() or {}

        simulation_id = data.get('simulation_id')
        interviews = data.get('interviews')
        platform = data.get('platform')  # 선택 사항: twitter/reddit/None
        timeout = data.get('timeout', 120)

        if not simulation_id:
            return jsonify({
                "success": False,
                "error": "simulation_id를 제공해주세요"
            }), 400

        if not interviews or not isinstance(interviews, list):
            return jsonify({
                "success": False,
                "error": "interviews (인터뷰 목록)를 제공해주세요"
            }), 400

        # platform 파라미터 검증
        if platform and platform not in ("twitter", "reddit"):
            return jsonify({
                "success": False,
                "error": "platform 파라미터는 'twitter' 또는 'reddit'만 가능"
            }), 400

        # 각 인터뷰 항목 검증
        for i, interview in enumerate(interviews):
            if 'agent_id' not in interview:
                return jsonify({
                    "success": False,
                    "error": f"인터뷰 목록 {i+1}번째 항목에 agent_id가 없음"
                }), 400
            if 'prompt' not in interview:
                return jsonify({
                    "success": False,
                    "error": f"인터뷰 목록 {i+1}번째 항목에 prompt가 없음"
                }), 400
            # 각 항목의 platform 검증 (있는 경우)
            item_platform = interview.get('platform')
            if item_platform and item_platform not in ("twitter", "reddit"):
                return jsonify({
                    "success": False,
                    "error": f"인터뷰 목록 {i+1}번째 항목의 platform은 'twitter' 또는 'reddit'만 가능"
                }), 400

        # 환경 상태 확인
        if not SimulationRunner.check_env_alive(simulation_id):
            return jsonify({
                "success": False,
                "error": "시뮬레이션 환경이 실행 중이 아니거나 종료됨. 시뮬레이션이 완료되고 명령 대기 모드에 있는지 확인해주세요."
            }), 400

        # 각 인터뷰 항목의 prompt 최적화, Agent가 도구를 호출하지 않도록 접두사 추가
        optimized_interviews = []
        for interview in interviews:
            optimized_interview = interview.copy()
            optimized_interview['prompt'] = optimize_interview_prompt(interview.get('prompt', ''))
            optimized_interviews.append(optimized_interview)

        result = SimulationRunner.interview_agents_batch(
            simulation_id=simulation_id,
            interviews=optimized_interviews,
            platform=platform,
            timeout=timeout
        )

        return jsonify({
            "success": result.get("success", False),
            "data": result
        })

    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400

    except TimeoutError as e:
        return jsonify({
            "success": False,
            "error": f"일괄 Interview 응답 대기 타임아웃: {str(e)}"
        }), 504

    except Exception as e:
        logger.error(f"일괄 Interview 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/interview/all', methods=['POST'])
def interview_all_agents():
    """
    전체 인터뷰 - 모든 Agent에게 동일한 질문으로 인터뷰

    참고: 이 기능은 시뮬레이션 환경이 실행 중 상태여야 함

    요청 (JSON):
        {
            "simulation_id": "sim_xxxx",            // 필수, 시뮬레이션 ID
            "prompt": "이 사안 전체에 대해 어떻게 생각하세요?",  // 필수, 인터뷰 질문 (모든 Agent가 동일한 질문 받음)
            "platform": "reddit",                   // 선택 사항, 플랫폼 지정 (twitter/reddit)
                                                    // 미지정 시: 양 플랫폼 시뮬레이션에서 각 Agent를 동시에 인터뷰
            "timeout": 180                          // 선택 사항, 타임아웃 (초), 기본값 180
        }

    반환:
        {
            "success": true,
            "data": {
                "interviews_count": 50,
                "result": {
                    "interviews_count": 100,
                    "results": {
                        "twitter_0": {"agent_id": 0, "response": "...", "platform": "twitter"},
                        "reddit_0": {"agent_id": 0, "response": "...", "platform": "reddit"},
                        ...
                    }
                },
                "timestamp": "2025-12-08T10:00:01"
            }
        }
    """
    try:
        data = request.get_json() or {}

        simulation_id = data.get('simulation_id')
        prompt = data.get('prompt')
        platform = data.get('platform')  # 선택 사항: twitter/reddit/None
        timeout = data.get('timeout', 180)

        if not simulation_id:
            return jsonify({
                "success": False,
                "error": "simulation_id를 제공해주세요"
            }), 400

        if not prompt:
            return jsonify({
                "success": False,
                "error": "prompt (인터뷰 질문)를 제공해주세요"
            }), 400

        # platform 파라미터 검증
        if platform and platform not in ("twitter", "reddit"):
            return jsonify({
                "success": False,
                "error": "platform 파라미터는 'twitter' 또는 'reddit'만 가능"
            }), 400

        # 환경 상태 확인
        if not SimulationRunner.check_env_alive(simulation_id):
            return jsonify({
                "success": False,
                "error": "시뮬레이션 환경이 실행 중이 아니거나 종료됨. 시뮬레이션이 완료되고 명령 대기 모드에 있는지 확인해주세요."
            }), 400

        # prompt 최적화, Agent가 도구를 호출하지 않도록 접두사 추가
        optimized_prompt = optimize_interview_prompt(prompt)

        result = SimulationRunner.interview_all_agents(
            simulation_id=simulation_id,
            prompt=optimized_prompt,
            platform=platform,
            timeout=timeout
        )

        return jsonify({
            "success": result.get("success", False),
            "data": result
        })

    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400

    except TimeoutError as e:
        return jsonify({
            "success": False,
            "error": f"전체 Interview 응답 대기 타임아웃: {str(e)}"
        }), 504

    except Exception as e:
        logger.error(f"전체 Interview 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/interview/history', methods=['POST'])
def get_interview_history():
    """
    Interview 이력 조회

    시뮬레이션 데이터베이스에서 모든 Interview 기록 읽기

    요청 (JSON):
        {
            "simulation_id": "sim_xxxx",  // 필수, 시뮬레이션 ID
            "platform": "reddit",          // 선택 사항, 플랫폼 유형 (reddit/twitter)
                                           // 미지정 시 양 플랫폼의 모든 이력 반환
            "agent_id": 0,                 // 선택 사항, 해당 Agent의 인터뷰 이력만 조회
            "limit": 100                   // 선택 사항, 반환 수량, 기본값 100
        }

    반환:
        {
            "success": true,
            "data": {
                "count": 10,
                "history": [
                    {
                        "agent_id": 0,
                        "response": "저는...",
                        "prompt": "이 사안에 대해 어떻게 생각하세요?",
                        "timestamp": "2025-12-08T10:00:00",
                        "platform": "reddit"
                    },
                    ...
                ]
            }
        }
    """
    try:
        data = request.get_json() or {}

        simulation_id = data.get('simulation_id')
        platform = data.get('platform')  # 미지정 시 양 플랫폼의 이력 반환
        agent_id = data.get('agent_id')
        limit = data.get('limit', 100)

        if not simulation_id:
            return jsonify({
                "success": False,
                "error": "simulation_id를 제공해주세요"
            }), 400

        history = SimulationRunner.get_interview_history(
            simulation_id=simulation_id,
            platform=platform,
            agent_id=agent_id,
            limit=limit
        )

        return jsonify({
            "success": True,
            "data": {
                "count": len(history),
                "history": history
            }
        })

    except Exception as e:
        logger.error(f"Interview 이력 가져오기 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/env-status', methods=['POST'])
def get_env_status():
    """
    시뮬레이션 환경 상태 가져오기

    시뮬레이션 환경이 살아있는지 확인 (Interview 명령 수신 가능 여부)

    요청 (JSON):
        {
            "simulation_id": "sim_xxxx"  // 필수, 시뮬레이션 ID
        }

    반환:
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "env_alive": true,
                "twitter_available": true,
                "reddit_available": true,
                "message": "환경이 실행 중, Interview 명령 수신 가능"
            }
        }
    """
    try:
        data = request.get_json() or {}

        simulation_id = data.get('simulation_id')

        if not simulation_id:
            return jsonify({
                "success": False,
                "error": "simulation_id를 제공해주세요"
            }), 400

        env_alive = SimulationRunner.check_env_alive(simulation_id)

        # 더 상세한 상태 정보 가져오기
        env_status = SimulationRunner.get_env_status_detail(simulation_id)

        if env_alive:
            message = "환경이 실행 중, Interview 명령 수신 가능"
        else:
            message = "환경이 실행 중이 아니거나 종료됨"

        return jsonify({
            "success": True,
            "data": {
                "simulation_id": simulation_id,
                "env_alive": env_alive,
                "twitter_available": env_status.get("twitter_available", False),
                "reddit_available": env_status.get("reddit_available", False),
                "message": message
            }
        })

    except Exception as e:
        logger.error(f"환경 상태 가져오기 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/close-env', methods=['POST'])
def close_simulation_env():
    """
    시뮬레이션 환경 종료

    시뮬레이션에 환경 종료 명령을 전송하여 명령 대기 모드를 우아하게 종료.

    참고: /stop 인터페이스와 다름, /stop은 프로세스를 강제 종료,
    이 인터페이스는 시뮬레이션이 환경을 우아하게 종료하고 나가도록 함.

    요청 (JSON):
        {
            "simulation_id": "sim_xxxx",  // 필수, 시뮬레이션 ID
            "timeout": 30                  // 선택 사항, 타임아웃 (초), 기본값 30
        }

    반환:
        {
            "success": true,
            "data": {
                "message": "환경 종료 명령 전송됨",
                "result": {...},
                "timestamp": "2025-12-08T10:00:01"
            }
        }
    """
    try:
        data = request.get_json() or {}
        
        simulation_id = data.get('simulation_id')
        timeout = data.get('timeout', 30)
        
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": "simulation_id를 제공해주세요"
            }), 400

        result = SimulationRunner.close_simulation_env(
            simulation_id=simulation_id,
            timeout=timeout
        )

        # 시뮬레이션 상태 업데이트
        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)
        if state:
            state.status = SimulationStatus.COMPLETED
            manager._save_simulation_state(state)

        return jsonify({
            "success": result.get("success", False),
            "data": result
        })

    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400

    except Exception as e:
        logger.error(f"환경 종료 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500
