"""
시뮬레이션 IPC 통신 모듈
Flask 백엔드와 시뮬레이션 스크립트 간의 프로세스 간 통신에 사용

파일 시스템을 통해 간단한 명령/응답 패턴을 구현:
1. Flask가 commands/ 디렉토리에 명령 파일 작성
2. 시뮬레이션 스크립트가 명령 디렉토리를 폴링하고, 명령을 실행한 후 responses/ 디렉토리에 응답 파일 작성
3. Flask가 응답 디렉토리를 폴링하여 결과를 가져옴
"""

import os
import json
import time
import uuid
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ..utils.logger import get_logger

logger = get_logger('mirofish.simulation_ipc')


class CommandType(str, Enum):
    """명령 타입"""
    INTERVIEW = "interview"           # 단일 Agent 인터뷰
    BATCH_INTERVIEW = "batch_interview"  # 배치 인터뷰
    CLOSE_ENV = "close_env"           # 환경 종료


class CommandStatus(str, Enum):
    """명령 상태"""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class IPCCommand:
    """IPC 명령"""
    command_id: str
    command_type: CommandType
    args: Dict[str, Any]
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "command_id": self.command_id,
            "command_type": self.command_type.value,
            "args": self.args,
            "timestamp": self.timestamp
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'IPCCommand':
        return cls(
            command_id=data["command_id"],
            command_type=CommandType(data["command_type"]),
            args=data.get("args", {}),
            timestamp=data.get("timestamp", datetime.now().isoformat())
        )


@dataclass
class IPCResponse:
    """IPC 응답"""
    command_id: str
    status: CommandStatus
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "command_id": self.command_id,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "timestamp": self.timestamp
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'IPCResponse':
        return cls(
            command_id=data["command_id"],
            status=CommandStatus(data["status"]),
            result=data.get("result"),
            error=data.get("error"),
            timestamp=data.get("timestamp", datetime.now().isoformat())
        )


class SimulationIPCClient:
    """
    시뮬레이션 IPC 클라이언트 (Flask 측에서 사용)

    시뮬레이션 프로세스에 명령을 보내고 응답을 기다림
    """

    def __init__(self, simulation_dir: str):
        """
        IPC 클라이언트 초기화

        Args:
            simulation_dir: 시뮬레이션 데이터 디렉토리
        """
        self.simulation_dir = simulation_dir
        self.commands_dir = os.path.join(simulation_dir, "ipc_commands")
        self.responses_dir = os.path.join(simulation_dir, "ipc_responses")

        # 디렉토리 존재 확인
        os.makedirs(self.commands_dir, exist_ok=True)
        os.makedirs(self.responses_dir, exist_ok=True)

    def send_command(
        self,
        command_type: CommandType,
        args: Dict[str, Any],
        timeout: float = 60.0,
        poll_interval: float = 0.5
    ) -> IPCResponse:
        """
        명령 전송 및 응답 대기

        Args:
            command_type: 명령 타입
            args: 명령 파라미터
            timeout: 타임아웃 시간 (초)
            poll_interval: 폴링 간격 (초)

        Returns:
            IPCResponse

        Raises:
            TimeoutError: 응답 대기 타임아웃
        """
        command_id = str(uuid.uuid4())
        command = IPCCommand(
            command_id=command_id,
            command_type=command_type,
            args=args
        )

        # 명령 파일 작성
        command_file = os.path.join(self.commands_dir, f"{command_id}.json")
        with open(command_file, 'w', encoding='utf-8') as f:
            json.dump(command.to_dict(), f, ensure_ascii=False, indent=2)

        logger.info(f"IPC 명령 전송: {command_type.value}, command_id={command_id}")

        # 응답 대기
        response_file = os.path.join(self.responses_dir, f"{command_id}.json")
        start_time = time.time()

        while time.time() - start_time < timeout:
            if os.path.exists(response_file):
                try:
                    with open(response_file, 'r', encoding='utf-8') as f:
                        response_data = json.load(f)
                    response = IPCResponse.from_dict(response_data)

                    # 명령 및 응답 파일 정리
                    try:
                        os.remove(command_file)
                        os.remove(response_file)
                    except OSError:
                        pass

                    logger.info(f"IPC 응답 수신: command_id={command_id}, status={response.status.value}")
                    return response
                except (json.JSONDecodeError, KeyError) as e:
                    logger.warning(f"응답 파싱 실패: {e}")

            time.sleep(poll_interval)

        # 타임아웃
        logger.error(f"IPC 응답 대기 타임아웃: command_id={command_id}")

        # 명령 파일 정리
        try:
            os.remove(command_file)
        except OSError:
            pass

        raise TimeoutError(f"명령 응답 대기 타임아웃 ({timeout}초)")

    def send_interview(
        self,
        agent_id: int,
        prompt: str,
        platform: str = None,
        timeout: float = 60.0
    ) -> IPCResponse:
        """
        단일 Agent 인터뷰 명령 전송

        Args:
            agent_id: Agent ID
            prompt: 인터뷰 질문
            platform: 지정 플랫폼 (선택)
                - "twitter": Twitter 플랫폼만 인터뷰
                - "reddit": Reddit 플랫폼만 인터뷰
                - None: 이중 플랫폼 시뮬레이션 시 양 플랫폼 동시 인터뷰, 단일 플랫폼 시 해당 플랫폼 인터뷰
            timeout: 타임아웃 시간

        Returns:
            IPCResponse, result 필드에 인터뷰 결과 포함
        """
        args = {
            "agent_id": agent_id,
            "prompt": prompt
        }
        if platform:
            args["platform"] = platform

        return self.send_command(
            command_type=CommandType.INTERVIEW,
            args=args,
            timeout=timeout
        )

    def send_batch_interview(
        self,
        interviews: List[Dict[str, Any]],
        platform: str = None,
        timeout: float = 120.0
    ) -> IPCResponse:
        """
        배치 인터뷰 명령 전송

        Args:
            interviews: 인터뷰 목록, 각 요소는 {"agent_id": int, "prompt": str, "platform": str(선택)} 포함
            platform: 기본 플랫폼 (선택, 각 인터뷰 항목의 platform으로 덮어씀)
                - "twitter": 기본으로 Twitter 플랫폼만 인터뷰
                - "reddit": 기본으로 Reddit 플랫폼만 인터뷰
                - None: 이중 플랫폼 시뮬레이션 시 각 Agent가 양 플랫폼 동시 인터뷰
            timeout: 타임아웃 시간

        Returns:
            IPCResponse, result 필드에 모든 인터뷰 결과 포함
        """
        args = {"interviews": interviews}
        if platform:
            args["platform"] = platform

        return self.send_command(
            command_type=CommandType.BATCH_INTERVIEW,
            args=args,
            timeout=timeout
        )

    def send_close_env(self, timeout: float = 30.0) -> IPCResponse:
        """
        환경 종료 명령 전송

        Args:
            timeout: 타임아웃 시간

        Returns:
            IPCResponse
        """
        return self.send_command(
            command_type=CommandType.CLOSE_ENV,
            args={},
            timeout=timeout
        )

    def check_env_alive(self) -> bool:
        """
        시뮬레이션 환경이 살아있는지 확인

        env_status.json 파일을 확인하여 판단
        """
        status_file = os.path.join(self.simulation_dir, "env_status.json")
        if not os.path.exists(status_file):
            return False

        try:
            with open(status_file, 'r', encoding='utf-8') as f:
                status = json.load(f)
            return status.get("status") == "alive"
        except (json.JSONDecodeError, OSError):
            return False


