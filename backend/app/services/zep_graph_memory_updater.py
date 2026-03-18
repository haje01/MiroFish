"""
Zep 그래프 메모리 업데이트 서비스
시뮬레이션의 Agent 활동을 Zep 그래프에 동적으로 업데이트
"""

import os
import time
import threading
import json
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass
from datetime import datetime
from queue import Queue, Empty

from zep_cloud.client import Zep

from ..config import Config
from ..utils.logger import get_logger

logger = get_logger('mirofish.zep_graph_memory_updater')


@dataclass
class AgentActivity:
    """Agent 활동 기록"""
    platform: str           # twitter / reddit
    agent_id: int
    agent_name: str
    action_type: str        # CREATE_POST, LIKE_POST, etc.
    action_args: Dict[str, Any]
    round_num: int
    timestamp: str

    def to_episode_text(self) -> str:
        """
        활동을 Zep에 전송 가능한 텍스트 설명으로 변환

        자연어 설명 형식을 채택하여 Zep이 엔티티와 관계를 추출할 수 있도록 함
        시뮬레이션 관련 접두사를 추가하지 않아 그래프 업데이트 오류 유도 방지
        """
        # 동작 유형별로 다른 설명 생성
        action_descriptions = {
            "CREATE_POST": self._describe_create_post,
            "LIKE_POST": self._describe_like_post,
            "DISLIKE_POST": self._describe_dislike_post,
            "REPOST": self._describe_repost,
            "QUOTE_POST": self._describe_quote_post,
            "FOLLOW": self._describe_follow,
            "CREATE_COMMENT": self._describe_create_comment,
            "LIKE_COMMENT": self._describe_like_comment,
            "DISLIKE_COMMENT": self._describe_dislike_comment,
            "SEARCH_POSTS": self._describe_search,
            "SEARCH_USER": self._describe_search_user,
            "MUTE": self._describe_mute,
        }
        
        describe_func = action_descriptions.get(self.action_type, self._describe_generic)
        description = describe_func()

        # "agent명: 활동 설명" 형식으로 직접 반환, 시뮬레이션 접두사 추가 안 함
        return f"{self.agent_name}: {description}"
    
    def _describe_create_post(self) -> str:
        content = self.action_args.get("content", "")
        if content:
            return f"게시물을 작성했습니다: 「{content}」"
        return "게시물을 작성했습니다"

    def _describe_like_post(self) -> str:
        """게시물 좋아요 - 게시물 원문과 작성자 정보 포함"""
        post_content = self.action_args.get("post_content", "")
        post_author = self.action_args.get("post_author_name", "")

        if post_content and post_author:
            return f"{post_author}의 게시물에 좋아요를 눌렀습니다: 「{post_content}」"
        elif post_content:
            return f"게시물에 좋아요를 눌렀습니다: 「{post_content}」"
        elif post_author:
            return f"{post_author}의 게시물에 좋아요를 눌렀습니다"
        return "게시물에 좋아요를 눌렀습니다"

    def _describe_dislike_post(self) -> str:
        """게시물 싫어요 - 게시물 원문과 작성자 정보 포함"""
        post_content = self.action_args.get("post_content", "")
        post_author = self.action_args.get("post_author_name", "")

        if post_content and post_author:
            return f"{post_author}의 게시물에 싫어요를 눌렀습니다: 「{post_content}」"
        elif post_content:
            return f"게시물에 싫어요를 눌렀습니다: 「{post_content}」"
        elif post_author:
            return f"{post_author}의 게시물에 싫어요를 눌렀습니다"
        return "게시물에 싫어요를 눌렀습니다"

    def _describe_repost(self) -> str:
        """게시물 리포스트 - 원문 내용과 작성자 정보 포함"""
        original_content = self.action_args.get("original_content", "")
        original_author = self.action_args.get("original_author_name", "")

        if original_content and original_author:
            return f"{original_author}의 게시물을 리포스트했습니다: 「{original_content}」"
        elif original_content:
            return f"게시물을 리포스트했습니다: 「{original_content}」"
        elif original_author:
            return f"{original_author}의 게시물을 리포스트했습니다"
        return "게시물을 리포스트했습니다"

    def _describe_quote_post(self) -> str:
        """게시물 인용 - 원문 내용, 작성자 정보 및 인용 댓글 포함"""
        original_content = self.action_args.get("original_content", "")
        original_author = self.action_args.get("original_author_name", "")
        quote_content = self.action_args.get("quote_content", "") or self.action_args.get("content", "")

        base = ""
        if original_content and original_author:
            base = f"{original_author}의 게시물 「{original_content}」을(를) 인용했습니다"
        elif original_content:
            base = f"게시물 「{original_content}」을(를) 인용했습니다"
        elif original_author:
            base = f"{original_author}의 게시물을 인용했습니다"
        else:
            base = "게시물을 인용했습니다"

        if quote_content:
            base += f", 댓글을 남겼습니다: 「{quote_content}」"
        return base

    def _describe_follow(self) -> str:
        """사용자 팔로우 - 팔로우된 사용자 이름 포함"""
        target_user_name = self.action_args.get("target_user_name", "")

        if target_user_name:
            return f"사용자 「{target_user_name}」을(를) 팔로우했습니다"
        return "사용자를 팔로우했습니다"

    def _describe_create_comment(self) -> str:
        """댓글 작성 - 댓글 내용과 해당 게시물 정보 포함"""
        content = self.action_args.get("content", "")
        post_content = self.action_args.get("post_content", "")
        post_author = self.action_args.get("post_author_name", "")

        if content:
            if post_content and post_author:
                return f"{post_author}의 게시물 「{post_content}」에 댓글을 작성했습니다: 「{content}」"
            elif post_content:
                return f"게시물 「{post_content}」에 댓글을 작성했습니다: 「{content}」"
            elif post_author:
                return f"{post_author}의 게시물에 댓글을 작성했습니다: 「{content}」"
            return f"댓글을 작성했습니다: 「{content}」"
        return "댓글을 작성했습니다"

    def _describe_like_comment(self) -> str:
        """댓글 좋아요 - 댓글 내용과 작성자 정보 포함"""
        comment_content = self.action_args.get("comment_content", "")
        comment_author = self.action_args.get("comment_author_name", "")

        if comment_content and comment_author:
            return f"{comment_author}의 댓글에 좋아요를 눌렀습니다: 「{comment_content}」"
        elif comment_content:
            return f"댓글에 좋아요를 눌렀습니다: 「{comment_content}」"
        elif comment_author:
            return f"{comment_author}의 댓글에 좋아요를 눌렀습니다"
        return "댓글에 좋아요를 눌렀습니다"

    def _describe_dislike_comment(self) -> str:
        """댓글 싫어요 - 댓글 내용과 작성자 정보 포함"""
        comment_content = self.action_args.get("comment_content", "")
        comment_author = self.action_args.get("comment_author_name", "")

        if comment_content and comment_author:
            return f"{comment_author}의 댓글에 싫어요를 눌렀습니다: 「{comment_content}」"
        elif comment_content:
            return f"댓글에 싫어요를 눌렀습니다: 「{comment_content}」"
        elif comment_author:
            return f"{comment_author}의 댓글에 싫어요를 눌렀습니다"
        return "댓글에 싫어요를 눌렀습니다"

    def _describe_search(self) -> str:
        """게시물 검색 - 검색 키워드 포함"""
        query = self.action_args.get("query", "") or self.action_args.get("keyword", "")
        return f"「{query}」을(를) 검색했습니다" if query else "검색을 수행했습니다"

    def _describe_search_user(self) -> str:
        """사용자 검색 - 검색 키워드 포함"""
        query = self.action_args.get("query", "") or self.action_args.get("username", "")
        return f"사용자 「{query}」을(를) 검색했습니다" if query else "사용자를 검색했습니다"

    def _describe_mute(self) -> str:
        """사용자 차단 - 차단된 사용자 이름 포함"""
        target_user_name = self.action_args.get("target_user_name", "")

        if target_user_name:
            return f"사용자 「{target_user_name}」을(를) 차단했습니다"
        return "사용자를 차단했습니다"

    def _describe_generic(self) -> str:
        # 알 수 없는 동작 유형에 대한 일반 설명 생성
        return f"{self.action_type} 작업을 수행했습니다"


