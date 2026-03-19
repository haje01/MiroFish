"""
OASIS 시뮬레이션 러너
백그라운드에서 시뮬레이션을 실행하고 각 Agent의 동작을 기록하며, 실시간 상태 모니터링을 지원합니다
"""

import os
import sys
import json
import time
import asyncio
import threading
import subprocess
import signal
import atexit
from typing import Dict, Any, List, Optional, Union
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from queue import Queue

from ..config import Config
from ..utils.logger import get_logger
from .zep_graph_memory_updater import ZepGraphMemoryManager
from .simulation_ipc import SimulationIPCClient, CommandType, IPCResponse

logger = get_logger('mirofish.simulation_runner')

# 정리 함수 등록 여부 표시
_cleanup_registered = False

# 플랫폼 감지
IS_WINDOWS = sys.platform == 'win32'


class RunnerStatus(str, Enum):
    """러너 상태"""
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class AgentAction:
    """Agent 동작 기록"""
    round_num: int
    timestamp: str
    platform: str  # twitter / reddit
    agent_id: int
    agent_name: str
    action_type: str  # CREATE_POST, LIKE_POST, etc.
    action_args: Dict[str, Any] = field(default_factory=dict)
    result: Optional[str] = None
    success: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "round_num": self.round_num,
            "timestamp": self.timestamp,
            "platform": self.platform,
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "action_type": self.action_type,
            "action_args": self.action_args,
            "result": self.result,
            "success": self.success,
        }


@dataclass
class RoundSummary:
    """라운드별 요약"""
    round_num: int
    start_time: str
    end_time: Optional[str] = None
    simulated_hour: int = 0
    twitter_actions: int = 0
    reddit_actions: int = 0
    active_agents: List[int] = field(default_factory=list)
    actions: List[AgentAction] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "round_num": self.round_num,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "simulated_hour": self.simulated_hour,
            "twitter_actions": self.twitter_actions,
            "reddit_actions": self.reddit_actions,
            "active_agents": self.active_agents,
            "actions_count": len(self.actions),
            "actions": [a.to_dict() for a in self.actions],
        }


@dataclass
class SimulationRunState:
    """시뮬레이션 실행 상태 (실시간)"""
    simulation_id: str
    runner_status: RunnerStatus = RunnerStatus.IDLE
    
    # 진행 정보
    current_round: int = 0
    total_rounds: int = 0
    simulated_hours: int = 0
    total_simulation_hours: int = 0
    
    # 각 플랫폼 독립 라운드 및 시뮬레이션 시간 (양쪽 플랫폼 병렬 표시용)
    twitter_current_round: int = 0
    reddit_current_round: int = 0
    twitter_simulated_hours: int = 0
    reddit_simulated_hours: int = 0
    
    # 플랫폼 상태
    twitter_running: bool = False
    reddit_running: bool = False
    twitter_actions_count: int = 0
    reddit_actions_count: int = 0
    
    # 플랫폼 완료 상태 (actions.jsonl에서 simulation_end 이벤트 감지로 판단)
    twitter_completed: bool = False
    reddit_completed: bool = False
    
    # 라운드별 요약
    rounds: List[RoundSummary] = field(default_factory=list)
    
    # 최근 동작 (프론트엔드 실시간 표시용)
    recent_actions: List[AgentAction] = field(default_factory=list)
    max_recent_actions: int = 50
    
    # 타임스탬프
    started_at: Optional[str] = None
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    completed_at: Optional[str] = None
    
    # 오류 정보
    error: Optional[str] = None
    
    # 프로세스 ID (중지에 사용)
    process_pid: Optional[int] = None
    
    def add_action(self, action: AgentAction):
        """최근 동작 목록에 동작 추가"""
        self.recent_actions.insert(0, action)
        if len(self.recent_actions) > self.max_recent_actions:
            self.recent_actions = self.recent_actions[:self.max_recent_actions]
        
        if action.platform == "twitter":
            self.twitter_actions_count += 1
        else:
            self.reddit_actions_count += 1
        
        self.updated_at = datetime.now().isoformat()
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "simulation_id": self.simulation_id,
            "runner_status": self.runner_status.value,
            "current_round": self.current_round,
            "total_rounds": self.total_rounds,
            "simulated_hours": self.simulated_hours,
            "total_simulation_hours": self.total_simulation_hours,
            "progress_percent": round(self.current_round / max(self.total_rounds, 1) * 100, 1),
            # 각 플랫폼 독립 라운드 및 시간
            "twitter_current_round": self.twitter_current_round,
            "reddit_current_round": self.reddit_current_round,
            "twitter_simulated_hours": self.twitter_simulated_hours,
            "reddit_simulated_hours": self.reddit_simulated_hours,
            "twitter_running": self.twitter_running,
            "reddit_running": self.reddit_running,
            "twitter_completed": self.twitter_completed,
            "reddit_completed": self.reddit_completed,
            "twitter_actions_count": self.twitter_actions_count,
            "reddit_actions_count": self.reddit_actions_count,
            "total_actions_count": self.twitter_actions_count + self.reddit_actions_count,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "error": self.error,
            "process_pid": self.process_pid,
        }
    
    def to_detail_dict(self) -> Dict[str, Any]:
        """최근 동작이 포함된 상세 정보"""
        result = self.to_dict()
        result["recent_actions"] = [a.to_dict() for a in self.recent_actions]
        result["rounds_count"] = len(self.rounds)
        return result