class SimulationIPCServer:
    """
    시뮬레이션 IPC 서버 (시뮬레이션 스크립트 측에서 사용)

    명령 디렉토리를 폴링하고, 명령을 실행하여 응답을 반환
    """

    def __init__(self, simulation_dir: str):
        """
        IPC 서버 초기화

        Args:
            simulation_dir: 시뮬레이션 데이터 디렉토리
        """
        self.simulation_dir = simulation_dir
        self.commands_dir = os.path.join(simulation_dir, "ipc_commands")
        self.responses_dir = os.path.join(simulation_dir, "ipc_responses")

        # 디렉토리 존재 확인
        os.makedirs(self.commands_dir, exist_ok=True)
        os.makedirs(self.responses_dir, exist_ok=True)

        # 환경 상태
        self._running = False

    def start(self):
        """서버를 실행 상태로 표시"""
        self._running = True
        self._update_env_status("alive")

    def stop(self):
        """서버를 중지 상태로 표시"""
        self._running = False
        self._update_env_status("stopped")

    def _update_env_status(self, status: str):
        """환경 상태 파일 업데이트"""
        status_file = os.path.join(self.simulation_dir, "env_status.json")
        with open(status_file, 'w', encoding='utf-8') as f:
            json.dump({
                "status": status,
                "timestamp": datetime.now().isoformat()
            }, f, ensure_ascii=False, indent=2)

    def poll_commands(self) -> Optional[IPCCommand]:
        """
        명령 디렉토리를 폴링하고, 첫 번째 대기 중인 명령 반환

        Returns:
            IPCCommand 또는 None
        """
        if not os.path.exists(self.commands_dir):
            return None

        # 시간순으로 정렬하여 명령 파일 가져오기
        command_files = []
        for filename in os.listdir(self.commands_dir):
            if filename.endswith('.json'):
                filepath = os.path.join(self.commands_dir, filename)
                command_files.append((filepath, os.path.getmtime(filepath)))

        command_files.sort(key=lambda x: x[1])

        for filepath, _ in command_files:
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                return IPCCommand.from_dict(data)
            except (json.JSONDecodeError, KeyError, OSError) as e:
                logger.warning(f"명령 파일 읽기 실패: {filepath}, {e}")
                continue

        return None

    def send_response(self, response: IPCResponse):
        """
        응답 전송

        Args:
            response: IPC 응답
        """
        response_file = os.path.join(self.responses_dir, f"{response.command_id}.json")
        with open(response_file, 'w', encoding='utf-8') as f:
            json.dump(response.to_dict(), f, ensure_ascii=False, indent=2)

        # 명령 파일 삭제
        command_file = os.path.join(self.commands_dir, f"{response.command_id}.json")
        try:
            os.remove(command_file)
        except OSError:
            pass

    def send_success(self, command_id: str, result: Dict[str, Any]):
        """성공 응답 전송"""
        self.send_response(IPCResponse(
            command_id=command_id,
            status=CommandStatus.COMPLETED,
            result=result
        ))

    def send_error(self, command_id: str, error: str):
        """오류 응답 전송"""
        self.send_response(IPCResponse(
            command_id=command_id,
            status=CommandStatus.FAILED,
            error=error
        ))
