"""
Zep 엔티티 읽기 및 필터링 서비스
Zep 그래프에서 노드를 읽고, 사전 정의된 엔티티 타입에 맞는 노드를 필터링
"""

import time
from typing import Dict, Any, List, Optional, Set, Callable, TypeVar
from dataclasses import dataclass, field

from zep_cloud.client import Zep

from ..config import Config
from ..utils.logger import get_logger
from ..utils.zep_paging import fetch_all_nodes, fetch_all_edges

logger = get_logger('mirofish.zep_entity_reader')

# 제네릭 반환 타입용
T = TypeVar('T')


@dataclass
class EntityNode:
    """엔티티 노드 데이터 구조"""
    uuid: str
    name: str
    labels: List[str]
    summary: str
    attributes: Dict[str, Any]
    # 관련 엣지 정보
    related_edges: List[Dict[str, Any]] = field(default_factory=list)
    # 관련 다른 노드 정보
    related_nodes: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "uuid": self.uuid,
            "name": self.name,
            "labels": self.labels,
            "summary": self.summary,
            "attributes": self.attributes,
            "related_edges": self.related_edges,
            "related_nodes": self.related_nodes,
        }

    def get_entity_type(self) -> Optional[str]:
        """엔티티 타입 가져오기 (기본 Entity 라벨 제외)"""
        for label in self.labels:
            if label not in ["Entity", "Node"]:
                return label
        return None


@dataclass
class FilteredEntities:
    """필터링된 엔티티 집합"""
    entities: List[EntityNode]
    entity_types: Set[str]
    total_count: int
    filtered_count: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entities": [e.to_dict() for e in self.entities],
            "entity_types": list(self.entity_types),
            "total_count": self.total_count,
            "filtered_count": self.filtered_count,
        }


