"""
그래프 구축 서비스
인터페이스2: Zep API를 사용하여 Standalone Graph 구축
"""

import os
import uuid
import time
import threading
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass

from zep_cloud.client import Zep
from zep_cloud import EpisodeData, EntityEdgeSourceTarget

from ..config import Config
from ..models.task import TaskManager, TaskStatus
from ..utils.zep_paging import fetch_all_nodes, fetch_all_edges
from .text_processor import TextProcessor


@dataclass
class GraphInfo:
    """그래프 정보"""
    graph_id: str
    node_count: int
    edge_count: int
    entity_types: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "graph_id": self.graph_id,
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "entity_types": self.entity_types,
        }


class GraphBuilderService:
    """
    그래프 구축 서비스
    Zep API를 호출하여 지식 그래프를 구축하는 역할
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or Config.ZEP_API_KEY
        if not self.api_key:
            raise ValueError("ZEP_API_KEY 가 설정되지 않았습니다")

        self.client = Zep(api_key=self.api_key)
        self.task_manager = TaskManager()

    def build_graph_async(
        self,
        text: str,
        ontology: Dict[str, Any],
        graph_name: str = "MiroFish Graph",
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        batch_size: int = 3
    ) -> str:
        """
        비동기 그래프 구축

        Args:
            text: 입력 텍스트
            ontology: 온톨로지 정의 (인터페이스1의 출력)
            graph_name: 그래프 이름
            chunk_size: 텍스트 청크 크기
            chunk_overlap: 청크 오버랩 크기
            batch_size: 배치당 전송할 청크 수

        Returns:
            작업 ID
        """
        # 작업 생성
        task_id = self.task_manager.create_task(
            task_type="graph_build",
            metadata={
                "graph_name": graph_name,
                "chunk_size": chunk_size,
                "text_length": len(text),
            }
        )

        # 백그라운드 스레드에서 구축 실행
        thread = threading.Thread(
            target=self._build_graph_worker,
            args=(task_id, text, ontology, graph_name, chunk_size, chunk_overlap, batch_size)
        )
        thread.daemon = True
        thread.start()

        return task_id

    def _build_graph_worker(
        self,
        task_id: str,
        text: str,
        ontology: Dict[str, Any],
        graph_name: str,
        chunk_size: int,
        chunk_overlap: int,
        batch_size: int
    ):
        """그래프 구축 워커 스레드"""
        try:
            self.task_manager.update_task(
                task_id,
                status=TaskStatus.PROCESSING,
                progress=5,
                message="그래프 구축 시작 중..."
            )

            # 1. 그래프 생성
            graph_id = self.create_graph(graph_name)
            self.task_manager.update_task(
                task_id,
                progress=10,
                message=f"그래프 생성됨: {graph_id}"
            )

            # 2. 온톨로지 설정
            self.set_ontology(graph_id, ontology)
            self.task_manager.update_task(
                task_id,
                progress=15,
                message="온톨로지 설정 완료"
            )

            # 3. 텍스트 청크 분할
            chunks = TextProcessor.split_text(text, chunk_size, chunk_overlap)
            total_chunks = len(chunks)
            self.task_manager.update_task(
                task_id,
                progress=20,
                message=f"텍스트를 {total_chunks}개 청크로 분할 완료"
            )

            # 4. 배치 데이터 전송
            episode_uuids = self.add_text_batches(
                graph_id, chunks, batch_size,
                lambda msg, prog: self.task_manager.update_task(
                    task_id,
                    progress=20 + int(prog * 0.4),  # 20-60%
                    message=msg
                )
            )

            # 5. Zep 처리 완료 대기
            self.task_manager.update_task(
                task_id,
                progress=60,
                message="Zep 데이터 처리 대기 중..."
            )

            self._wait_for_episodes(
                episode_uuids,
                lambda msg, prog: self.task_manager.update_task(
                    task_id,
                    progress=60 + int(prog * 0.3),  # 60-90%
                    message=msg
                )
            )

            # 6. 그래프 정보 가져오기
            self.task_manager.update_task(
                task_id,
                progress=90,
                message="그래프 정보 가져오는 중..."
            )

            graph_info = self._get_graph_info(graph_id)

            # 완료
            self.task_manager.complete_task(task_id, {
                "graph_id": graph_id,
                "graph_info": graph_info.to_dict(),
                "chunks_processed": total_chunks,
            })

        except Exception as e:
            import traceback
            error_msg = f"{str(e)}\n{traceback.format_exc()}"
            self.task_manager.fail_task(task_id, error_msg)

    def create_graph(self, name: str) -> str:
        """Zep 그래프 생성 (공개 메서드)"""
        graph_id = f"mirofish_{uuid.uuid4().hex[:16]}"

        self.client.graph.create(
            graph_id=graph_id,
            name=name,
            description="MiroFish Social Simulation Graph"
        )

        return graph_id

    def set_ontology(self, graph_id: str, ontology: Dict[str, Any]):
        """그래프 온톨로지 설정 (공개 메서드)"""
        import warnings
        from typing import Optional
        from pydantic import Field
        from zep_cloud.external_clients.ontology import EntityModel, EntityText, EdgeModel

        # Pydantic v2의 Field(default=None)에 대한 경고 억제
        # 이는 Zep SDK에서 요구하는 사용법으로, 동적 클래스 생성에서 발생하는 경고이므로 안전하게 무시할 수 있음
        warnings.filterwarnings('ignore', category=UserWarning, module='pydantic')

        # Zep 예약 이름, 속성 이름으로 사용 불가
        RESERVED_NAMES = {'uuid', 'name', 'group_id', 'name_embedding', 'summary', 'created_at'}

        def safe_attr_name(attr_name: str) -> str:
            """예약 이름을 안전한 이름으로 변환"""
            if attr_name.lower() in RESERVED_NAMES:
                return f"entity_{attr_name}"
            return attr_name

        def to_pascal_case(name: str) -> str:
            """임의의 문자열을 PascalCase로 변환 (영숫자만 허용)"""
            import re
            # 공백·언더스코어·하이픈·점 등을 단어 경계로 분리
            words = re.split(r'[\s_\-\.]+', name.strip())
            pascal = ''.join(w.capitalize() for w in words if w)
            # 영숫자 이외 문자 제거
            pascal = re.sub(r'[^A-Za-z0-9]', '', pascal)
            # 첫 글자가 숫자면 앞에 'T' 추가 (Zep 규칙)
            if pascal and pascal[0].isdigit():
                pascal = 'T' + pascal
            return pascal or 'Entity'

        # 동적으로 엔티티 타입 생성
        entity_types = {}
        for entity_def in ontology.get("entity_types", []):
            name = to_pascal_case(entity_def["name"])
            description = entity_def.get("description", f"A {name} entity.")

            # 속성 딕셔너리 및 타입 어노테이션 생성 (Pydantic v2 필요)
            attrs = {"__doc__": description}
            annotations = {}

            for attr_def in entity_def.get("attributes", []):
                attr_name = safe_attr_name(attr_def["name"])  # 안전한 이름 사용
                attr_desc = attr_def.get("description", attr_name)
                # Zep API는 Field의 description이 필요하며 이는 필수 사항
                attrs[attr_name] = Field(description=attr_desc, default=None)
                annotations[attr_name] = Optional[EntityText]  # 타입 어노테이션

            attrs["__annotations__"] = annotations

            # 동적으로 클래스 생성
            entity_class = type(name, (EntityModel,), attrs)
            entity_class.__doc__ = description
            entity_types[name] = entity_class

        # 동적으로 엣지 타입 생성
        edge_definitions = {}
        for edge_def in ontology.get("edge_types", []):
            name = edge_def["name"]  # 엣지는 키로 원본 사용, 클래스명만 PascalCase 변환
            description = edge_def.get("description", f"A {name} relationship.")

            # 속성 딕셔너리 및 타입 어노테이션 생성
            attrs = {"__doc__": description}
            annotations = {}

            for attr_def in edge_def.get("attributes", []):
                attr_name = safe_attr_name(attr_def["name"])  # 안전한 이름 사용
                attr_desc = attr_def.get("description", attr_name)
                # Zep API는 Field의 description이 필요하며 이는 필수 사항
                attrs[attr_name] = Field(description=attr_desc, default=None)
                annotations[attr_name] = Optional[str]  # 엣지 속성은 str 타입 사용

            attrs["__annotations__"] = annotations

            # 동적으로 클래스 생성
            class_name = ''.join(word.capitalize() for word in name.split('_'))
            edge_class = type(class_name, (EdgeModel,), attrs)
            edge_class.__doc__ = description

            # source_targets 구성
            source_targets = []
            for st in edge_def.get("source_targets", []):
                source_targets.append(
                    EntityEdgeSourceTarget(
                        source=to_pascal_case(st.get("source", "Entity")),
                        target=to_pascal_case(st.get("target", "Entity"))
                    )
                )

            if source_targets:
                edge_definitions[name] = (edge_class, source_targets)

        # Zep API를 호출하여 온톨로지 설정
        if entity_types or edge_definitions:
            self.client.graph.set_ontology(
                graph_ids=[graph_id],
                entities=entity_types if entity_types else None,
                edges=edge_definitions if edge_definitions else None,
            )

    def add_text_batches(
        self,
        graph_id: str,
        chunks: List[str],
        batch_size: int = 3,
        progress_callback: Optional[Callable] = None
    ) -> List[str]:
        """텍스트를 배치로 그래프에 추가, 모든 episode의 uuid 목록 반환"""
        episode_uuids = []
        total_chunks = len(chunks)

        for i in range(0, total_chunks, batch_size):
            batch_chunks = chunks[i:i + batch_size]
            batch_num = i // batch_size + 1
            total_batches = (total_chunks + batch_size - 1) // batch_size

            if progress_callback:
                progress = (i + len(batch_chunks)) / total_chunks
                progress_callback(
                    f"{batch_num}/{total_batches} 번째 배치 전송 중 ({len(batch_chunks)}개 청크)...",
                    progress
                )

            # episode 데이터 구성
            episodes = [
                EpisodeData(data=chunk, type="text")
                for chunk in batch_chunks
            ]

            # Zep에 전송
            try:
                batch_result = self.client.graph.add_batch(
                    graph_id=graph_id,
                    episodes=episodes
                )

                # 반환된 episode uuid 수집
                if batch_result and isinstance(batch_result, list):
                    for ep in batch_result:
                        ep_uuid = getattr(ep, 'uuid_', None) or getattr(ep, 'uuid', None)
                        if ep_uuid:
                            episode_uuids.append(ep_uuid)

                # 요청이 너무 빠르지 않도록 대기
                time.sleep(1)

            except Exception as e:
                if progress_callback:
                    progress_callback(f"배치 {batch_num} 전송 실패: {str(e)}", 0)
                raise

        return episode_uuids

    def _wait_for_episodes(
        self,
        episode_uuids: List[str],
        progress_callback: Optional[Callable] = None,
        timeout: int = 600
    ):
        """모든 episode 처리 완료 대기 (각 episode의 processed 상태 조회)"""
        if not episode_uuids:
            if progress_callback:
                progress_callback("대기 불필요 (episode 없음)", 1.0)
            return

        start_time = time.time()
        pending_episodes = set(episode_uuids)
        completed_count = 0
        total_episodes = len(episode_uuids)

        if progress_callback:
            progress_callback(f"{total_episodes}개 텍스트 청크 처리 대기 시작...", 0)

        while pending_episodes:
            if time.time() - start_time > timeout:
                if progress_callback:
                    progress_callback(
                        f"일부 텍스트 청크 타임아웃, 완료 {completed_count}/{total_episodes}",
                        completed_count / total_episodes
                    )
                break

            # 각 episode의 처리 상태 확인
            for ep_uuid in list(pending_episodes):
                try:
                    episode = self.client.graph.episode.get(uuid_=ep_uuid)
                    is_processed = getattr(episode, 'processed', False)

                    if is_processed:
                        pending_episodes.remove(ep_uuid)
                        completed_count += 1

                except Exception as e:
                    # 개별 조회 오류 무시, 계속 진행
                    pass

            elapsed = int(time.time() - start_time)
            if progress_callback:
                progress_callback(
                    f"Zep 처리 중... {completed_count}/{total_episodes} 완료, {len(pending_episodes)} 대기 중 ({elapsed}초)",
                    completed_count / total_episodes if total_episodes > 0 else 0
                )

            if pending_episodes:
                time.sleep(3)  # 3초마다 확인

        if progress_callback:
            progress_callback(f"처리 완료: {completed_count}/{total_episodes}", 1.0)

    def _get_graph_info(self, graph_id: str) -> GraphInfo:
        """그래프 정보 가져오기"""
        # 노드 가져오기 (페이지네이션)
        nodes = fetch_all_nodes(self.client, graph_id)

        # 엣지 가져오기 (페이지네이션)
        edges = fetch_all_edges(self.client, graph_id)

        # 엔티티 타입 집계
        entity_types = set()
        for node in nodes:
            if node.labels:
                for label in node.labels:
                    if label not in ["Entity", "Node"]:
                        entity_types.add(label)

        return GraphInfo(
            graph_id=graph_id,
            node_count=len(nodes),
            edge_count=len(edges),
            entity_types=list(entity_types)
        )

    def get_graph_data(self, graph_id: str) -> Dict[str, Any]:
        """
        전체 그래프 데이터 가져오기 (상세 정보 포함)

        Args:
            graph_id: 그래프 ID

        Returns:
            nodes와 edges를 포함하는 딕셔너리, 시간 정보, 속성 등 상세 데이터 포함
        """
        nodes = fetch_all_nodes(self.client, graph_id)
        edges = fetch_all_edges(self.client, graph_id)

        # 노드 이름을 가져오기 위한 노드 맵 생성
        node_map = {}
        for node in nodes:
            node_map[node.uuid_] = node.name or ""

        nodes_data = []
        for node in nodes:
            # 생성 시간 가져오기
            created_at = getattr(node, 'created_at', None)
            if created_at:
                created_at = str(created_at)

            nodes_data.append({
                "uuid": node.uuid_,
                "name": node.name,
                "labels": node.labels or [],
                "summary": node.summary or "",
                "attributes": node.attributes or {},
                "created_at": created_at,
            })

        edges_data = []
        for edge in edges:
            # 시간 정보 가져오기
            created_at = getattr(edge, 'created_at', None)
            valid_at = getattr(edge, 'valid_at', None)
            invalid_at = getattr(edge, 'invalid_at', None)
            expired_at = getattr(edge, 'expired_at', None)

            # episodes 가져오기
            episodes = getattr(edge, 'episodes', None) or getattr(edge, 'episode_ids', None)
            if episodes and not isinstance(episodes, list):
                episodes = [str(episodes)]
            elif episodes:
                episodes = [str(e) for e in episodes]

            # fact_type 가져오기
            fact_type = getattr(edge, 'fact_type', None) or edge.name or ""

            edges_data.append({
                "uuid": edge.uuid_,
                "name": edge.name or "",
                "fact": edge.fact or "",
                "fact_type": fact_type,
                "source_node_uuid": edge.source_node_uuid,
                "target_node_uuid": edge.target_node_uuid,
                "source_node_name": node_map.get(edge.source_node_uuid, ""),
                "target_node_name": node_map.get(edge.target_node_uuid, ""),
                "attributes": edge.attributes or {},
                "created_at": str(created_at) if created_at else None,
                "valid_at": str(valid_at) if valid_at else None,
                "invalid_at": str(invalid_at) if invalid_at else None,
                "expired_at": str(expired_at) if expired_at else None,
                "episodes": episodes or [],
            })

        return {
            "graph_id": graph_id,
            "nodes": nodes_data,
            "edges": edges_data,
            "node_count": len(nodes_data),
            "edge_count": len(edges_data),
        }

    def delete_graph(self, graph_id: str):
        """그래프 삭제"""
        self.client.graph.delete(graph_id=graph_id)