class ZepGraphMemoryUpdater:
    """
    Zep 그래프 메모리 업데이터

    시뮬레이션의 actions 로그 파일을 모니터링하며, 새로운 에이전트 활동을 Zep 그래프에 실시간으로 업데이트합니다.
    플랫폼별로 그룹화하여, BATCH_SIZE개의 활동이 누적되면 Zep에 일괄 전송합니다.

    의미 있는 모든 행동이 Zep에 업데이트되며, action_args에 전체 컨텍스트 정보가 포함됩니다:
    - 좋아요/싫어요한 게시물 원문
    - 리포스트/인용한 게시물 원문
    - 팔로우/차단한 사용자 이름
    - 좋아요/싫어요한 댓글 원문
    """

    # 일괄 전송 크기 (플랫폼별로 몇 개가 누적되면 전송할지)
    BATCH_SIZE = 5

    # 플랫폼 이름 매핑 (콘솔 표시용)
    PLATFORM_DISPLAY_NAMES = {
        'twitter': '세계1',
        'reddit': '세계2',
    }

    # 전송 간격 (초), 요청이 너무 빨라지지 않도록
    SEND_INTERVAL = 0.5

    # 재시도 설정
    MAX_RETRIES = 3
    RETRY_DELAY = 2  # 초

    def __init__(self, graph_id: str, api_key: Optional[str] = None):
        """
        업데이터 초기화

        Args:
            graph_id: Zep 그래프 ID
            api_key: Zep API Key (선택 사항, 기본값은 설정에서 읽음)
        """
        self.graph_id = graph_id
        self.api_key = api_key or Config.ZEP_API_KEY

        if not self.api_key:
            raise ValueError("ZEP_API_KEY가 설정되지 않았습니다")

        self.client = Zep(api_key=self.api_key)

        # 활동 큐
        self._activity_queue: Queue = Queue()

        # 플랫폼별 활동 버퍼 (각 플랫폼이 BATCH_SIZE까지 누적되면 일괄 전송)
        self._platform_buffers: Dict[str, List[AgentActivity]] = {
            'twitter': [],
            'reddit': [],
        }
        self._buffer_lock = threading.Lock()

        # 제어 플래그
        self._running = False
        self._worker_thread: Optional[threading.Thread] = None

        # 통계
        self._total_activities = 0  # 실제로 큐에 추가된 활동 수
        self._total_sent = 0        # Zep에 성공적으로 전송된 배치 수
        self._total_items_sent = 0  # Zep에 성공적으로 전송된 활동 항목 수
        self._failed_count = 0      # 전송 실패한 배치 수
        self._skipped_count = 0     # 필터링되어 건너뛴 활동 수 (DO_NOTHING)

        logger.info(f"ZepGraphMemoryUpdater 초기화 완료: graph_id={graph_id}, batch_size={self.BATCH_SIZE}")
    
    def _get_platform_display_name(self, platform: str) -> str:
        """플랫폼의 표시 이름 반환"""
        return self.PLATFORM_DISPLAY_NAMES.get(platform.lower(), platform)

    def start(self):
        """백그라운드 워커 스레드 시작"""
        if self._running:
            return

        self._running = True
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            daemon=True,
            name=f"ZepMemoryUpdater-{self.graph_id[:8]}"
        )
        self._worker_thread.start()
        logger.info(f"ZepGraphMemoryUpdater 시작됨: graph_id={self.graph_id}")

    def stop(self):
        """백그라운드 워커 스레드 중지"""
        self._running = False

        # 남은 활동 전송
        self._flush_remaining()

        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=10)

        logger.info(f"ZepGraphMemoryUpdater 중지됨: graph_id={self.graph_id}, "
                   f"total_activities={self._total_activities}, "
                   f"batches_sent={self._total_sent}, "
                   f"items_sent={self._total_items_sent}, "
                   f"failed={self._failed_count}, "
                   f"skipped={self._skipped_count}")
    
    def add_activity(self, activity: AgentActivity):
        """
        에이전트 활동을 큐에 추가

        의미 있는 모든 행동이 큐에 추가됩니다:
        - CREATE_POST (게시물 작성)
        - CREATE_COMMENT (댓글 작성)
        - QUOTE_POST (게시물 인용)
        - SEARCH_POSTS (게시물 검색)
        - SEARCH_USER (사용자 검색)
        - LIKE_POST/DISLIKE_POST (게시물 좋아요/싫어요)
        - REPOST (리포스트)
        - FOLLOW (팔로우)
        - MUTE (차단)
        - LIKE_COMMENT/DISLIKE_COMMENT (댓글 좋아요/싫어요)

        action_args에는 전체 컨텍스트 정보(예: 게시물 원문, 사용자 이름 등)가 포함됩니다.

        Args:
            activity: 에이전트 활동 기록
        """
        # DO_NOTHING 유형의 활동은 건너뜀
        if activity.action_type == "DO_NOTHING":
            self._skipped_count += 1
            return

        self._activity_queue.put(activity)
        self._total_activities += 1
        logger.debug(f"Zep 큐에 활동 추가: {activity.agent_name} - {activity.action_type}")
    
    def add_activity_from_dict(self, data: Dict[str, Any], platform: str):
        """
        딕셔너리 데이터로부터 활동 추가

        Args:
            data: actions.jsonl에서 파싱된 딕셔너리 데이터
            platform: 플랫폼 이름 (twitter/reddit)
        """
        # 이벤트 유형의 항목은 건너뜀
        if "event_type" in data:
            return
        
        activity = AgentActivity(
            platform=platform,
            agent_id=data.get("agent_id", 0),
            agent_name=data.get("agent_name", ""),
            action_type=data.get("action_type", ""),
            action_args=data.get("action_args", {}),
            round_num=data.get("round", 0),
            timestamp=data.get("timestamp", datetime.now().isoformat()),
        )
        
        self.add_activity(activity)
    
    def _worker_loop(self):
        """백그라운드 워커 루프 - 플랫폼별로 활동을 Zep에 일괄 전송"""
        while self._running or not self._activity_queue.empty():
            try:
                # 큐에서 활동 가져오기 시도 (1초 타임아웃)
                try:
                    activity = self._activity_queue.get(timeout=1)

                    # 해당 플랫폼의 버퍼에 활동 추가
                    platform = activity.platform.lower()
                    with self._buffer_lock:
                        if platform not in self._platform_buffers:
                            self._platform_buffers[platform] = []
                        self._platform_buffers[platform].append(activity)

                        # 해당 플랫폼이 배치 크기에 도달했는지 확인
                        if len(self._platform_buffers[platform]) >= self.BATCH_SIZE:
                            batch = self._platform_buffers[platform][:self.BATCH_SIZE]
                            self._platform_buffers[platform] = self._platform_buffers[platform][self.BATCH_SIZE:]
                            # 락 해제 후 전송
                            self._send_batch_activities(batch, platform)
                            # 전송 간격, 요청이 너무 빨라지지 않도록
                            time.sleep(self.SEND_INTERVAL)

                except Empty:
                    pass

            except Exception as e:
                logger.error(f"워커 루프 예외 발생: {e}")
                time.sleep(1)
    
    def _send_batch_activities(self, activities: List[AgentActivity], platform: str):
        """
        Zep 그래프에 활동을 일괄 전송 (하나의 텍스트로 합쳐서)

        Args:
            activities: 에이전트 활동 목록
            platform: 플랫폼 이름
        """
        if not activities:
            return

        # 여러 활동을 줄바꿈으로 구분된 하나의 텍스트로 합치기
        episode_texts = [activity.to_episode_text() for activity in activities]
        combined_text = "\n".join(episode_texts)

        # 재시도와 함께 전송
        for attempt in range(self.MAX_RETRIES):
            try:
                self.client.graph.add(
                    graph_id=self.graph_id,
                    type="text",
                    data=combined_text
                )

                self._total_sent += 1
                self._total_items_sent += len(activities)
                display_name = self._get_platform_display_name(platform)
                logger.info(f"{display_name} 활동 {len(activities)}개를 그래프 {self.graph_id}에 일괄 전송 성공")
                logger.debug(f"일괄 내용 미리보기: {combined_text[:200]}...")
                return

            except Exception as e:
                if attempt < self.MAX_RETRIES - 1:
                    logger.warning(f"Zep에 일괄 전송 실패 (시도 {attempt + 1}/{self.MAX_RETRIES}): {e}")
                    time.sleep(self.RETRY_DELAY * (attempt + 1))
                else:
                    logger.error(f"Zep에 일괄 전송 실패, {self.MAX_RETRIES}회 재시도 완료: {e}")
                    self._failed_count += 1
    
    def _flush_remaining(self):
        """큐와 버퍼에 남은 활동 전송"""
        # 먼저 큐에 남은 활동을 버퍼에 추가
        while not self._activity_queue.empty():
            try:
                activity = self._activity_queue.get_nowait()
                platform = activity.platform.lower()
                with self._buffer_lock:
                    if platform not in self._platform_buffers:
                        self._platform_buffers[platform] = []
                    self._platform_buffers[platform].append(activity)
            except Empty:
                break

        # 그런 다음 각 플랫폼 버퍼의 남은 활동 전송 (BATCH_SIZE에 미치지 않아도)
        with self._buffer_lock:
            for platform, buffer in self._platform_buffers.items():
                if buffer:
                    display_name = self._get_platform_display_name(platform)
                    logger.info(f"{display_name} 플랫폼의 남은 {len(buffer)}개 활동 전송")
                    self._send_batch_activities(buffer, platform)
            # 모든 버퍼 초기화
            for platform in self._platform_buffers:
                self._platform_buffers[platform] = []
    
    def get_stats(self) -> Dict[str, Any]:
        """통계 정보 반환"""
        with self._buffer_lock:
            buffer_sizes = {p: len(b) for p, b in self._platform_buffers.items()}

        return {
            "graph_id": self.graph_id,
            "batch_size": self.BATCH_SIZE,
            "total_activities": self._total_activities,  # 큐에 추가된 총 활동 수
            "batches_sent": self._total_sent,            # 성공적으로 전송된 배치 수
            "items_sent": self._total_items_sent,        # 성공적으로 전송된 활동 항목 수
            "failed_count": self._failed_count,          # 전송 실패한 배치 수
            "skipped_count": self._skipped_count,        # 필터링되어 건너뛴 활동 수 (DO_NOTHING)
            "queue_size": self._activity_queue.qsize(),
            "buffer_sizes": buffer_sizes,                # 각 플랫폼 버퍼 크기
            "running": self._running,
        }