class ZepEntityReader:
    """
    Zep 엔티티 읽기 및 필터링 서비스

    주요 기능:
    1. Zep 그래프에서 모든 노드 읽기
    2. 사전 정의된 엔티티 타입에 맞는 노드 필터링 (Labels가 Entity만이 아닌 노드)
    3. 각 엔티티의 관련 엣지 및 연결 노드 정보 가져오기
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or Config.ZEP_API_KEY
        if not self.api_key:
            raise ValueError("ZEP_API_KEY 가 설정되지 않았습니다")

        self.client = Zep(api_key=self.api_key)

    def _call_with_retry(
        self,
        func: Callable[[], T],
        operation_name: str,
        max_retries: int = 3,
        initial_delay: float = 2.0
    ) -> T:
        """
        재시도 메커니즘이 있는 Zep API 호출

        Args:
            func: 실행할 함수 (파라미터 없는 lambda 또는 callable)
            operation_name: 작업 이름, 로깅용
            max_retries: 최대 재시도 횟수 (기본 3회, 즉 최대 3회 시도)
            initial_delay: 초기 지연 시간(초)

        Returns:
            API 호출 결과
        """
        last_exception = None
        delay = initial_delay

        for attempt in range(max_retries):
            try:
                return func()
            except Exception as e:
                last_exception = e
                if attempt < max_retries - 1:
                    logger.warning(
                        f"Zep {operation_name} {attempt + 1}번째 시도 실패: {str(e)[:100]}, "
                        f"{delay:.1f}초 후 재시도..."
                    )
                    time.sleep(delay)
                    delay *= 2  # 지수 백오프
                else:
                    logger.error(f"Zep {operation_name} {max_retries}번 시도 후 실패: {str(e)}")

        raise last_exception

    def get_all_nodes(self, graph_id: str) -> List[Dict[str, Any]]:
        """
        그래프의 모든 노드 가져오기 (페이지네이션)

        Args:
            graph_id: 그래프 ID

        Returns:
            노드 목록
        """
        logger.info(f"그래프 {graph_id}의 모든 노드 가져오는 중...")

        nodes = fetch_all_nodes(self.client, graph_id)

        nodes_data = []
        for node in nodes:
            nodes_data.append({
                "uuid": getattr(node, 'uuid_', None) or getattr(node, 'uuid', ''),
                "name": node.name or "",
                "labels": node.labels or [],
                "summary": node.summary or "",
                "attributes": node.attributes or {},
            })

        logger.info(f"총 {len(nodes_data)}개 노드 가져옴")
        return nodes_data

    def get_all_edges(self, graph_id: str) -> List[Dict[str, Any]]:
        """
        그래프의 모든 엣지 가져오기 (페이지네이션)

        Args:
            graph_id: 그래프 ID

        Returns:
            엣지 목록
        """
        logger.info(f"그래프 {graph_id}의 모든 엣지 가져오는 중...")

        edges = fetch_all_edges(self.client, graph_id)

        edges_data = []
        for edge in edges:
            edges_data.append({
                "uuid": getattr(edge, 'uuid_', None) or getattr(edge, 'uuid', ''),
                "name": edge.name or "",
                "fact": edge.fact or "",
                "source_node_uuid": edge.source_node_uuid,
                "target_node_uuid": edge.target_node_uuid,
                "attributes": edge.attributes or {},
            })

        logger.info(f"총 {len(edges_data)}개 엣지 가져옴")
        return edges_data

    def get_node_edges(self, node_uuid: str) -> List[Dict[str, Any]]:
        """
        지정 노드의 모든 관련 엣지 가져오기 (재시도 메커니즘 포함)

        Args:
            node_uuid: 노드 UUID

        Returns:
            엣지 목록
        """
        try:
            # 재시도 메커니즘으로 Zep API 호출
            edges = self._call_with_retry(
                func=lambda: self.client.graph.node.get_entity_edges(node_uuid=node_uuid),
                operation_name=f"노드 엣지 가져오기(node={node_uuid[:8]}...)"
            )

            edges_data = []
            for edge in edges:
                edges_data.append({
                    "uuid": getattr(edge, 'uuid_', None) or getattr(edge, 'uuid', ''),
                    "name": edge.name or "",
                    "fact": edge.fact or "",
                    "source_node_uuid": edge.source_node_uuid,
                    "target_node_uuid": edge.target_node_uuid,
                    "attributes": edge.attributes or {},
                })

            return edges_data
        except Exception as e:
            logger.warning(f"노드 {node_uuid}의 엣지 가져오기 실패: {str(e)}")
            return []

    def filter_defined_entities(
        self,
        graph_id: str,
        defined_entity_types: Optional[List[str]] = None,
        enrich_with_edges: bool = True
    ) -> FilteredEntities:
        """
        사전 정의된 엔티티 타입에 맞는 노드 필터링

        필터링 로직:
        - 노드의 Labels가 "Entity" 하나뿐이라면 사전 정의된 타입에 맞지 않으므로 건너뜀
        - 노드의 Labels에 "Entity"와 "Node" 외의 라벨이 포함되면 사전 정의된 타입에 맞으므로 유지

        Args:
            graph_id: 그래프 ID
            defined_entity_types: 사전 정의된 엔티티 타입 목록 (선택, 제공 시 해당 타입만 유지)
            enrich_with_edges: 각 엔티티의 관련 엣지 정보 가져올지 여부

        Returns:
            FilteredEntities: 필터링된 엔티티 집합
        """
        logger.info(f"그래프 {graph_id}의 엔티티 필터링 시작...")

        # 모든 노드 가져오기
        all_nodes = self.get_all_nodes(graph_id)
        total_count = len(all_nodes)

        # 모든 엣지 가져오기 (이후 연결 검색용)
        all_edges = self.get_all_edges(graph_id) if enrich_with_edges else []

        # 노드 UUID에서 노드 데이터로의 매핑 구성
        node_map = {n["uuid"]: n for n in all_nodes}

        # 조건에 맞는 엔티티 필터링
        filtered_entities = []
        entity_types_found = set()

        for node in all_nodes:
            labels = node.get("labels", [])

            # 필터링 로직: Labels에 "Entity"와 "Node" 외의 라벨이 포함되어야 함
            custom_labels = [l for l in labels if l not in ["Entity", "Node"]]

            if not custom_labels:
                # 기본 라벨만 있음, 건너뜀
                continue

            # 사전 정의된 타입이 지정된 경우 매칭 여부 확인
            if defined_entity_types:
                matching_labels = [l for l in custom_labels if l in defined_entity_types]
                if not matching_labels:
                    continue
                entity_type = matching_labels[0]
            else:
                entity_type = custom_labels[0]

            entity_types_found.add(entity_type)

            # 엔티티 노드 객체 생성
            entity = EntityNode(
                uuid=node["uuid"],
                name=node["name"],
                labels=labels,
                summary=node["summary"],
                attributes=node["attributes"],
            )

            # 관련 엣지 및 노드 가져오기
            if enrich_with_edges:
                related_edges = []
                related_node_uuids = set()

                for edge in all_edges:
                    if edge["source_node_uuid"] == node["uuid"]:
                        related_edges.append({
                            "direction": "outgoing",
                            "edge_name": edge["name"],
                            "fact": edge["fact"],
                            "target_node_uuid": edge["target_node_uuid"],
                        })
                        related_node_uuids.add(edge["target_node_uuid"])
                    elif edge["target_node_uuid"] == node["uuid"]:
                        related_edges.append({
                            "direction": "incoming",
                            "edge_name": edge["name"],
                            "fact": edge["fact"],
                            "source_node_uuid": edge["source_node_uuid"],
                        })
                        related_node_uuids.add(edge["source_node_uuid"])

                entity.related_edges = related_edges

                # 연결 노드의 기본 정보 가져오기
                related_nodes = []
                for related_uuid in related_node_uuids:
                    if related_uuid in node_map:
                        related_node = node_map[related_uuid]
                        related_nodes.append({
                            "uuid": related_node["uuid"],
                            "name": related_node["name"],
                            "labels": related_node["labels"],
                            "summary": related_node.get("summary", ""),
                        })

                entity.related_nodes = related_nodes

            filtered_entities.append(entity)

        logger.info(f"필터링 완료: 전체 노드 {total_count}, 조건 만족 {len(filtered_entities)}, "
                   f"엔티티 타입: {entity_types_found}")

        return FilteredEntities(
            entities=filtered_entities,
            entity_types=entity_types_found,
            total_count=total_count,
            filtered_count=len(filtered_entities),
        )

    def get_entity_with_context(
        self,
        graph_id: str,
        entity_uuid: str
    ) -> Optional[EntityNode]:
        """
        단일 엔티티 및 전체 컨텍스트 가져오기 (엣지 및 연결 노드, 재시도 메커니즘 포함)

        Args:
            graph_id: 그래프 ID
            entity_uuid: 엔티티 UUID

        Returns:
            EntityNode 또는 None
        """
        try:
            # 재시도 메커니즘으로 노드 가져오기
            node = self._call_with_retry(
                func=lambda: self.client.graph.node.get(uuid_=entity_uuid),
                operation_name=f"노드 상세 정보 가져오기(uuid={entity_uuid[:8]}...)"
            )

            if not node:
                return None

            # 노드의 엣지 가져오기
            edges = self.get_node_edges(entity_uuid)

            # 연결 검색을 위한 모든 노드 가져오기
            all_nodes = self.get_all_nodes(graph_id)
            node_map = {n["uuid"]: n for n in all_nodes}

            # 관련 엣지 및 노드 처리
            related_edges = []
            related_node_uuids = set()

            for edge in edges:
                if edge["source_node_uuid"] == entity_uuid:
                    related_edges.append({
                        "direction": "outgoing",
                        "edge_name": edge["name"],
                        "fact": edge["fact"],
                        "target_node_uuid": edge["target_node_uuid"],
                    })
                    related_node_uuids.add(edge["target_node_uuid"])
                else:
                    related_edges.append({
                        "direction": "incoming",
                        "edge_name": edge["name"],
                        "fact": edge["fact"],
                        "source_node_uuid": edge["source_node_uuid"],
                    })
                    related_node_uuids.add(edge["source_node_uuid"])

            # 연결 노드 정보 가져오기
            related_nodes = []
            for related_uuid in related_node_uuids:
                if related_uuid in node_map:
                    related_node = node_map[related_uuid]
                    related_nodes.append({
                        "uuid": related_node["uuid"],
                        "name": related_node["name"],
                        "labels": related_node["labels"],
                        "summary": related_node.get("summary", ""),
                    })

            return EntityNode(
                uuid=getattr(node, 'uuid_', None) or getattr(node, 'uuid', ''),
                name=node.name or "",
                labels=node.labels or [],
                summary=node.summary or "",
                attributes=node.attributes or {},
                related_edges=related_edges,
                related_nodes=related_nodes,
            )

        except Exception as e:
            logger.error(f"엔티티 {entity_uuid} 가져오기 실패: {str(e)}")
            return None

    def get_entities_by_type(
        self,
        graph_id: str,
        entity_type: str,
        enrich_with_edges: bool = True
    ) -> List[EntityNode]:
        """
        지정 타입의 모든 엔티티 가져오기

        Args:
            graph_id: 그래프 ID
            entity_type: 엔티티 타입 (예: "Student", "PublicFigure" 등)
            enrich_with_edges: 관련 엣지 정보 가져올지 여부

        Returns:
            엔티티 목록
        """
        result = self.filter_defined_entities(
            graph_id=graph_id,
            defined_entity_types=[entity_type],
            enrich_with_edges=enrich_with_edges
        )
        return result.entities
