"""
Report API 라우트
시뮬레이션 보고서 생성, 조회, 대화 등의 인터페이스 제공
"""

import os
import traceback
import threading
from flask import request, jsonify, send_file

from . import report_bp
from ..config import Config
from ..services.report_agent import ReportAgent, ReportManager, ReportStatus
from ..services.simulation_manager import SimulationManager
from ..models.project import ProjectManager
from ..models.task import TaskManager, TaskStatus
from ..utils.logger import get_logger

logger = get_logger('mirofish.api.report')


# ============== 보고서 생성 인터페이스 ==============

@report_bp.route('/generate', methods=['POST'])
def generate_report():
    """
    시뮬레이션 분석 보고서 생성 (비동기 작업)

    이는 시간이 걸리는 작업으로, 인터페이스는 즉시 task_id를 반환합니다.
    GET /api/report/generate/status 를 사용하여 진행 상황을 조회하세요.

    요청 (JSON):
        {
            "simulation_id": "sim_xxxx",    // 필수, 시뮬레이션 ID
            "force_regenerate": false        // 선택 사항, 강제 재생성
        }

    반환:
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "task_id": "task_xxxx",
                "status": "generating",
                "message": "보고서 생성 작업이 시작되었습니다"
            }
        }
    """
    try:
        data = request.get_json() or {}
        
        simulation_id = data.get('simulation_id')
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": "simulation_id를 제공해 주세요"
            }), 400

        force_regenerate = data.get('force_regenerate', False)

        # 시뮬레이션 정보 조회
        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)

        if not state:
            return jsonify({
                "success": False,
                "error": f"시뮬레이션이 존재하지 않습니다: {simulation_id}"
            }), 404

        # 이미 보고서가 있는지 확인
        if not force_regenerate:
            existing_report = ReportManager.get_report_by_simulation(simulation_id)
            if existing_report and existing_report.status == ReportStatus.COMPLETED:
                return jsonify({
                    "success": True,
                    "data": {
                        "simulation_id": simulation_id,
                        "report_id": existing_report.report_id,
                        "status": "completed",
                        "message": "보고서가 이미 존재합니다",
                        "already_generated": True
                    }
                })

        # 프로젝트 정보 조회
        project = ProjectManager.get_project(state.project_id)
        if not project:
            return jsonify({
                "success": False,
                "error": f"프로젝트가 존재하지 않습니다: {state.project_id}"
            }), 404

        graph_id = state.graph_id or project.graph_id
        if not graph_id:
            return jsonify({
                "success": False,
                "error": "그래프 ID가 없습니다. 그래프가 구축되었는지 확인해 주세요"
            }), 400

        simulation_requirement = project.simulation_requirement
        if not simulation_requirement:
            return jsonify({
                "success": False,
                "error": "시뮬레이션 요구사항 설명이 없습니다"
            }), 400

        # 프론트엔드에 즉시 반환하기 위해 미리 report_id 생성
        import uuid
        report_id = f"report_{uuid.uuid4().hex[:12]}"

        # 비동기 작업 생성
        task_manager = TaskManager()
        task_id = task_manager.create_task(
            task_type="report_generate",
            metadata={
                "simulation_id": simulation_id,
                "graph_id": graph_id,
                "report_id": report_id
            }
        )
        
        # 백그라운드 작업 정의
        def run_generate():
            try:
                task_manager.update_task(
                    task_id,
                    status=TaskStatus.PROCESSING,
                    progress=0,
                    message="Report Agent 초기화 중..."
                )

                # Report Agent 생성
                agent = ReportAgent(
                    graph_id=graph_id,
                    simulation_id=simulation_id,
                    simulation_requirement=simulation_requirement
                )
                
                # 진행 상황 콜백
                def progress_callback(stage, progress, message):
                    task_manager.update_task(
                        task_id,
                        progress=progress,
                        message=f"[{stage}] {message}"
                    )

                # 보고서 생성 (미리 생성된 report_id 전달)
                report = agent.generate_report(
                    progress_callback=progress_callback,
                    report_id=report_id
                )

                # 보고서 저장
                ReportManager.save_report(report)

                if report.status == ReportStatus.COMPLETED:
                    task_manager.complete_task(
                        task_id,
                        result={
                            "report_id": report.report_id,
                            "simulation_id": simulation_id,
                            "status": "completed"
                        }
                    )
                else:
                    task_manager.fail_task(task_id, report.error or "보고서 생성 실패")

            except Exception as e:
                logger.error(f"보고서 생성 실패: {str(e)}")
                task_manager.fail_task(task_id, str(e))

        # 백그라운드 스레드 시작
        thread = threading.Thread(target=run_generate, daemon=True)
        thread.start()

        return jsonify({
            "success": True,
            "data": {
                "simulation_id": simulation_id,
                "report_id": report_id,
                "task_id": task_id,
                "status": "generating",
                "message": "보고서 생성 작업이 시작되었습니다. /api/report/generate/status 를 통해 진행 상황을 확인하세요",
                "already_generated": False
            }
        })

    except Exception as e:
        logger.error(f"보고서 생성 작업 시작 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/generate/status', methods=['POST'])
def get_generate_status():
    """
    보고서 생성 작업 진행 상황 조회

    요청 (JSON):
        {
            "task_id": "task_xxxx",         // 선택 사항, generate에서 반환된 task_id
            "simulation_id": "sim_xxxx"     // 선택 사항, 시뮬레이션 ID
        }

    반환:
        {
            "success": true,
            "data": {
                "task_id": "task_xxxx",
                "status": "processing|completed|failed",
                "progress": 45,
                "message": "..."
            }
        }
    """
    try:
        data = request.get_json() or {}

        task_id = data.get('task_id')
        simulation_id = data.get('simulation_id')

        # simulation_id가 제공된 경우 이미 완료된 보고서가 있는지 먼저 확인
        if simulation_id:
            existing_report = ReportManager.get_report_by_simulation(simulation_id)
            if existing_report and existing_report.status == ReportStatus.COMPLETED:
                return jsonify({
                    "success": True,
                    "data": {
                        "simulation_id": simulation_id,
                        "report_id": existing_report.report_id,
                        "status": "completed",
                        "progress": 100,
                        "message": "보고서가 이미 생성되었습니다",
                        "already_completed": True
                    }
                })

        if not task_id:
            return jsonify({
                "success": False,
                "error": "task_id 또는 simulation_id를 제공해 주세요"
            }), 400

        task_manager = TaskManager()
        task = task_manager.get_task(task_id)

        if not task:
            return jsonify({
                "success": False,
                "error": f"작업이 존재하지 않습니다: {task_id}"
            }), 404

        return jsonify({
            "success": True,
            "data": task.to_dict()
        })

    except Exception as e:
        logger.error(f"작업 상태 조회 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


# ============== 보고서 조회 인터페이스 ==============

@report_bp.route('/<report_id>', methods=['GET'])
def get_report(report_id: str):
    """
    보고서 상세 조회

    반환:
        {
            "success": true,
            "data": {
                "report_id": "report_xxxx",
                "simulation_id": "sim_xxxx",
                "status": "completed",
                "outline": {...},
                "markdown_content": "...",
                "created_at": "...",
                "completed_at": "..."
            }
        }
    """
    try:
        report = ReportManager.get_report(report_id)

        if not report:
            return jsonify({
                "success": False,
                "error": f"보고서가 존재하지 않습니다: {report_id}"
            }), 404

        return jsonify({
            "success": True,
            "data": report.to_dict()
        })

    except Exception as e:
        logger.error(f"보고서 조회 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/by-simulation/<simulation_id>', methods=['GET'])
def get_report_by_simulation(simulation_id: str):
    """
    시뮬레이션 ID로 보고서 조회

    반환:
        {
            "success": true,
            "data": {
                "report_id": "report_xxxx",
                ...
            }
        }
    """
    try:
        report = ReportManager.get_report_by_simulation(simulation_id)

        if not report:
            return jsonify({
                "success": False,
                "error": f"해당 시뮬레이션의 보고서가 없습니다: {simulation_id}",
                "has_report": False
            }), 404

        return jsonify({
            "success": True,
            "data": report.to_dict(),
            "has_report": True
        })

    except Exception as e:
        logger.error(f"보고서 조회 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/list', methods=['GET'])
def list_reports():
    """
    모든 보고서 목록 조회

    Query 파라미터:
        simulation_id: 시뮬레이션 ID로 필터링 (선택 사항)
        limit: 반환 개수 제한 (기본값 50)

    반환:
        {
            "success": true,
            "data": [...],
            "count": 10
        }
    """
    try:
        simulation_id = request.args.get('simulation_id')
        limit = request.args.get('limit', 50, type=int)

        reports = ReportManager.list_reports(
            simulation_id=simulation_id,
            limit=limit
        )

        return jsonify({
            "success": True,
            "data": [r.to_dict() for r in reports],
            "count": len(reports)
        })

    except Exception as e:
        logger.error(f"보고서 목록 조회 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/<report_id>/download', methods=['GET'])
def download_report(report_id: str):
    """
    보고서 다운로드 (Markdown 형식)

    Markdown 파일 반환
    """
    try:
        report = ReportManager.get_report(report_id)

        if not report:
            return jsonify({
                "success": False,
                "error": f"보고서가 존재하지 않습니다: {report_id}"
            }), 404

        md_path = ReportManager._get_report_markdown_path(report_id)

        if not os.path.exists(md_path):
            # MD 파일이 없을 경우 임시 파일 생성
            import tempfile
            with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
                f.write(report.markdown_content)
                temp_path = f.name
            
            return send_file(
                temp_path,
                as_attachment=True,
                download_name=f"{report_id}.md"
            )
        
        return send_file(
            md_path,
            as_attachment=True,
            download_name=f"{report_id}.md"
        )
        
    except Exception as e:
        logger.error(f"보고서 다운로드 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/<report_id>', methods=['DELETE'])
def delete_report(report_id: str):
    """보고서 삭제"""
    try:
        success = ReportManager.delete_report(report_id)

        if not success:
            return jsonify({
                "success": False,
                "error": f"보고서가 존재하지 않습니다: {report_id}"
            }), 404

        return jsonify({
            "success": True,
            "message": f"보고서가 삭제되었습니다: {report_id}"
        })

    except Exception as e:
        logger.error(f"보고서 삭제 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== Report Agent 대화 인터페이스 ==============

@report_bp.route('/chat', methods=['POST'])
def chat_with_report_agent():
    """
    Report Agent와 대화

    Report Agent는 대화 중 자율적으로 검색 도구를 호출하여 질문에 답변할 수 있습니다.

    요청 (JSON):
        {
            "simulation_id": "sim_xxxx",        // 필수, 시뮬레이션 ID
            "message": "여론 동향을 설명해 주세요",    // 필수, 사용자 메시지
            "chat_history": [                   // 선택 사항, 대화 이력
                {"role": "user", "content": "..."},
                {"role": "assistant", "content": "..."}
            ]
        }

    반환:
        {
            "success": true,
            "data": {
                "response": "Agent 응답...",
                "tool_calls": [호출된 도구 목록],
                "sources": [정보 출처]
            }
        }
    """
    try:
        data = request.get_json() or {}

        simulation_id = data.get('simulation_id')
        message = data.get('message')
        chat_history = data.get('chat_history', [])

        if not simulation_id:
            return jsonify({
                "success": False,
                "error": "simulation_id를 제공해 주세요"
            }), 400

        if not message:
            return jsonify({
                "success": False,
                "error": "message를 제공해 주세요"
            }), 400

        # 시뮬레이션 및 프로젝트 정보 조회
        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)

        if not state:
            return jsonify({
                "success": False,
                "error": f"시뮬레이션이 존재하지 않습니다: {simulation_id}"
            }), 404

        project = ProjectManager.get_project(state.project_id)
        if not project:
            return jsonify({
                "success": False,
                "error": f"프로젝트가 존재하지 않습니다: {state.project_id}"
            }), 404

        graph_id = state.graph_id or project.graph_id
        if not graph_id:
            return jsonify({
                "success": False,
                "error": "그래프 ID가 없습니다"
            }), 400

        simulation_requirement = project.simulation_requirement or ""

        # Agent 생성 후 대화
        agent = ReportAgent(
            graph_id=graph_id,
            simulation_id=simulation_id,
            simulation_requirement=simulation_requirement
        )

        result = agent.chat(message=message, chat_history=chat_history)

        return jsonify({
            "success": True,
            "data": result
        })

    except Exception as e:
        logger.error(f"대화 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== 보고서 진행 상황 및 섹션별 인터페이스 ==============

@report_bp.route('/<report_id>/progress', methods=['GET'])
def get_report_progress(report_id: str):
    """
    보고서 생성 진행 상황 조회 (실시간)

    반환:
        {
            "success": true,
            "data": {
                "status": "generating",
                "progress": 45,
                "message": "섹션 생성 중: 핵심 발견사항",
                "current_section": "핵심 발견사항",
                "completed_sections": ["실행 요약", "시뮬레이션 배경"],
                "updated_at": "2025-12-09T..."
            }
        }
    """
    try:
        progress = ReportManager.get_progress(report_id)

        if not progress:
            return jsonify({
                "success": False,
                "error": f"보고서가 존재하지 않거나 진행 정보를 사용할 수 없습니다: {report_id}"
            }), 404

        return jsonify({
            "success": True,
            "data": progress
        })

    except Exception as e:
        logger.error(f"보고서 진행 상황 조회 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/<report_id>/sections', methods=['GET'])
def get_report_sections(report_id: str):
    """
    생성된 섹션 목록 조회 (섹션별 출력)

    프론트엔드는 이 인터페이스를 폴링하여 생성된 섹션 내용을 확인할 수 있으며,
    전체 보고서가 완료될 때까지 기다릴 필요가 없습니다.

    반환:
        {
            "success": true,
            "data": {
                "report_id": "report_xxxx",
                "sections": [
                    {
                        "filename": "section_01.md",
                        "section_index": 1,
                        "content": "## 실행 요약\\n\\n..."
                    },
                    ...
                ],
                "total_sections": 3,
                "is_complete": false
            }
        }
    """
    try:
        sections = ReportManager.get_generated_sections(report_id)

        # 보고서 상태 조회
        report = ReportManager.get_report(report_id)
        is_complete = report is not None and report.status == ReportStatus.COMPLETED

        return jsonify({
            "success": True,
            "data": {
                "report_id": report_id,
                "sections": sections,
                "total_sections": len(sections),
                "is_complete": is_complete
            }
        })

    except Exception as e:
        logger.error(f"섹션 목록 조회 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/<report_id>/section/<int:section_index>', methods=['GET'])
def get_single_section(report_id: str, section_index: int):
    """
    단일 섹션 내용 조회

    반환:
        {
            "success": true,
            "data": {
                "filename": "section_01.md",
                "content": "## 실행 요약\\n\\n..."
            }
        }
    """
    try:
        section_path = ReportManager._get_section_path(report_id, section_index)

        if not os.path.exists(section_path):
            return jsonify({
                "success": False,
                "error": f"섹션이 존재하지 않습니다: section_{section_index:02d}.md"
            }), 404

        with open(section_path, 'r', encoding='utf-8') as f:
            content = f.read()

        return jsonify({
            "success": True,
            "data": {
                "filename": f"section_{section_index:02d}.md",
                "section_index": section_index,
                "content": content
            }
        })

    except Exception as e:
        logger.error(f"섹션 내용 조회 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== 보고서 상태 확인 인터페이스 ==============

@report_bp.route('/check/<simulation_id>', methods=['GET'])
def check_report_status(simulation_id: str):
    """
    시뮬레이션에 보고서가 있는지, 보고서 상태 확인

    프론트엔드에서 Interview 기능 잠금 해제 여부를 판단하는 데 사용

    반환:
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "has_report": true,
                "report_status": "completed",
                "report_id": "report_xxxx",
                "interview_unlocked": true
            }
        }
    """
    try:
        report = ReportManager.get_report_by_simulation(simulation_id)

        has_report = report is not None
        report_status = report.status.value if report else None
        report_id = report.report_id if report else None

        # 보고서가 완료된 후에만 interview 잠금 해제
        interview_unlocked = has_report and report.status == ReportStatus.COMPLETED

        return jsonify({
            "success": True,
            "data": {
                "simulation_id": simulation_id,
                "has_report": has_report,
                "report_status": report_status,
                "report_id": report_id,
                "interview_unlocked": interview_unlocked
            }
        })

    except Exception as e:
        logger.error(f"보고서 상태 확인 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== Agent 로그 인터페이스 ==============

@report_bp.route('/<report_id>/agent-log', methods=['GET'])
def get_agent_log(report_id: str):
    """
    Report Agent의 상세 실행 로그 조회

    보고서 생성 과정에서의 각 단계별 동작을 실시간으로 조회합니다:
    - 보고서 시작, 계획 시작/완료
    - 각 섹션의 시작, 도구 호출, LLM 응답, 완료
    - 보고서 완료 또는 실패

    Query 파라미터:
        from_line: 몇 번째 줄부터 읽을지 (선택 사항, 기본값 0, 증분 조회용)

    반환:
        {
            "success": true,
            "data": {
                "logs": [
                    {
                        "timestamp": "2025-12-13T...",
                        "elapsed_seconds": 12.5,
                        "report_id": "report_xxxx",
                        "action": "tool_call",
                        "stage": "generating",
                        "section_title": "실행 요약",
                        "section_index": 1,
                        "details": {
                            "tool_name": "insight_forge",
                            "parameters": {...},
                            ...
                        }
                    },
                    ...
                ],
                "total_lines": 25,
                "from_line": 0,
                "has_more": false
            }
        }
    """
    try:
        from_line = request.args.get('from_line', 0, type=int)

        log_data = ReportManager.get_agent_log(report_id, from_line=from_line)

        return jsonify({
            "success": True,
            "data": log_data
        })

    except Exception as e:
        logger.error(f"Agent 로그 조회 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/<report_id>/agent-log/stream', methods=['GET'])
def stream_agent_log(report_id: str):
    """
    전체 Agent 로그 조회 (한 번에 모두 가져오기)

    반환:
        {
            "success": true,
            "data": {
                "logs": [...],
                "count": 25
            }
        }
    """
    try:
        logs = ReportManager.get_agent_log_stream(report_id)

        return jsonify({
            "success": True,
            "data": {
                "logs": logs,
                "count": len(logs)
            }
        })

    except Exception as e:
        logger.error(f"Agent 로그 조회 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== 콘솔 로그 인터페이스 ==============

@report_bp.route('/<report_id>/console-log', methods=['GET'])
def get_console_log(report_id: str):
    """
    Report Agent의 콘솔 출력 로그 조회

    보고서 생성 과정에서의 콘솔 출력 (INFO, WARNING 등)을 실시간으로 조회합니다.
    이는 agent-log 인터페이스에서 반환하는 구조화된 JSON 로그와 다르며,
    순수 텍스트 형식의 콘솔 스타일 로그입니다.

    Query 파라미터:
        from_line: 몇 번째 줄부터 읽을지 (선택 사항, 기본값 0, 증분 조회용)

    반환:
        {
            "success": true,
            "data": {
                "logs": [
                    "[19:46:14] INFO: 검색 완료: 관련 사실 15건 발견",
                    "[19:46:14] INFO: 그래프 검색: graph_id=xxx, query=...",
                    ...
                ],
                "total_lines": 100,
                "from_line": 0,
                "has_more": false
            }
        }
    """
    try:
        from_line = request.args.get('from_line', 0, type=int)

        log_data = ReportManager.get_console_log(report_id, from_line=from_line)

        return jsonify({
            "success": True,
            "data": log_data
        })

    except Exception as e:
        logger.error(f"콘솔 로그 조회 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/<report_id>/console-log/stream', methods=['GET'])
def stream_console_log(report_id: str):
    """
    전체 콘솔 로그 조회 (한 번에 모두 가져오기)

    반환:
        {
            "success": true,
            "data": {
                "logs": [...],
                "count": 100
            }
        }
    """
    try:
        logs = ReportManager.get_console_log_stream(report_id)

        return jsonify({
            "success": True,
            "data": {
                "logs": logs,
                "count": len(logs)
            }
        })

    except Exception as e:
        logger.error(f"콘솔 로그 조회 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== 도구 호출 인터페이스 (디버깅용) ==============

@report_bp.route('/tools/search', methods=['POST'])
def search_graph_tool():
    """
    그래프 검색 도구 인터페이스 (디버깅용)

    요청 (JSON):
        {
            "graph_id": "mirofish_xxxx",
            "query": "검색 쿼리",
            "limit": 10
        }
    """
    try:
        data = request.get_json() or {}

        graph_id = data.get('graph_id')
        query = data.get('query')
        limit = data.get('limit', 10)

        if not graph_id or not query:
            return jsonify({
                "success": False,
                "error": "graph_id와 query를 제공해 주세요"
            }), 400

        from ..services.zep_tools import ZepToolsService

        tools = ZepToolsService()
        result = tools.search_graph(
            graph_id=graph_id,
            query=query,
            limit=limit
        )

        return jsonify({
            "success": True,
            "data": result.to_dict()
        })

    except Exception as e:
        logger.error(f"그래프 검색 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/tools/statistics', methods=['POST'])
def get_graph_statistics_tool():
    """
    그래프 통계 도구 인터페이스 (디버깅용)

    요청 (JSON):
        {
            "graph_id": "mirofish_xxxx"
        }
    """
    try:
        data = request.get_json() or {}

        graph_id = data.get('graph_id')

        if not graph_id:
            return jsonify({
                "success": False,
                "error": "graph_id를 제공해 주세요"
            }), 400

        from ..services.zep_tools import ZepToolsService

        tools = ZepToolsService()
        result = tools.get_graph_statistics(graph_id)

        return jsonify({
            "success": True,
            "data": result
        })

    except Exception as e:
        logger.error(f"그래프 통계 조회 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500