class SimulationRunner:
    """
    시뮬레이션 러너

    담당 기능:
    1. 백그라운드 프로세스에서 OASIS 시뮬레이션 실행
    2. 실행 로그 파싱, 각 Agent의 동작 기록
    3. 실시간 상태 조회 인터페이스 제공
    4. 일시정지/중지/재개 지원
    """
    
    # 실행 상태 저장 디렉토리
    RUN_STATE_DIR = os.path.join(
        os.path.dirname(__file__),
        '../../uploads/simulations'
    )
    
    # 스크립트 디렉토리
    SCRIPTS_DIR = os.path.join(
        os.path.dirname(__file__),
        '../../scripts'
    )
    
    # 메모리 내 실행 상태
    _run_states: Dict[str, SimulationRunState] = {}
    _processes: Dict[str, subprocess.Popen] = {}
    _action_queues: Dict[str, Queue] = {}
    _monitor_threads: Dict[str, threading.Thread] = {}
    _stdout_files: Dict[str, Any] = {}  # stdout 파일 핸들 저장
    _stderr_files: Dict[str, Any] = {}  # stderr 파일 핸들 저장

    # 그래프 메모리 업데이트 설정
    _graph_memory_enabled: Dict[str, bool] = {}  # simulation_id -> enabled
    
    @classmethod
    def get_run_state(cls, simulation_id: str) -> Optional[SimulationRunState]:
        """실행 상태 조회"""
        if simulation_id in cls._run_states:
            return cls._run_states[simulation_id]
        
        # 파일에서 로드 시도
        state = cls._load_run_state(simulation_id)
        if state:
            cls._run_states[simulation_id] = state
        return state
    
    @classmethod
    def _load_run_state(cls, simulation_id: str) -> Optional[SimulationRunState]:
        """파일에서 실행 상태 로드"""
        state_file = os.path.join(cls.RUN_STATE_DIR, simulation_id, "run_state.json")
        if not os.path.exists(state_file):
            return None
        
        try:
            with open(state_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            state = SimulationRunState(
                simulation_id=simulation_id,
                runner_status=RunnerStatus(data.get("runner_status", "idle")),
                current_round=data.get("current_round", 0),
                total_rounds=data.get("total_rounds", 0),
                simulated_hours=data.get("simulated_hours", 0),
                total_simulation_hours=data.get("total_simulation_hours", 0),
                # 각 플랫폼 독립 라운드 및 시간
                twitter_current_round=data.get("twitter_current_round", 0),
                reddit_current_round=data.get("reddit_current_round", 0),
                twitter_simulated_hours=data.get("twitter_simulated_hours", 0),
                reddit_simulated_hours=data.get("reddit_simulated_hours", 0),
                twitter_running=data.get("twitter_running", False),
                reddit_running=data.get("reddit_running", False),
                twitter_completed=data.get("twitter_completed", False),
                reddit_completed=data.get("reddit_completed", False),
                twitter_actions_count=data.get("twitter_actions_count", 0),
                reddit_actions_count=data.get("reddit_actions_count", 0),
                started_at=data.get("started_at"),
                updated_at=data.get("updated_at", datetime.now().isoformat()),
                completed_at=data.get("completed_at"),
                error=data.get("error"),
                process_pid=data.get("process_pid"),
            )
            
            # 최근 동작 로드
            actions_data = data.get("recent_actions", [])
            for a in actions_data:
                state.recent_actions.append(AgentAction(
                    round_num=a.get("round_num", 0),
                    timestamp=a.get("timestamp", ""),
                    platform=a.get("platform", ""),
                    agent_id=a.get("agent_id", 0),
                    agent_name=a.get("agent_name", ""),
                    action_type=a.get("action_type", ""),
                    action_args=a.get("action_args", {}),
                    result=a.get("result"),
                    success=a.get("success", True),
                ))
            
            return state
        except Exception as e:
            logger.error(f"실행 상태 로드 실패: {str(e)}")
            return None
    
    @classmethod
    def _save_run_state(cls, state: SimulationRunState):
        """파일에 실행 상태 저장"""
        sim_dir = os.path.join(cls.RUN_STATE_DIR, state.simulation_id)
        os.makedirs(sim_dir, exist_ok=True)
        state_file = os.path.join(sim_dir, "run_state.json")
        
        data = state.to_detail_dict()
        
        with open(state_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        cls._run_states[state.simulation_id] = state
    
    @classmethod
    def start_simulation(
        cls,
        simulation_id: str,
        platform: str = "parallel",  # twitter / reddit / parallel
        max_rounds: int = None,  # 최대 시뮬레이션 라운드 수 (선택, 너무 긴 시뮬레이션 잘라내기용)
        enable_graph_memory_update: bool = False,  # Zep 그래프에 활동 업데이트 여부
        graph_id: str = None  # Zep 그래프 ID (그래프 업데이트 활성화 시 필수)
    ) -> SimulationRunState:
        """
        시뮬레이션 시작

        Args:
            simulation_id: 시뮬레이션 ID
            platform: 실행 플랫폼 (twitter/reddit/parallel)
            max_rounds: 최대 시뮬레이션 라운드 수 (선택, 너무 긴 시뮬레이션 잘라내기)
            enable_graph_memory_update: Agent 활동을 Zep 그래프에 동적 업데이트 여부
            graph_id: Zep 그래프 ID (그래프 업데이트 활성화 시 필수)

        Returns:
            SimulationRunState
        """
        # 이미 실행 중인지 확인
        existing = cls.get_run_state(simulation_id)
        if existing and existing.runner_status in [RunnerStatus.RUNNING, RunnerStatus.STARTING]:
            raise ValueError(f"시뮬레이션이 이미 실행 중입니다: {simulation_id}")

        # 시뮬레이션 설정 로드
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        config_path = os.path.join(sim_dir, "simulation_config.json")
        
        if not os.path.exists(config_path):
            raise ValueError(f"시뮬레이션 설정이 존재하지 않습니다. 먼저 /prepare 인터페이스를 호출하세요")


        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        # 실행 상태 초기화
        time_config = config.get("time_config", {})
        total_hours = time_config.get("total_simulation_hours", 72)
        minutes_per_round = time_config.get("minutes_per_round", 30)
        total_rounds = int(total_hours * 60 / minutes_per_round)
        
        # 최대 라운드 수가 지정된 경우 잘라내기
        if max_rounds is not None and max_rounds > 0:
            original_rounds = total_rounds
            total_rounds = min(total_rounds, max_rounds)
            if total_rounds < original_rounds:
                logger.info(f"라운드 수 잘라냄: {original_rounds} -> {total_rounds} (max_rounds={max_rounds})")
        
        state = SimulationRunState(
            simulation_id=simulation_id,
            runner_status=RunnerStatus.STARTING,
            total_rounds=total_rounds,
            total_simulation_hours=total_hours,
            started_at=datetime.now().isoformat(),
        )
        
        cls._save_run_state(state)
        
        # 그래프 메모리 업데이트 활성화 시 업데이터 생성
        if enable_graph_memory_update:
            if not graph_id:
                raise ValueError("그래프 메모리 업데이트 활성화 시 graph_id를 반드시 제공해야 합니다")
            
            try:
                ZepGraphMemoryManager.create_updater(simulation_id, graph_id)
                cls._graph_memory_enabled[simulation_id] = True
                logger.info(f"그래프 메모리 업데이트 활성화: simulation_id={simulation_id}, graph_id={graph_id}")
            except Exception as e:
                logger.error(f"그래프 메모리 업데이터 생성 실패: {e}")
                cls._graph_memory_enabled[simulation_id] = False
        else:
            cls._graph_memory_enabled[simulation_id] = False
        
        # 실행할 스크립트 결정 (스크립트는 backend/scripts/ 디렉토리에 위치)
        if platform == "twitter":
            script_name = "run_twitter_simulation.py"
            state.twitter_running = True
        elif platform == "reddit":
            script_name = "run_reddit_simulation.py"
            state.reddit_running = True
        else:
            script_name = "run_parallel_simulation.py"
            state.twitter_running = True
            state.reddit_running = True
        
        script_path = os.path.join(cls.SCRIPTS_DIR, script_name)
        
        if not os.path.exists(script_path):
            raise ValueError(f"스크립트가 존재하지 않습니다: {script_path}")

        # 동작 큐 생성
        action_queue = Queue()
        cls._action_queues[simulation_id] = action_queue
        
        # 시뮬레이션 프로세스 시작
        try:
            # 전체 경로를 사용하여 실행 명령 구성
            # 새 로그 구조:
            #   twitter/actions.jsonl - Twitter 동작 로그
            #   reddit/actions.jsonl  - Reddit 동작 로그
            #   simulation.log        - 메인 프로세스 로그
            
            cmd = [
                sys.executable,  # Python 인터프리터
                script_path,
                "--config", config_path,  # 전체 설정 파일 경로 사용
            ]
            
            # 최대 라운드 수가 지정된 경우 명령행 인수에 추가
            if max_rounds is not None and max_rounds > 0:
                cmd.extend(["--max-rounds", str(max_rounds)])
            
            # 메인 로그 파일 생성, stdout/stderr 파이프 버퍼 초과로 인한 프로세스 블록 방지
            main_log_path = os.path.join(sim_dir, "simulation.log")
            main_log_file = open(main_log_path, 'w', encoding='utf-8')
            
            # 자식 프로세스 환경 변수 설정, Windows에서 UTF-8 인코딩 사용 보장
            # 이는 서드파티 라이브러리 (예: OASIS)가 파일 읽기 시 인코딩 미지정 문제를 수정
            env = os.environ.copy()
            env['PYTHONUTF8'] = '1'  # Python 3.7+ 지원, 모든 open()이 기본적으로 UTF-8 사용
            env['PYTHONIOENCODING'] = 'utf-8'  # stdout/stderr가 UTF-8을 사용하도록 보장

            # 작업 디렉토리를 시뮬레이션 디렉토리로 설정 (데이터베이스 등 파일이 생성되는 곳)
            # start_new_session=True를 사용하여 새 프로세스 그룹 생성, os.killpg로 모든 자식 프로세스 종료 가능
            process = subprocess.Popen(
                cmd,
                cwd=sim_dir,
                stdout=main_log_file,
                stderr=subprocess.STDOUT,  # stderr도 같은 파일에 기록
                text=True,
                encoding='utf-8',  # 인코딩 명시 지정
                bufsize=1,
                env=env,  # UTF-8 설정이 포함된 환경 변수 전달
                start_new_session=True,  # 새 프로세스 그룹 생성, 서버 종료 시 관련 모든 프로세스 종료 보장
            )
            
            # 이후 닫기 위해 파일 핸들 저장
            cls._stdout_files[simulation_id] = main_log_file
            cls._stderr_files[simulation_id] = None  # 더 이상 별도의 stderr 불필요
            
            state.process_pid = process.pid
            state.runner_status = RunnerStatus.RUNNING
            cls._processes[simulation_id] = process
            cls._save_run_state(state)
            
            # 모니터링 스레드 시작
            monitor_thread = threading.Thread(
                target=cls._monitor_simulation,
                args=(simulation_id,),
                daemon=True
            )
            monitor_thread.start()
            cls._monitor_threads[simulation_id] = monitor_thread
            
            logger.info(f"시뮬레이션 시작 성공: {simulation_id}, pid={process.pid}, platform={platform}")
            
        except Exception as e:
            state.runner_status = RunnerStatus.FAILED
            state.error = str(e)
            cls._save_run_state(state)
            raise
        
        return state
    
    @classmethod
    def _monitor_simulation(cls, simulation_id: str):
        """시뮬레이션 프로세스 모니터링, 동작 로그 파싱"""
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        
        # 새 로그 구조: 플랫폼별 동작 로그
        twitter_actions_log = os.path.join(sim_dir, "twitter", "actions.jsonl")
        reddit_actions_log = os.path.join(sim_dir, "reddit", "actions.jsonl")
        
        process = cls._processes.get(simulation_id)
        state = cls.get_run_state(simulation_id)
        
        if not process or not state:
            return
        
        twitter_position = 0
        reddit_position = 0
        
        try:
            while process.poll() is None:  # 프로세스 실행 중
                # Twitter 동작 로그 읽기
                if os.path.exists(twitter_actions_log):
                    twitter_position = cls._read_action_log(
                        twitter_actions_log, twitter_position, state, "twitter"
                    )
                
                # Reddit 동작 로그 읽기
                if os.path.exists(reddit_actions_log):
                    reddit_position = cls._read_action_log(
                        reddit_actions_log, reddit_position, state, "reddit"
                    )
                
                # 상태 업데이트
                cls._save_run_state(state)
                time.sleep(2)
            
            # 프로세스 종료 후 마지막으로 로그 한 번 읽기
            if os.path.exists(twitter_actions_log):
                cls._read_action_log(twitter_actions_log, twitter_position, state, "twitter")
            if os.path.exists(reddit_actions_log):
                cls._read_action_log(reddit_actions_log, reddit_position, state, "reddit")
            
            # 프로세스 종료
            exit_code = process.returncode
            
            if exit_code == 0:
                state.runner_status = RunnerStatus.COMPLETED
                state.completed_at = datetime.now().isoformat()
                logger.info(f"시뮬레이션 완료: {simulation_id}")
            else:
                state.runner_status = RunnerStatus.FAILED
                # 메인 로그 파일에서 오류 정보 읽기
                main_log_path = os.path.join(sim_dir, "simulation.log")
                error_info = ""
                try:
                    if os.path.exists(main_log_path):
                        with open(main_log_path, 'r', encoding='utf-8') as f:
                            error_info = f.read()[-2000:]  # 마지막 2000자 취하기
                except Exception:
                    pass
                state.error = f"프로세스 종료 코드: {exit_code}, 오류: {error_info}"
                logger.error(f"시뮬레이션 실패: {simulation_id}, error={state.error}")
            
            state.twitter_running = False
            state.reddit_running = False
            cls._save_run_state(state)
            
        except Exception as e:
            logger.error(f"모니터링 스레드 예외: {simulation_id}, error={str(e)}")
            state.runner_status = RunnerStatus.FAILED
            state.error = str(e)
            cls._save_run_state(state)
        
        finally:
            # 그래프 메모리 업데이터 중지
            if cls._graph_memory_enabled.get(simulation_id, False):
                try:
                    ZepGraphMemoryManager.stop_updater(simulation_id)
                    logger.info(f"그래프 메모리 업데이트 중지됨: simulation_id={simulation_id}")
                except Exception as e:
                    logger.error(f"그래프 메모리 업데이터 중지 실패: {e}")
                cls._graph_memory_enabled.pop(simulation_id, None)
            
            # 프로세스 리소스 정리
            cls._processes.pop(simulation_id, None)
            cls._action_queues.pop(simulation_id, None)
            
            # 로그 파일 핸들 닫기
            if simulation_id in cls._stdout_files:
                try:
                    cls._stdout_files[simulation_id].close()
                except Exception:
                    pass
                cls._stdout_files.pop(simulation_id, None)
            if simulation_id in cls._stderr_files and cls._stderr_files[simulation_id]:
                try:
                    cls._stderr_files[simulation_id].close()
                except Exception:
                    pass
                cls._stderr_files.pop(simulation_id, None)
    
    @classmethod
    def _read_action_log(
        cls, 
        log_path: str, 
        position: int, 
        state: SimulationRunState,
        platform: str
    ) -> int:
        """
        동작 로그 파일 읽기

        Args:
            log_path: 로그 파일 경로
            position: 마지막 읽기 위치
            state: 실행 상태 객체
            platform: 플랫폼 이름 (twitter/reddit)

        Returns:
            새 읽기 위치
        """
        # 그래프 메모리 업데이트 활성화 여부 확인
        graph_memory_enabled = cls._graph_memory_enabled.get(state.simulation_id, False)
        graph_updater = None
        if graph_memory_enabled:
            graph_updater = ZepGraphMemoryManager.get_updater(state.simulation_id)
        
        try:
            with open(log_path, 'r', encoding='utf-8') as f:
                f.seek(position)
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            action_data = json.loads(line)
                            
                            # 이벤트 유형 항목 처리
                            if "event_type" in action_data:
                                event_type = action_data.get("event_type")
                                
                                # simulation_end 이벤트 감지, 플랫폼 완료 표시
                                if event_type == "simulation_end":
                                    if platform == "twitter":
                                        state.twitter_completed = True
                                        state.twitter_running = False
                                        logger.info(f"Twitter 시뮬레이션 완료: {state.simulation_id}, total_rounds={action_data.get('total_rounds')}, total_actions={action_data.get('total_actions')}")
                                    elif platform == "reddit":
                                        state.reddit_completed = True
                                        state.reddit_running = False
                                        logger.info(f"Reddit 시뮬레이션 완료: {state.simulation_id}, total_rounds={action_data.get('total_rounds')}, total_actions={action_data.get('total_actions')}")
                                    
                                    # 모든 활성화된 플랫폼이 완료되었는지 확인
                                    # 하나의 플랫폼만 실행 중인 경우 그 플랫폼만 확인
                                    # 두 플랫폼 모두 실행 중인 경우 두 플랫폼 모두 완료되어야 함
                                    all_completed = cls._check_all_platforms_completed(state)
                                    if all_completed:
                                        state.runner_status = RunnerStatus.COMPLETED
                                        state.completed_at = datetime.now().isoformat()
                                        logger.info(f"모든 플랫폼 시뮬레이션 완료: {state.simulation_id}")
                                
                                # 라운드 정보 업데이트 (round_end 이벤트에서)
                                elif event_type == "round_end":
                                    round_num = action_data.get("round", 0)
                                    simulated_hours = action_data.get("simulated_hours", 0)
                                    
                                    # 각 플랫폼 독립 라운드 및 시간 업데이트
                                    if platform == "twitter":
                                        if round_num > state.twitter_current_round:
                                            state.twitter_current_round = round_num
                                        state.twitter_simulated_hours = simulated_hours
                                    elif platform == "reddit":
                                        if round_num > state.reddit_current_round:
                                            state.reddit_current_round = round_num
                                        state.reddit_simulated_hours = simulated_hours
                                    
                                    # 전체 라운드는 두 플랫폼의 최댓값
                                    if round_num > state.current_round:
                                        state.current_round = round_num
                                    # 전체 시간은 두 플랫폼의 최댓값
                                    state.simulated_hours = max(state.twitter_simulated_hours, state.reddit_simulated_hours)
                                
                                continue
                            
                            action = AgentAction(
                                round_num=action_data.get("round", 0),
                                timestamp=action_data.get("timestamp", datetime.now().isoformat()),
                                platform=platform,
                                agent_id=action_data.get("agent_id", 0),
                                agent_name=action_data.get("agent_name", ""),
                                action_type=action_data.get("action_type", ""),
                                action_args=action_data.get("action_args", {}),
                                result=action_data.get("result"),
                                success=action_data.get("success", True),
                            )
                            state.add_action(action)
                            
                            # 라운드 업데이트
                            if action.round_num and action.round_num > state.current_round:
                                state.current_round = action.round_num
                            
                            # 그래프 메모리 업데이트 활성화 시 활동을 Zep에 전송
                            if graph_updater:
                                graph_updater.add_activity_from_dict(action_data, platform)
                            
                        except json.JSONDecodeError:
                            pass
                return f.tell()
        except Exception as e:
            logger.warning(f"동작 로그 읽기 실패: {log_path}, error={e}")
            return position
    
    @classmethod
    def _check_all_platforms_completed(cls, state: SimulationRunState) -> bool:
        """
        모든 활성화된 플랫폼이 시뮬레이션을 완료했는지 확인

        해당 actions.jsonl 파일 존재 여부로 플랫폼 활성화 판단

        Returns:
            True 모든 활성화된 플랫폼이 완료된 경우
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, state.simulation_id)
        twitter_log = os.path.join(sim_dir, "twitter", "actions.jsonl")
        reddit_log = os.path.join(sim_dir, "reddit", "actions.jsonl")
        
        # 어떤 플랫폼이 활성화되었는지 확인 (파일 존재 여부로 판단)
        twitter_enabled = os.path.exists(twitter_log)
        reddit_enabled = os.path.exists(reddit_log)
        
        # 플랫폼이 활성화되었지만 완료되지 않은 경우 False 반환
        if twitter_enabled and not state.twitter_completed:
            return False
        if reddit_enabled and not state.reddit_completed:
            return False
        
        # 최소 하나의 플랫폼이 활성화되어 완료됨
        return twitter_enabled or reddit_enabled

    @classmethod
    def _terminate_process(cls, process: subprocess.Popen, simulation_id: str, timeout: int = 10):
        """
        크로스 플랫폼 프로세스 및 자식 프로세스 종료

        Args:
            process: 종료할 프로세스
            simulation_id: 시뮬레이션 ID (로그용)
            timeout: 프로세스 종료를 기다리는 타임아웃 (초)
        """
        if IS_WINDOWS:
            # Windows: taskkill 명령으로 프로세스 트리 종료
            # /F = 강제 종료, /T = 프로세스 트리 종료 (자식 프로세스 포함)
            logger.info(f"프로세스 트리 종료 (Windows): simulation={simulation_id}, pid={process.pid}")
            try:
                # 먼저 정상 종료 시도
                subprocess.run(
                    ['taskkill', '/PID', str(process.pid), '/T'],
                    capture_output=True,
                    timeout=5
                )
                try:
                    process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    # 강제 종료
                    logger.warning(f"프로세스가 응답하지 않아 강제 종료: {simulation_id}")
                    subprocess.run(
                        ['taskkill', '/F', '/PID', str(process.pid), '/T'],
                        capture_output=True,
                        timeout=5
                    )
                    process.wait(timeout=5)
            except Exception as e:
                logger.warning(f"taskkill 실패, terminate 시도: {e}")
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
        else:
            # Unix: 프로세스 그룹 종료
            # start_new_session=True 사용으로 프로세스 그룹 ID = 메인 프로세스 PID
            pgid = os.getpgid(process.pid)
            logger.info(f"프로세스 그룹 종료 (Unix): simulation={simulation_id}, pgid={pgid}")

            # 먼저 전체 프로세스 그룹에 SIGTERM 전송
            os.killpg(pgid, signal.SIGTERM)

            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                # 타임아웃 후에도 종료되지 않으면 강제로 SIGKILL 전송
                logger.warning(f"프로세스 그룹이 SIGTERM에 응답하지 않아 강제 종료: {simulation_id}")
                os.killpg(pgid, signal.SIGKILL)
                process.wait(timeout=5)
    
    @classmethod
    def stop_simulation(cls, simulation_id: str) -> SimulationRunState:
        """시뮬레이션 중지"""
        state = cls.get_run_state(simulation_id)
        if not state:
            raise ValueError(f"시뮬레이션이 존재하지 않습니다: {simulation_id}")

        if state.runner_status not in [RunnerStatus.RUNNING, RunnerStatus.PAUSED]:
            raise ValueError(f"시뮬레이션이 실행 중이 아닙니다: {simulation_id}, status={state.runner_status}")

        state.runner_status = RunnerStatus.STOPPING
        cls._save_run_state(state)

        # 프로세스 종료
        process = cls._processes.get(simulation_id)
        if process and process.poll() is None:
            try:
                cls._terminate_process(process, simulation_id)
            except ProcessLookupError:
                # 프로세스가 이미 존재하지 않음
                pass
            except Exception as e:
                logger.error(f"프로세스 그룹 종료 실패: {simulation_id}, error={e}")
                # 직접 프로세스 종료로 폴백
                try:
                    process.terminate()
                    process.wait(timeout=5)
                except Exception:
                    process.kill()

        state.runner_status = RunnerStatus.STOPPED
        state.twitter_running = False
        state.reddit_running = False
        state.completed_at = datetime.now().isoformat()
        cls._save_run_state(state)

        # 그래프 메모리 업데이터 중지
        if cls._graph_memory_enabled.get(simulation_id, False):
            try:
                ZepGraphMemoryManager.stop_updater(simulation_id)
                logger.info(f"그래프 메모리 업데이트 중지됨: simulation_id={simulation_id}")
            except Exception as e:
                logger.error(f"그래프 메모리 업데이터 중지 실패: {e}")
            cls._graph_memory_enabled.pop(simulation_id, None)

        logger.info(f"시뮬레이션 중지됨: {simulation_id}")
        return state
    
    @classmethod
    def _read_actions_from_file(
        cls,
        file_path: str,
        default_platform: Optional[str] = None,
        platform_filter: Optional[str] = None,
        agent_id: Optional[int] = None,
        round_num: Optional[int] = None
    ) -> List[AgentAction]:
        """
        단일 동작 파일에서 동작 읽기

        Args:
            file_path: 동작 로그 파일 경로
            default_platform: 기본 플랫폼 (동작 레코드에 platform 필드가 없을 때 사용)
            platform_filter: 플랫폼 필터
            agent_id: Agent ID 필터
            round_num: 라운드 필터
        """
        if not os.path.exists(file_path):
            return []

        actions = []

        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                try:
                    data = json.loads(line)

                    # 비동작 레코드 건너뛰기 (예: simulation_start, round_start, round_end 이벤트)
                    if "event_type" in data:
                        continue

                    # agent_id가 없는 레코드 건너뛰기 (Agent 동작 아님)
                    if "agent_id" not in data:
                        continue

                    # 플랫폼 가져오기: 레코드의 platform 우선, 없으면 기본 플랫폼 사용
                    record_platform = data.get("platform") or default_platform or ""

                    # 필터링
                    if platform_filter and record_platform != platform_filter:
                        continue
                    if agent_id is not None and data.get("agent_id") != agent_id:
                        continue
                    if round_num is not None and data.get("round") != round_num:
                        continue
                    
                    actions.append(AgentAction(
                        round_num=data.get("round", 0),
                        timestamp=data.get("timestamp", ""),
                        platform=record_platform,
                        agent_id=data.get("agent_id", 0),
                        agent_name=data.get("agent_name", ""),
                        action_type=data.get("action_type", ""),
                        action_args=data.get("action_args", {}),
                        result=data.get("result"),
                        success=data.get("success", True),
                    ))
                    
                except json.JSONDecodeError:
                    continue
        
        return actions
    
    @classmethod
    def get_all_actions(
        cls,
        simulation_id: str,
        platform: Optional[str] = None,
        agent_id: Optional[int] = None,
        round_num: Optional[int] = None
    ) -> List[AgentAction]:
        """
        모든 플랫폼의 전체 동작 이력 가져오기 (페이지네이션 없음)

        Args:
            simulation_id: 시뮬레이션 ID
            platform: 플랫폼 필터 (twitter/reddit)
            agent_id: Agent 필터
            round_num: 라운드 필터

        Returns:
            전체 동작 목록 (타임스탬프 기준 정렬, 최신순)
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        actions = []

        # Twitter 동작 파일 읽기 (파일 경로에 따라 platform을 twitter로 자동 설정)
        twitter_actions_log = os.path.join(sim_dir, "twitter", "actions.jsonl")
        if not platform or platform == "twitter":
            actions.extend(cls._read_actions_from_file(
                twitter_actions_log,
                default_platform="twitter",  # platform 필드 자동 채우기
                platform_filter=platform,
                agent_id=agent_id,
                round_num=round_num
            ))

        # Reddit 동작 파일 읽기 (파일 경로에 따라 platform을 reddit으로 자동 설정)
        reddit_actions_log = os.path.join(sim_dir, "reddit", "actions.jsonl")
        if not platform or platform == "reddit":
            actions.extend(cls._read_actions_from_file(
                reddit_actions_log,
                default_platform="reddit",  # platform 필드 자동 채우기
                platform_filter=platform,
                agent_id=agent_id,
                round_num=round_num
            ))

        # 플랫폼별 파일이 없으면 구형 단일 파일 형식 시도
        if not actions:
            actions_log = os.path.join(sim_dir, "actions.jsonl")
            actions = cls._read_actions_from_file(
                actions_log,
                default_platform=None,  # 구형 형식 파일에는 platform 필드가 있어야 함
                platform_filter=platform,
                agent_id=agent_id,
                round_num=round_num
            )

        # 타임스탬프 기준 정렬 (최신순)
        actions.sort(key=lambda x: x.timestamp, reverse=True)
        
        return actions
    
    @classmethod
    def get_actions(
        cls,
        simulation_id: str,
        limit: int = 100,
        offset: int = 0,
        platform: Optional[str] = None,
        agent_id: Optional[int] = None,
        round_num: Optional[int] = None
    ) -> List[AgentAction]:
        """
        동작 이력 가져오기 (페이지네이션 포함)

        Args:
            simulation_id: 시뮬레이션 ID
            limit: 반환 수량 제한
            offset: 오프셋
            platform: 플랫폼 필터
            agent_id: Agent 필터
            round_num: 라운드 필터

        Returns:
            동작 목록
        """
        actions = cls.get_all_actions(
            simulation_id=simulation_id,
            platform=platform,
            agent_id=agent_id,
            round_num=round_num
        )

        # 페이지네이션
        return actions[offset:offset + limit]

    @classmethod
    def get_timeline(
        cls,
        simulation_id: str,
        start_round: int = 0,
        end_round: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        시뮬레이션 타임라인 가져오기 (라운드별 요약)

        Args:
            simulation_id: 시뮬레이션 ID
            start_round: 시작 라운드
            end_round: 종료 라운드

        Returns:
            각 라운드의 요약 정보
        """
        actions = cls.get_actions(simulation_id, limit=10000)

        # 라운드별 그룹화
        rounds: Dict[int, Dict[str, Any]] = {}

        for action in actions:
            round_num = action.round_num

            if round_num < start_round:
                continue
            if end_round is not None and round_num > end_round:
                continue

            if round_num not in rounds:
                rounds[round_num] = {
                    "round_num": round_num,
                    "twitter_actions": 0,
                    "reddit_actions": 0,
                    "active_agents": set(),
                    "action_types": {},
                    "first_action_time": action.timestamp,
                    "last_action_time": action.timestamp,
                }

            r = rounds[round_num]

            if action.platform == "twitter":
                r["twitter_actions"] += 1
            else:
                r["reddit_actions"] += 1

            r["active_agents"].add(action.agent_id)
            r["action_types"][action.action_type] = r["action_types"].get(action.action_type, 0) + 1
            r["last_action_time"] = action.timestamp

        # 리스트로 변환
        result = []
        for round_num in sorted(rounds.keys()):
            r = rounds[round_num]
            result.append({
                "round_num": round_num,
                "twitter_actions": r["twitter_actions"],
                "reddit_actions": r["reddit_actions"],
                "total_actions": r["twitter_actions"] + r["reddit_actions"],
                "active_agents_count": len(r["active_agents"]),
                "active_agents": list(r["active_agents"]),
                "action_types": r["action_types"],
                "first_action_time": r["first_action_time"],
                "last_action_time": r["last_action_time"],
            })

        return result

    @classmethod
    def get_agent_stats(cls, simulation_id: str) -> List[Dict[str, Any]]:
        """
        각 Agent의 통계 정보 가져오기

        Returns:
            Agent 통계 목록
        """
        actions = cls.get_actions(simulation_id, limit=10000)
        
        agent_stats: Dict[int, Dict[str, Any]] = {}
        
        for action in actions:
            agent_id = action.agent_id
            
            if agent_id not in agent_stats:
                agent_stats[agent_id] = {
                    "agent_id": agent_id,
                    "agent_name": action.agent_name,
                    "total_actions": 0,
                    "twitter_actions": 0,
                    "reddit_actions": 0,
                    "action_types": {},
                    "first_action_time": action.timestamp,
                    "last_action_time": action.timestamp,
                }
            
            stats = agent_stats[agent_id]
            stats["total_actions"] += 1
            
            if action.platform == "twitter":
                stats["twitter_actions"] += 1
            else:
                stats["reddit_actions"] += 1
            
            stats["action_types"][action.action_type] = stats["action_types"].get(action.action_type, 0) + 1
            stats["last_action_time"] = action.timestamp
        
        # 총 동작 수 기준 정렬
        result = sorted(agent_stats.values(), key=lambda x: x["total_actions"], reverse=True)

        return result

    @classmethod
    def cleanup_simulation_logs(cls, simulation_id: str) -> Dict[str, Any]:
        """
        시뮬레이션 실행 로그 정리 (시뮬레이션 강제 재시작에 사용)

        삭제되는 파일 목록:
        - run_state.json
        - twitter/actions.jsonl
        - reddit/actions.jsonl
        - simulation.log
        - stdout.log / stderr.log
        - twitter_simulation.db (시뮬레이션 데이터베이스)
        - reddit_simulation.db (시뮬레이션 데이터베이스)
        - env_status.json (환경 상태)

        주의: 설정 파일(simulation_config.json)과 profile 파일은 삭제하지 않음

        Args:
            simulation_id: 시뮬레이션 ID

        Returns:
            정리 결과 정보
        """
        import shutil

        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)

        if not os.path.exists(sim_dir):
            return {"success": True, "message": "시뮬레이션 디렉터리가 존재하지 않아 정리 불필요"}

        cleaned_files = []
        errors = []

        # 삭제할 파일 목록 (데이터베이스 파일 포함)
        files_to_delete = [
            "run_state.json",
            "simulation.log",
            "stdout.log",
            "stderr.log",
            "twitter_simulation.db",  # Twitter 플랫폼 데이터베이스
            "reddit_simulation.db",   # Reddit 플랫폼 데이터베이스
            "env_status.json",        # 환경 상태 파일
        ]

        # 삭제할 디렉터리 목록 (동작 로그 포함)
        dirs_to_clean = ["twitter", "reddit"]

        # 파일 삭제
        for filename in files_to_delete:
            file_path = os.path.join(sim_dir, filename)
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    cleaned_files.append(filename)
                except Exception as e:
                    errors.append(f"{filename} 삭제 실패: {str(e)}")

        # 플랫폼 디렉터리의 동작 로그 정리
        for dir_name in dirs_to_clean:
            dir_path = os.path.join(sim_dir, dir_name)
            if os.path.exists(dir_path):
                actions_file = os.path.join(dir_path, "actions.jsonl")
                if os.path.exists(actions_file):
                    try:
                        os.remove(actions_file)
                        cleaned_files.append(f"{dir_name}/actions.jsonl")
                    except Exception as e:
                        errors.append(f"{dir_name}/actions.jsonl 삭제 실패: {str(e)}")

        # 메모리 내 실행 상태 정리
        if simulation_id in cls._run_states:
            del cls._run_states[simulation_id]

        logger.info(f"시뮬레이션 로그 정리 완료: {simulation_id}, 삭제된 파일: {cleaned_files}")

        return {
            "success": len(errors) == 0,
            "cleaned_files": cleaned_files,
            "errors": errors if errors else None
        }
    
    # 중복 정리 방지 플래그
    _cleanup_done = False

    @classmethod
    def cleanup_all_simulations(cls):
        """
        실행 중인 모든 시뮬레이션 프로세스 정리

        서버 종료 시 호출되어 모든 자식 프로세스가 종료되도록 보장
        """
        # 중복 정리 방지
        if cls._cleanup_done:
            return
        cls._cleanup_done = True

        # 정리할 내용이 있는지 확인 (빈 프로세스로 불필요한 로그 출력 방지)
        has_processes = bool(cls._processes)
        has_updaters = bool(cls._graph_memory_enabled)

        if not has_processes and not has_updaters:
            return  # 정리할 내용이 없으면 조용히 반환

        logger.info("모든 시뮬레이션 프로세스 정리 중...")

        # 먼저 모든 그래프 메모리 업데이터 중지 (stop_all 내부에서 로그 출력)
        try:
            ZepGraphMemoryManager.stop_all()
        except Exception as e:
            logger.error(f"그래프 메모리 업데이터 중지 실패: {e}")
        cls._graph_memory_enabled.clear()

        # 반복 중 딕셔너리 수정을 피하기 위해 복사
        processes = list(cls._processes.items())

        for simulation_id, process in processes:
            try:
                if process.poll() is None:  # 프로세스가 아직 실행 중
                    logger.info(f"시뮬레이션 프로세스 종료: {simulation_id}, pid={process.pid}")

                    try:
                        # 크로스 플랫폼 프로세스 종료 방법 사용
                        cls._terminate_process(process, simulation_id, timeout=5)
                    except (ProcessLookupError, OSError):
                        # 프로세스가 이미 존재하지 않을 수 있음, 직접 종료 시도
                        try:
                            process.terminate()
                            process.wait(timeout=3)
                        except Exception:
                            process.kill()

                    # run_state.json 업데이트
                    state = cls.get_run_state(simulation_id)
                    if state:
                        state.runner_status = RunnerStatus.STOPPED
                        state.twitter_running = False
                        state.reddit_running = False
                        state.completed_at = datetime.now().isoformat()
                        state.error = "Server shutdown, simulation terminated"
                        cls._save_run_state(state)

                    # state.json도 업데이트하여 상태를 stopped로 변경
                    try:
                        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
                        state_file = os.path.join(sim_dir, "state.json")
                        logger.info(f"state.json 업데이트 시도: {state_file}")
                        if os.path.exists(state_file):
                            with open(state_file, 'r', encoding='utf-8') as f:
                                state_data = json.load(f)
                            state_data['status'] = 'stopped'
                            state_data['updated_at'] = datetime.now().isoformat()
                            with open(state_file, 'w', encoding='utf-8') as f:
                                json.dump(state_data, f, indent=2, ensure_ascii=False)
                            logger.info(f"state.json 상태를 stopped로 업데이트됨: {simulation_id}")
                        else:
                            logger.warning(f"state.json이 존재하지 않음: {state_file}")
                    except Exception as state_err:
                        logger.warning(f"state.json 업데이트 실패: {simulation_id}, error={state_err}")

            except Exception as e:
                logger.error(f"프로세스 정리 실패: {simulation_id}, error={e}")

        # 파일 핸들 정리
        for simulation_id, file_handle in list(cls._stdout_files.items()):
            try:
                if file_handle:
                    file_handle.close()
            except Exception:
                pass
        cls._stdout_files.clear()

        for simulation_id, file_handle in list(cls._stderr_files.items()):
            try:
                if file_handle:
                    file_handle.close()
            except Exception:
                pass
        cls._stderr_files.clear()

        # 메모리 내 상태 정리
        cls._processes.clear()
        cls._action_queues.clear()

        logger.info("시뮬레이션 프로세스 정리 완료")
    
    @classmethod
    def register_cleanup(cls):
        """
        정리 함수 등록

        Flask 앱 시작 시 호출되어 서버 종료 시 모든 시뮬레이션 프로세스를 정리하도록 보장
        """
        global _cleanup_registered

        if _cleanup_registered:
            return

        # Flask debug 모드에서는 리로더 자식 프로세스에서만 정리 등록 (실제 앱을 실행하는 프로세스)
        # WERKZEUG_RUN_MAIN=true는 리로더 자식 프로세스를 의미
        # debug 모드가 아닌 경우 이 환경 변수가 없어도 등록 필요
        is_reloader_process = os.environ.get('WERKZEUG_RUN_MAIN') == 'true'
        is_debug_mode = os.environ.get('FLASK_DEBUG') == '1' or os.environ.get('WERKZEUG_RUN_MAIN') is not None

        # debug 모드에서는 리로더 자식 프로세스에서만 등록; 비 debug 모드에서는 항상 등록
        if is_debug_mode and not is_reloader_process:
            _cleanup_registered = True  # 등록된 것으로 표시, 자식 프로세스가 재시도하지 않도록
            return

        # 기존 시그널 핸들러 저장
        original_sigint = signal.getsignal(signal.SIGINT)
        original_sigterm = signal.getsignal(signal.SIGTERM)
        # SIGHUP은 Unix 시스템에만 존재 (macOS/Linux), Windows에는 없음
        original_sighup = None
        has_sighup = hasattr(signal, 'SIGHUP')
        if has_sighup:
            original_sighup = signal.getsignal(signal.SIGHUP)

        def cleanup_handler(signum=None, frame=None):
            """시그널 핸들러: 시뮬레이션 프로세스 먼저 정리 후 원래 핸들러 호출"""
            # 정리할 프로세스가 있을 때만 로그 출력
            if cls._processes or cls._graph_memory_enabled:
                logger.info(f"시그널 {signum} 수신, 정리 시작...")
            cls.cleanup_all_simulations()

            # 원래 시그널 핸들러 호출하여 Flask가 정상 종료되도록
            if signum == signal.SIGINT and callable(original_sigint):
                original_sigint(signum, frame)
            elif signum == signal.SIGTERM and callable(original_sigterm):
                original_sigterm(signum, frame)
            elif has_sighup and signum == signal.SIGHUP:
                # SIGHUP: 터미널 종료 시 전송
                if callable(original_sighup):
                    original_sighup(signum, frame)
                else:
                    # 기본 동작: 정상 종료
                    sys.exit(0)
            else:
                # 원래 핸들러를 호출할 수 없는 경우 (예: SIG_DFL) 기본 동작 사용
                raise KeyboardInterrupt

        # atexit 핸들러 등록 (백업용)
        atexit.register(cls.cleanup_all_simulations)

        # 시그널 핸들러 등록 (메인 스레드에서만)
        try:
            # SIGTERM: kill 명령 기본 시그널
            signal.signal(signal.SIGTERM, cleanup_handler)
            # SIGINT: Ctrl+C
            signal.signal(signal.SIGINT, cleanup_handler)
            # SIGHUP: 터미널 종료 (Unix 시스템 전용)
            if has_sighup:
                signal.signal(signal.SIGHUP, cleanup_handler)
        except ValueError:
            # 메인 스레드가 아니면 atexit만 사용 가능
            logger.warning("시그널 핸들러 등록 불가 (메인 스레드 아님), atexit만 사용")

        _cleanup_registered = True

    @classmethod
    def get_running_simulations(cls) -> List[str]:
        """
        실행 중인 모든 시뮬레이션 ID 목록 가져오기
        """
        running = []
        for sim_id, process in cls._processes.items():
            if process.poll() is None:
                running.append(sim_id)
        return running

    # ============== Interview 기능 ==============

    @classmethod
    def check_env_alive(cls, simulation_id: str) -> bool:
        """
        시뮬레이션 환경이 살아있는지 확인 (Interview 명령을 수신할 수 있는지)

        Args:
            simulation_id: 시뮬레이션 ID

        Returns:
            True이면 환경 활성, False이면 환경 종료
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        if not os.path.exists(sim_dir):
            return False

        ipc_client = SimulationIPCClient(sim_dir)
        return ipc_client.check_env_alive()

    @classmethod
    def get_env_status_detail(cls, simulation_id: str) -> Dict[str, Any]:
        """
        시뮬레이션 환경의 상세 상태 정보 가져오기

        Args:
            simulation_id: 시뮬레이션 ID

        Returns:
            상태 상세 딕셔너리, status, twitter_available, reddit_available, timestamp 포함
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        status_file = os.path.join(sim_dir, "env_status.json")
        
        default_status = {
            "status": "stopped",
            "twitter_available": False,
            "reddit_available": False,
            "timestamp": None
        }
        
        if not os.path.exists(status_file):
            return default_status
        
        try:
            with open(status_file, 'r', encoding='utf-8') as f:
                status = json.load(f)
            return {
                "status": status.get("status", "stopped"),
                "twitter_available": status.get("twitter_available", False),
                "reddit_available": status.get("reddit_available", False),
                "timestamp": status.get("timestamp")
            }
        except (json.JSONDecodeError, OSError):
            return default_status

    @classmethod
    def interview_agent(
        cls,
        simulation_id: str,
        agent_id: int,
        prompt: str,
        platform: str = None,
        timeout: float = 60.0
    ) -> Dict[str, Any]:
        """
        단일 Agent 인터뷰

        Args:
            simulation_id: 시뮬레이션 ID
            agent_id: Agent ID
            prompt: 인터뷰 질문
            platform: 플랫폼 지정 (선택사항)
                - "twitter": Twitter 플랫폼만 인터뷰
                - "reddit": Reddit 플랫폼만 인터뷰
                - None: 양 플랫폼 시뮬레이션 시 두 플랫폼 동시 인터뷰, 통합 결과 반환
            timeout: 타임아웃 (초)

        Returns:
            인터뷰 결과 딕셔너리

        Raises:
            ValueError: 시뮬레이션이 존재하지 않거나 환경이 실행 중이 아닌 경우
            TimeoutError: 응답 대기 타임아웃
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        if not os.path.exists(sim_dir):
            raise ValueError(f"시뮬레이션이 존재하지 않습니다: {simulation_id}")

        ipc_client = SimulationIPCClient(sim_dir)

        if not ipc_client.check_env_alive():
            raise ValueError(f"시뮬레이션 환경이 실행 중이 아니거나 종료됨, Interview 수행 불가: {simulation_id}")

        logger.info(f"Interview 명령 전송: simulation_id={simulation_id}, agent_id={agent_id}, platform={platform}")

        response = ipc_client.send_interview(
            agent_id=agent_id,
            prompt=prompt,
            platform=platform,
            timeout=timeout
        )

        if response.status.value == "completed":
            return {
                "success": True,
                "agent_id": agent_id,
                "prompt": prompt,
                "result": response.result,
                "timestamp": response.timestamp
            }
        else:
            return {
                "success": False,
                "agent_id": agent_id,
                "prompt": prompt,
                "error": response.error,
                "timestamp": response.timestamp
            }
    
    @classmethod
    def interview_agents_batch(
        cls,
        simulation_id: str,
        interviews: List[Dict[str, Any]],
        platform: str = None,
        timeout: float = 120.0
    ) -> Dict[str, Any]:
        """
        다수 Agent 일괄 인터뷰

        Args:
            simulation_id: 시뮬레이션 ID
            interviews: 인터뷰 목록, 각 요소는 {"agent_id": int, "prompt": str, "platform": str(선택)} 포함
            platform: 기본 플랫폼 (선택사항, 각 인터뷰 항목의 platform으로 재정의 가능)
                - "twitter": 기본적으로 Twitter 플랫폼만 인터뷰
                - "reddit": 기본적으로 Reddit 플랫폼만 인터뷰
                - None: 양 플랫폼 시뮬레이션 시 각 Agent를 두 플랫폼 동시 인터뷰
            timeout: 타임아웃 (초)

        Returns:
            일괄 인터뷰 결과 딕셔너리

        Raises:
            ValueError: 시뮬레이션이 존재하지 않거나 환경이 실행 중이 아닌 경우
            TimeoutError: 응답 대기 타임아웃
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        if not os.path.exists(sim_dir):
            raise ValueError(f"시뮬레이션이 존재하지 않습니다: {simulation_id}")

        ipc_client = SimulationIPCClient(sim_dir)

        if not ipc_client.check_env_alive():
            raise ValueError(f"시뮬레이션 환경이 실행 중이 아니거나 종료됨, Interview 수행 불가: {simulation_id}")

        logger.info(f"일괄 Interview 명령 전송: simulation_id={simulation_id}, count={len(interviews)}, platform={platform}")

        response = ipc_client.send_batch_interview(
            interviews=interviews,
            platform=platform,
            timeout=timeout
        )

        if response.status.value == "completed":
            return {
                "success": True,
                "interviews_count": len(interviews),
                "result": response.result,
                "timestamp": response.timestamp
            }
        else:
            return {
                "success": False,
                "interviews_count": len(interviews),
                "error": response.error,
                "timestamp": response.timestamp
            }
    
    @classmethod
    def interview_all_agents(
        cls,
        simulation_id: str,
        prompt: str,
        platform: str = None,
        timeout: float = 180.0
    ) -> Dict[str, Any]:
        """
        모든 Agent 인터뷰 (전체 인터뷰)

        시뮬레이션의 모든 Agent에게 동일한 질문으로 인터뷰

        Args:
            simulation_id: 시뮬레이션 ID
            prompt: 인터뷰 질문 (모든 Agent에게 동일한 질문 사용)
            platform: 플랫폼 지정 (선택사항)
                - "twitter": Twitter 플랫폼만 인터뷰
                - "reddit": Reddit 플랫폼만 인터뷰
                - None: 양 플랫폼 시뮬레이션 시 각 Agent를 두 플랫폼 동시 인터뷰
            timeout: 타임아웃 (초)

        Returns:
            전체 인터뷰 결과 딕셔너리
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        if not os.path.exists(sim_dir):
            raise ValueError(f"시뮬레이션이 존재하지 않습니다: {simulation_id}")

        # 설정 파일에서 모든 Agent 정보 가져오기
        config_path = os.path.join(sim_dir, "simulation_config.json")
        if not os.path.exists(config_path):
            raise ValueError(f"시뮬레이션 설정이 존재하지 않습니다: {simulation_id}")

        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)

        agent_configs = config.get("agent_configs", [])
        if not agent_configs:
            raise ValueError(f"시뮬레이션 설정에 Agent가 없습니다: {simulation_id}")

        # 일괄 인터뷰 목록 구성 (사물/장소/개념 에이전트 제외)
        from .oasis_profile_generator import OasisProfileGenerator
        _checker = OasisProfileGenerator.__new__(OasisProfileGenerator)
        interviews = []
        skipped_interview = []
        for agent_config in agent_configs:
            agent_id = agent_config.get("agent_id")
            entity_type = agent_config.get("entity_type", "")
            if agent_id is not None:
                if entity_type and _checker._is_inanimate_entity_type(entity_type):
                    skipped_interview.append(f"{agent_config.get('name', agent_id)}({entity_type})")
                else:
                    interviews.append({
                        "agent_id": agent_id,
                        "prompt": prompt
                    })
        if skipped_interview:
            logger.info(f"인터뷰 제외 엔티티 {len(skipped_interview)}개: {', '.join(skipped_interview)}")

        logger.info(f"전체 Interview 명령 전송: simulation_id={simulation_id}, agent_count={len(interviews)}, platform={platform}")

        return cls.interview_agents_batch(
            simulation_id=simulation_id,
            interviews=interviews,
            platform=platform,
            timeout=timeout
        )
    
    @classmethod
    def close_simulation_env(
        cls,
        simulation_id: str,
        timeout: float = 30.0
    ) -> Dict[str, Any]:
        """
        시뮬레이션 환경 종료 (시뮬레이션 프로세스 중지가 아님)

        시뮬레이션에 환경 종료 명령을 전송하여 명령 대기 모드를 정상 종료

        Args:
            simulation_id: 시뮬레이션 ID
            timeout: 타임아웃 (초)

        Returns:
            작업 결과 딕셔너리
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        if not os.path.exists(sim_dir):
            raise ValueError(f"시뮬레이션이 존재하지 않습니다: {simulation_id}")

        ipc_client = SimulationIPCClient(sim_dir)

        if not ipc_client.check_env_alive():
            return {
                "success": True,
                "message": "환경이 이미 종료됨"
            }

        logger.info(f"환경 종료 명령 전송: simulation_id={simulation_id}")

        try:
            response = ipc_client.send_close_env(timeout=timeout)

            return {
                "success": response.status.value == "completed",
                "message": "환경 종료 명령 전송됨",
                "result": response.result,
                "timestamp": response.timestamp
            }
        except TimeoutError:
            # 타임아웃은 환경이 종료 중인 경우일 수 있음
            return {
                "success": True,
                "message": "환경 종료 명령 전송됨 (응답 대기 타임아웃, 환경이 종료 중일 수 있음)"
            }
    
    @classmethod
    def _get_interview_history_from_db(
        cls,
        db_path: str,
        platform_name: str,
        agent_id: Optional[int] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """단일 데이터베이스에서 Interview 이력 가져오기"""
        import sqlite3
        
        if not os.path.exists(db_path):
            return []
        
        results = []
        
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            if agent_id is not None:
                cursor.execute("""
                    SELECT user_id, info, created_at
                    FROM trace
                    WHERE action = 'interview' AND user_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (agent_id, limit))
            else:
                cursor.execute("""
                    SELECT user_id, info, created_at
                    FROM trace
                    WHERE action = 'interview'
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (limit,))
            
            for user_id, info_json, created_at in cursor.fetchall():
                try:
                    info = json.loads(info_json) if info_json else {}
                except json.JSONDecodeError:
                    info = {"raw": info_json}
                
                results.append({
                    "agent_id": user_id,
                    "response": info.get("response", info),
                    "prompt": info.get("prompt", ""),
                    "timestamp": created_at,
                    "platform": platform_name
                })
            
            conn.close()
            
        except Exception as e:
            logger.error(f"Interview 이력 읽기 실패 ({platform_name}): {e}")
        
        return results

    @classmethod
    def get_interview_history(
        cls,
        simulation_id: str,
        platform: str = None,
        agent_id: Optional[int] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Interview 이력 레코드 가져오기 (데이터베이스에서 읽기)

        Args:
            simulation_id: 시뮬레이션 ID
            platform: 플랫폼 유형 (reddit/twitter/None)
                - "reddit": Reddit 플랫폼 이력만 가져오기
                - "twitter": Twitter 플랫폼 이력만 가져오기
                - None: 두 플랫폼의 모든 이력 가져오기
            agent_id: Agent ID 지정 (선택사항, 해당 Agent의 이력만 가져오기)
            limit: 플랫폼당 반환 수량 제한

        Returns:
            Interview 이력 레코드 목록
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)

        results = []

        # 조회할 플랫폼 결정
        if platform in ("reddit", "twitter"):
            platforms = [platform]
        else:
            # platform 미지정 시 두 플랫폼 조회
            platforms = ["twitter", "reddit"]

        for p in platforms:
            db_path = os.path.join(sim_dir, f"{p}_simulation.db")
            platform_results = cls._get_interview_history_from_db(
                db_path=db_path,
                platform_name=p,
                agent_id=agent_id,
                limit=limit
            )
            results.extend(platform_results)

        # 시간 기준 내림차순 정렬
        results.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

        # 여러 플랫폼 조회 시 총 수량 제한
        if len(platforms) > 1 and len(results) > limit:
            results = results[:limit]

        return results