class ZepGraphMemoryManager:
    """
    여러 시뮬레이션의 Zep 그래프 메모리 업데이터 관리

    각 시뮬레이션은 자체 업데이터 인스턴스를 가질 수 있습니다.
    """

    _updaters: Dict[str, ZepGraphMemoryUpdater] = {}
    _lock = threading.Lock()

    @classmethod
    def create_updater(cls, simulation_id: str, graph_id: str) -> ZepGraphMemoryUpdater:
        """
        시뮬레이션을 위한 그래프 메모리 업데이터 생성

        Args:
            simulation_id: 시뮬레이션 ID
            graph_id: Zep 그래프 ID

        Returns:
            ZepGraphMemoryUpdater 인스턴스
        """
        with cls._lock:
            # 이미 존재하는 경우 기존 것을 먼저 중지
            if simulation_id in cls._updaters:
                cls._updaters[simulation_id].stop()

            updater = ZepGraphMemoryUpdater(graph_id)
            updater.start()
            cls._updaters[simulation_id] = updater

            logger.info(f"그래프 메모리 업데이터 생성: simulation_id={simulation_id}, graph_id={graph_id}")
            return updater

    @classmethod
    def get_updater(cls, simulation_id: str) -> Optional[ZepGraphMemoryUpdater]:
        """시뮬레이션의 업데이터 반환"""
        return cls._updaters.get(simulation_id)

    @classmethod
    def stop_updater(cls, simulation_id: str):
        """시뮬레이션의 업데이터 중지 및 제거"""
        with cls._lock:
            if simulation_id in cls._updaters:
                cls._updaters[simulation_id].stop()
                del cls._updaters[simulation_id]
                logger.info(f"그래프 메모리 업데이터 중지됨: simulation_id={simulation_id}")

    # stop_all 중복 호출 방지 플래그
    _stop_all_done = False

    @classmethod
    def stop_all(cls):
        """모든 업데이터 중지"""
        # 중복 호출 방지
        if cls._stop_all_done:
            return
        cls._stop_all_done = True

        with cls._lock:
            if cls._updaters:
                for simulation_id, updater in list(cls._updaters.items()):
                    try:
                        updater.stop()
                    except Exception as e:
                        logger.error(f"업데이터 중지 실패: simulation_id={simulation_id}, error={e}")
                cls._updaters.clear()
            logger.info("모든 그래프 메모리 업데이터 중지됨")

    @classmethod
    def get_all_stats(cls) -> Dict[str, Dict[str, Any]]:
        """모든 업데이터의 통계 정보 반환"""
        return {
            sim_id: updater.get_stats()
            for sim_id, updater in cls._updaters.items()
        }
