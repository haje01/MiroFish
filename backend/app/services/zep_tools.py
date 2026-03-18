"""
Zep 검색 도구 서비스
그래프 검색, 노드 읽기, 엣지 조회 등 도구를 캡슐화하여 Report Agent에서 사용

핵심 검색 도구 (최적화 후):
1. InsightForge (심층 인사이트 검색) - 가장 강력한 하이브리드 검색, 자동으로 하위 질문 생성 및 다차원 검색
2. PanoramaSearch (광범위 검색) - 전체 개요 획득, 만료된 콘텐츠 포함
3. QuickSearch (간단 검색) - 빠른 검색
"""

import time
import json
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field

from zep_cloud.client import Zep

from ..config import Config
from ..utils.logger import get_logger
from ..utils.llm_client import LLMClient
from ..utils.zep_paging import fetch_all_nodes, fetch_all_edges

logger = get_logger('mirofish.zep_tools')


@dataclass
class SearchResult:
    """검색 결과"""
    facts: List[str]
    edges: List[Dict[str, Any]]
    nodes: List[Dict[str, Any]]
    query: str
    total_count: int
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "facts": self.facts,
            "edges": self.edges,
            "nodes": self.nodes,
            "query": self.query,
            "total_count": self.total_count
        }
    
    def to_text(self) -> str:
        """텍스트 형식으로 변환, LLM 이해용"""
        text_parts = [f"검색 쿼리: {self.query}", f"{self.total_count}개의 관련 정보 발견"]

        if self.facts:
            text_parts.append("\n### 관련 사실:")
            for i, fact in enumerate(self.facts, 1):
                text_parts.append(f"{i}. {fact}")
        
        return "\n".join(text_parts)


@dataclass
class NodeInfo:
    """노드 정보"""
    uuid: str
    name: str
    labels: List[str]
    summary: str
    attributes: Dict[str, Any]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "uuid": self.uuid,
            "name": self.name,
            "labels": self.labels,
            "summary": self.summary,
            "attributes": self.attributes
        }
    
    def to_text(self) -> str:
        """텍스트 형식으로 변환"""
        entity_type = next((l for l in self.labels if l not in ["Entity", "Node"]), "알 수 없는 유형")
        return f"엔티티: {self.name} (유형: {entity_type})\n요약: {self.summary}"


@dataclass
class EdgeInfo:
    """엣지 정보"""
    uuid: str
    name: str
    fact: str
    source_node_uuid: str
    target_node_uuid: str
    source_node_name: Optional[str] = None
    target_node_name: Optional[str] = None
    # 시간 정보
    created_at: Optional[str] = None
    valid_at: Optional[str] = None
    invalid_at: Optional[str] = None
    expired_at: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "uuid": self.uuid,
            "name": self.name,
            "fact": self.fact,
            "source_node_uuid": self.source_node_uuid,
            "target_node_uuid": self.target_node_uuid,
            "source_node_name": self.source_node_name,
            "target_node_name": self.target_node_name,
            "created_at": self.created_at,
            "valid_at": self.valid_at,
            "invalid_at": self.invalid_at,
            "expired_at": self.expired_at
        }
    
    def to_text(self, include_temporal: bool = False) -> str:
        """텍스트 형식으로 변환"""
        source = self.source_node_name or self.source_node_uuid[:8]
        target = self.target_node_name or self.target_node_uuid[:8]
        base_text = f"관계: {source} --[{self.name}]--> {target}\n사실: {self.fact}"

        if include_temporal:
            valid_at = self.valid_at or "알 수 없음"
            invalid_at = self.invalid_at or "현재까지"
            base_text += f"\n유효기간: {valid_at} - {invalid_at}"
            if self.expired_at:
                base_text += f" (만료됨: {self.expired_at})"
        
        return base_text
    
    @property
    def is_expired(self) -> bool:
        """만료 여부"""
        return self.expired_at is not None
    
    @property
    def is_invalid(self) -> bool:
        """무효화 여부"""
        return self.invalid_at is not None


@dataclass
class InsightForgeResult:
    """
    심층 인사이트 검색 결과 (InsightForge)
    여러 하위 질문의 검색 결과 및 종합 분석 포함
    """
    query: str
    simulation_requirement: str
    sub_queries: List[str]
    
    # 각 차원별 검색 결과
    semantic_facts: List[str] = field(default_factory=list)  # 의미론적 검색 결과
    entity_insights: List[Dict[str, Any]] = field(default_factory=list)  # 엔티티 인사이트
    relationship_chains: List[str] = field(default_factory=list)  # 관계 체인

    # 통계 정보
    total_facts: int = 0
    total_entities: int = 0
    total_relationships: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "simulation_requirement": self.simulation_requirement,
            "sub_queries": self.sub_queries,
            "semantic_facts": self.semantic_facts,
            "entity_insights": self.entity_insights,
            "relationship_chains": self.relationship_chains,
            "total_facts": self.total_facts,
            "total_entities": self.total_entities,
            "total_relationships": self.total_relationships
        }
    
    def to_text(self) -> str:
        """상세 텍스트 형식으로 변환, LLM 이해용"""
        text_parts = [
            f"## 미래 예측 심층 분석",
            f"분석 질문: {self.query}",
            f"예측 시나리오: {self.simulation_requirement}",
            f"\n### 예측 데이터 통계",
            f"- 관련 예측 사실: {self.total_facts}건",
            f"- 관련 엔티티: {self.total_entities}개",
            f"- 관계 체인: {self.total_relationships}건"
        ]

        # 하위 질문
        if self.sub_queries:
            text_parts.append(f"\n### 분석된 하위 질문")
            for i, sq in enumerate(self.sub_queries, 1):
                text_parts.append(f"{i}. {sq}")

        # 의미론적 검색 결과
        if self.semantic_facts:
            text_parts.append(f"\n### 【핵심 사실】(보고서에서 이 원문을 인용하세요)")
            for i, fact in enumerate(self.semantic_facts, 1):
                text_parts.append(f"{i}. \"{fact}\"")

        # 엔티티 인사이트
        if self.entity_insights:
            text_parts.append(f"\n### 【핵심 엔티티】")
            for entity in self.entity_insights:
                text_parts.append(f"- **{entity.get('name', '알 수 없음')}** ({entity.get('type', '엔티티')})")
                if entity.get('summary'):
                    text_parts.append(f"  요약: \"{entity.get('summary')}\"")
                if entity.get('related_facts'):
                    text_parts.append(f"  관련 사실: {len(entity.get('related_facts', []))}건")

        # 관계 체인
        if self.relationship_chains:
            text_parts.append(f"\n### 【관계 체인】")
            for chain in self.relationship_chains:
                text_parts.append(f"- {chain}")
        
        return "\n".join(text_parts)


@dataclass
class PanoramaResult:
    """
    광범위 검색 결과 (Panorama)
    만료된 콘텐츠를 포함한 모든 관련 정보 포함
    """
    query: str
    
    # 전체 노드
    all_nodes: List[NodeInfo] = field(default_factory=list)
    # 전체 엣지 (만료된 것 포함)
    all_edges: List[EdgeInfo] = field(default_factory=list)
    # 현재 유효한 사실
    active_facts: List[str] = field(default_factory=list)
    # 만료/무효화된 사실 (이력 기록)
    historical_facts: List[str] = field(default_factory=list)

    # 통계
    total_nodes: int = 0
    total_edges: int = 0
    active_count: int = 0
    historical_count: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "all_nodes": [n.to_dict() for n in self.all_nodes],
            "all_edges": [e.to_dict() for e in self.all_edges],
            "active_facts": self.active_facts,
            "historical_facts": self.historical_facts,
            "total_nodes": self.total_nodes,
            "total_edges": self.total_edges,
            "active_count": self.active_count,
            "historical_count": self.historical_count
        }
    
    def to_text(self) -> str:
        """텍스트 형식으로 변환 (완전 버전, 잘림 없음)"""
        text_parts = [
            f"## 광범위 검색 결과 (미래 전체 조망)",
            f"쿼리: {self.query}",
            f"\n### 통계 정보",
            f"- 총 노드 수: {self.total_nodes}",
            f"- 총 엣지 수: {self.total_edges}",
            f"- 현재 유효 사실: {self.active_count}건",
            f"- 이력/만료 사실: {self.historical_count}건"
        ]

        # 현재 유효한 사실 (완전 출력, 잘림 없음)
        if self.active_facts:
            text_parts.append(f"\n### 【현재 유효 사실】(시뮬레이션 결과 원문)")
            for i, fact in enumerate(self.active_facts, 1):
                text_parts.append(f"{i}. \"{fact}\"")

        # 이력/만료 사실 (완전 출력, 잘림 없음)
        if self.historical_facts:
            text_parts.append(f"\n### 【이력/만료 사실】(변화 과정 기록)")
            for i, fact in enumerate(self.historical_facts, 1):
                text_parts.append(f"{i}. \"{fact}\"")

        # 핵심 엔티티 (완전 출력, 잘림 없음)
        if self.all_nodes:
            text_parts.append(f"\n### 【관련 엔티티】")
            for node in self.all_nodes:
                entity_type = next((l for l in node.labels if l not in ["Entity", "Node"]), "엔티티")
                text_parts.append(f"- **{node.name}** ({entity_type})")
        
        return "\n".join(text_parts)


@dataclass
class AgentInterview:
    """단일 Agent 인터뷰 결과"""
    agent_name: str
    agent_role: str  # 역할 유형 (예: 학생, 교사, 미디어 등)
    agent_bio: str  # 소개
    question: str  # 인터뷰 질문
    response: str  # 인터뷰 답변
    key_quotes: List[str] = field(default_factory=list)  # 핵심 인용문
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "agent_role": self.agent_role,
            "agent_bio": self.agent_bio,
            "question": self.question,
            "response": self.response,
            "key_quotes": self.key_quotes
        }
    
    def to_text(self) -> str:
        text = f"**{self.agent_name}** ({self.agent_role})\n"
        # agent_bio 전체 표시, 잘림 없음
        text += f"_소개: {self.agent_bio}_\n\n"
        text += f"**Q:** {self.question}\n\n"
        text += f"**A:** {self.response}\n"
        if self.key_quotes:
            text += "\n**핵심 인용문:**\n"
            for quote in self.key_quotes:
                # 각종 따옴표 제거
                clean_quote = quote.replace('\u201c', '').replace('\u201d', '').replace('"', '')
                clean_quote = clean_quote.replace('\u300c', '').replace('\u300d', '')
                clean_quote = clean_quote.strip()
                # 앞의 구두점 제거
                while clean_quote and clean_quote[0] in '，,；;：:、。！？\n\r\t ':
                    clean_quote = clean_quote[1:]
                # 질문 번호가 포함된 불필요한 내용 필터링 (질문1-9)
                skip = False
                for d in '123456789':
                    if f'\u95ee\u9898{d}' in clean_quote:
                        skip = True
                        break
                if skip:
                    continue
                # 너무 긴 내용 잘라내기 (마침표 기준, 강제 자름 아님)
                if len(clean_quote) > 150:
                    dot_pos = clean_quote.find('\u3002', 80)
                    if dot_pos > 0:
                        clean_quote = clean_quote[:dot_pos + 1]
                    else:
                        clean_quote = clean_quote[:147] + "..."
                if clean_quote and len(clean_quote) >= 10:
                    text += f'> "{clean_quote}"\n'
        return text


@dataclass
class InterviewResult:
    """
    인터뷰 결과 (Interview)
    여러 시뮬레이션 Agent의 인터뷰 답변 포함
    """
    interview_topic: str  # 인터뷰 주제
    interview_questions: List[str]  # 인터뷰 질문 목록

    # 인터뷰 선택된 Agent
    selected_agents: List[Dict[str, Any]] = field(default_factory=list)
    # 각 Agent의 인터뷰 답변
    interviews: List[AgentInterview] = field(default_factory=list)

    # Agent 선택 이유
    selection_reasoning: str = ""
    # 통합된 인터뷰 요약
    summary: str = ""

    # 통계
    total_agents: int = 0
    interviewed_count: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "interview_topic": self.interview_topic,
            "interview_questions": self.interview_questions,
            "selected_agents": self.selected_agents,
            "interviews": [i.to_dict() for i in self.interviews],
            "selection_reasoning": self.selection_reasoning,
            "summary": self.summary,
            "total_agents": self.total_agents,
            "interviewed_count": self.interviewed_count
        }
    
    def to_text(self) -> str:
        """상세 텍스트 형식으로 변환, LLM 이해 및 보고서 인용용"""
        text_parts = [
            "## 심층 인터뷰 보고서",
            f"**인터뷰 주제:** {self.interview_topic}",
            f"**인터뷰 인원:** {self.interviewed_count} / {self.total_agents}명 시뮬레이션 Agent",
            "\n### 인터뷰 대상 선택 이유",
            self.selection_reasoning or "（자동 선택）",
            "\n---",
            "\n### 인터뷰 기록",
        ]

        if self.interviews:
            for i, interview in enumerate(self.interviews, 1):
                text_parts.append(f"\n#### 인터뷰 #{i}: {interview.agent_name}")
                text_parts.append(interview.to_text())
                text_parts.append("\n---")
        else:
            text_parts.append("（인터뷰 기록 없음）\n\n---")

        text_parts.append("\n### 인터뷰 요약 및 핵심 의견")
        text_parts.append(self.summary or "（요약 없음）")

        return "\n".join(text_parts)


class ZepToolsService:
    """
    Zep 검색 도구 서비스

    【핵심 검색 도구 - 최적화 후】
    1. insight_forge - 심층 인사이트 검색 (가장 강력, 자동 하위 질문 생성, 다차원 검색)
    2. panorama_search - 광범위 검색 (전체 개요 획득, 만료된 콘텐츠 포함)
    3. quick_search - 간단 검색 (빠른 검색)
    4. interview_agents - 심층 인터뷰 (시뮬레이션 Agent 인터뷰, 다각도 의견 수집)

    【기본 도구】
    - search_graph - 그래프 의미론적 검색
    - get_all_nodes - 그래프의 모든 노드 조회
    - get_all_edges - 그래프의 모든 엣지 조회 (시간 정보 포함)
    - get_node_detail - 노드 상세 정보 조회
    - get_node_edges - 노드 관련 엣지 조회
    - get_entities_by_type - 유형별 엔티티 조회
    - get_entity_summary - 엔티티의 관계 요약 조회
    """
    
    # 재시도 설정
    MAX_RETRIES = 3
    RETRY_DELAY = 2.0
    
    def __init__(self, api_key: Optional[str] = None, llm_client: Optional[LLMClient] = None):
        self.api_key = api_key or Config.ZEP_API_KEY
        if not self.api_key:
            raise ValueError("ZEP_API_KEY가 설정되지 않았습니다")
        
        self.client = Zep(api_key=self.api_key)
        # InsightForge 하위 질문 생성을 위한 LLM 클라이언트
        self._llm_client = llm_client
        logger.info("ZepToolsService 초기화 완료")
    
    @property
    def llm(self) -> LLMClient:
        """LLM 클라이언트 지연 초기화"""
        if self._llm_client is None:
            self._llm_client = LLMClient()
        return self._llm_client
    
    def _call_with_retry(self, func, operation_name: str, max_retries: int = None):
        """재시도 메커니즘이 있는 API 호출"""
        max_retries = max_retries or self.MAX_RETRIES
        last_exception = None
        delay = self.RETRY_DELAY
        
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
                    delay *= 2
                else:
                    logger.error(f"Zep {operation_name} {max_retries}번 시도 후에도 실패: {str(e)}")
        
        raise last_exception
    
    def search_graph(
        self, 
        graph_id: str, 
        query: str, 
        limit: int = 10,
        scope: str = "edges"
    ) -> SearchResult:
        """
        그래프 의미론적 검색

        하이브리드 검색 (의미론적 + BM25)을 사용하여 그래프에서 관련 정보를 검색합니다.
        Zep Cloud의 search API를 사용할 수 없는 경우 로컬 키워드 매칭으로 대체합니다.

        Args:
            graph_id: 그래프 ID (Standalone Graph)
            query: 검색 쿼리
            limit: 반환 결과 수
            scope: 검색 범위, "edges" 또는 "nodes"

        Returns:
            SearchResult: 검색 결과
        """
        logger.info(f"그래프 검색: graph_id={graph_id}, query={query[:50]}...")
        
        # Zep Cloud Search API 사용 시도
        try:
            search_results = self._call_with_retry(
                func=lambda: self.client.graph.search(
                    graph_id=graph_id,
                    query=query,
                    limit=limit,
                    scope=scope,
                    reranker="cross_encoder"
                ),
                operation_name=f"그래프 검색(graph={graph_id})"
            )
            
            facts = []
            edges = []
            nodes = []
            
            # 엣지 검색 결과 파싱
            if hasattr(search_results, 'edges') and search_results.edges:
                for edge in search_results.edges:
                    if hasattr(edge, 'fact') and edge.fact:
                        facts.append(edge.fact)
                    edges.append({
                        "uuid": getattr(edge, 'uuid_', None) or getattr(edge, 'uuid', ''),
                        "name": getattr(edge, 'name', ''),
                        "fact": getattr(edge, 'fact', ''),
                        "source_node_uuid": getattr(edge, 'source_node_uuid', ''),
                        "target_node_uuid": getattr(edge, 'target_node_uuid', ''),
                    })
            
            # 노드 검색 결과 파싱
            if hasattr(search_results, 'nodes') and search_results.nodes:
                for node in search_results.nodes:
                    nodes.append({
                        "uuid": getattr(node, 'uuid_', None) or getattr(node, 'uuid', ''),
                        "name": getattr(node, 'name', ''),
                        "labels": getattr(node, 'labels', []),
                        "summary": getattr(node, 'summary', ''),
                    })
                    # 노드 요약도 사실로 간주
                    if hasattr(node, 'summary') and node.summary:
                        facts.append(f"[{node.name}]: {node.summary}")
            
            logger.info(f"검색 완료: 관련 사실 {len(facts)}건 발견")
            
            return SearchResult(
                facts=facts,
                edges=edges,
                nodes=nodes,
                query=query,
                total_count=len(facts)
            )
            
        except Exception as e:
            logger.warning(f"Zep Search API 실패, 로컬 검색으로 대체: {str(e)}")
            # 대체: 로컬 키워드 매칭 검색 사용
            return self._local_search(graph_id, query, limit, scope)
    
    def _local_search(
        self, 
        graph_id: str, 
        query: str, 
        limit: int = 10,
        scope: str = "edges"
    ) -> SearchResult:
        """
        로컬 키워드 매칭 검색 (Zep Search API의 대체 방안)

        모든 엣지/노드를 가져온 후 로컬에서 키워드 매칭 수행

        Args:
            graph_id: 그래프 ID
            query: 검색 쿼리
            limit: 반환 결과 수
            scope: 검색 범위

        Returns:
            SearchResult: 검색 결과
        """
        logger.info(f"로컬 검색 사용: query={query[:30]}...")
        
        facts = []
        edges_result = []
        nodes_result = []
        
        # 검색 쿼리 키워드 추출 (간단 분리)
        query_lower = query.lower()
        keywords = [w.strip() for w in query_lower.replace(',', ' ').replace('，', ' ').split() if len(w.strip()) > 1]
        
        def match_score(text: str) -> int:
            """텍스트와 쿼리의 매칭 점수 계산"""
            if not text:
                return 0
            text_lower = text.lower()
            # 쿼리 완전 일치
            if query_lower in text_lower:
                return 100
            # 키워드 매칭
            score = 0
            for keyword in keywords:
                if keyword in text_lower:
                    score += 10
            return score
        
        try:
            if scope in ["edges", "both"]:
                # 모든 엣지 가져와서 매칭
                all_edges = self.get_all_edges(graph_id)
                scored_edges = []
                for edge in all_edges:
                    score = match_score(edge.fact) + match_score(edge.name)
                    if score > 0:
                        scored_edges.append((score, edge))
                
                # 점수 순 정렬
                scored_edges.sort(key=lambda x: x[0], reverse=True)
                
                for score, edge in scored_edges[:limit]:
                    if edge.fact:
                        facts.append(edge.fact)
                    edges_result.append({
                        "uuid": edge.uuid,
                        "name": edge.name,
                        "fact": edge.fact,
                        "source_node_uuid": edge.source_node_uuid,
                        "target_node_uuid": edge.target_node_uuid,
                    })
            
            if scope in ["nodes", "both"]:
                # 모든 노드 가져와서 매칭
                all_nodes = self.get_all_nodes(graph_id)
                scored_nodes = []
                for node in all_nodes:
                    score = match_score(node.name) + match_score(node.summary)
                    if score > 0:
                        scored_nodes.append((score, node))
                
                scored_nodes.sort(key=lambda x: x[0], reverse=True)
                
                for score, node in scored_nodes[:limit]:
                    nodes_result.append({
                        "uuid": node.uuid,
                        "name": node.name,
                        "labels": node.labels,
                        "summary": node.summary,
                    })
                    if node.summary:
                        facts.append(f"[{node.name}]: {node.summary}")
            
            logger.info(f"로컬 검색 완료: 관련 사실 {len(facts)}건 발견")
            
        except Exception as e:
            logger.error(f"로컬 검색 실패: {str(e)}")
        
        return SearchResult(
            facts=facts,
            edges=edges_result,
            nodes=nodes_result,
            query=query,
            total_count=len(facts)
        )
    
    def get_all_nodes(self, graph_id: str) -> List[NodeInfo]:
        """
        그래프의 모든 노드 조회 (페이지네이션)

        Args:
            graph_id: 그래프 ID

        Returns:
            노드 목록
        """
        logger.info(f"그래프 {graph_id}의 모든 노드 조회 중...")

        nodes = fetch_all_nodes(self.client, graph_id)

        result = []
        for node in nodes:
            node_uuid = getattr(node, 'uuid_', None) or getattr(node, 'uuid', None) or ""
            result.append(NodeInfo(
                uuid=str(node_uuid) if node_uuid else "",
                name=node.name or "",
                labels=node.labels or [],
                summary=node.summary or "",
                attributes=node.attributes or {}
            ))

        logger.info(f"노드 {len(result)}개 조회 완료")
        return result

    def get_all_edges(self, graph_id: str, include_temporal: bool = True) -> List[EdgeInfo]:
        """
        그래프의 모든 엣지 조회 (페이지네이션, 시간 정보 포함)

        Args:
            graph_id: 그래프 ID
            include_temporal: 시간 정보 포함 여부 (기본값 True)

        Returns:
            엣지 목록 (created_at, valid_at, invalid_at, expired_at 포함)
        """
        logger.info(f"그래프 {graph_id}의 모든 엣지 조회 중...")

        edges = fetch_all_edges(self.client, graph_id)

        result = []
        for edge in edges:
            edge_uuid = getattr(edge, 'uuid_', None) or getattr(edge, 'uuid', None) or ""
            edge_info = EdgeInfo(
                uuid=str(edge_uuid) if edge_uuid else "",
                name=edge.name or "",
                fact=edge.fact or "",
                source_node_uuid=edge.source_node_uuid or "",
                target_node_uuid=edge.target_node_uuid or ""
            )

            # 시간 정보 추가
            if include_temporal:
                edge_info.created_at = getattr(edge, 'created_at', None)
                edge_info.valid_at = getattr(edge, 'valid_at', None)
                edge_info.invalid_at = getattr(edge, 'invalid_at', None)
                edge_info.expired_at = getattr(edge, 'expired_at', None)

            result.append(edge_info)

        logger.info(f"엣지 {len(result)}개 조회 완료")
        return result
    
    def get_node_detail(self, node_uuid: str) -> Optional[NodeInfo]:
        """
        단일 노드의 상세 정보 조회

        Args:
            node_uuid: 노드 UUID

        Returns:
            노드 정보 또는 None
        """
        logger.info(f"노드 상세 정보 조회: {node_uuid[:8]}...")
        
        try:
            node = self._call_with_retry(
                func=lambda: self.client.graph.node.get(uuid_=node_uuid),
                operation_name=f"노드 상세 정보 조회(uuid={node_uuid[:8]}...)"
            )
            
            if not node:
                return None
            
            return NodeInfo(
                uuid=getattr(node, 'uuid_', None) or getattr(node, 'uuid', ''),
                name=node.name or "",
                labels=node.labels or [],
                summary=node.summary or "",
                attributes=node.attributes or {}
            )
        except Exception as e:
            logger.error(f"노드 상세 정보 조회 실패: {str(e)}")
            return None
    
    def get_node_edges(self, graph_id: str, node_uuid: str) -> List[EdgeInfo]:
        """
        노드 관련 모든 엣지 조회

        그래프의 모든 엣지를 가져온 후 지정된 노드와 관련된 엣지를 필터링

        Args:
            graph_id: 그래프 ID
            node_uuid: 노드 UUID

        Returns:
            엣지 목록
        """
        logger.info(f"노드 {node_uuid[:8]}...의 관련 엣지 조회")
        
        try:
            # 그래프 모든 엣지 가져와서 필터링
            all_edges = self.get_all_edges(graph_id)
            
            result = []
            for edge in all_edges:
                # 엣지가 지정된 노드와 관련이 있는지 확인 (소스 또는 타깃으로)
                if edge.source_node_uuid == node_uuid or edge.target_node_uuid == node_uuid:
                    result.append(edge)
            
            logger.info(f"노드 관련 엣지 {len(result)}건 발견")
            return result
            
        except Exception as e:
            logger.warning(f"노드 엣지 조회 실패: {str(e)}")
            return []
    
    def get_entities_by_type(
        self, 
        graph_id: str, 
        entity_type: str
    ) -> List[NodeInfo]:
        """
        유형별 엔티티 조회

        Args:
            graph_id: 그래프 ID
            entity_type: 엔티티 유형 (예: Student, PublicFigure 등)

        Returns:
            해당 유형의 엔티티 목록
        """
        logger.info(f"유형이 {entity_type}인 엔티티 조회 중...")
        
        all_nodes = self.get_all_nodes(graph_id)
        
        filtered = []
        for node in all_nodes:
            # labels에 지정된 유형이 포함되어 있는지 확인
            if entity_type in node.labels:
                filtered.append(node)
        
        logger.info(f"{entity_type} 유형의 엔티티 {len(filtered)}개 발견")
        return filtered
    
    def get_entity_summary(
        self, 
        graph_id: str, 
        entity_name: str
    ) -> Dict[str, Any]:
        """
        지정된 엔티티의 관계 요약 조회

        해당 엔티티와 관련된 모든 정보를 검색하고 요약 생성

        Args:
            graph_id: 그래프 ID
            entity_name: 엔티티 이름

        Returns:
            엔티티 요약 정보
        """
        logger.info(f"엔티티 {entity_name}의 관계 요약 조회 중...")

        # 해당 엔티티 관련 정보 먼저 검색
        search_result = self.search_graph(
            graph_id=graph_id,
            query=entity_name,
            limit=20
        )
        
        # 모든 노드 중에서 해당 엔티티 찾기 시도
        all_nodes = self.get_all_nodes(graph_id)
        entity_node = None
        for node in all_nodes:
            if node.name.lower() == entity_name.lower():
                entity_node = node
                break
        
        related_edges = []
        if entity_node:
            # graph_id 파라미터 전달
            related_edges = self.get_node_edges(graph_id, entity_node.uuid)
        
        return {
            "entity_name": entity_name,
            "entity_info": entity_node.to_dict() if entity_node else None,
            "related_facts": search_result.facts,
            "related_edges": [e.to_dict() for e in related_edges],
            "total_relations": len(related_edges)
        }
    
    def get_graph_statistics(self, graph_id: str) -> Dict[str, Any]:
        """
        그래프 통계 정보 조회

        Args:
            graph_id: 그래프 ID

        Returns:
            통계 정보
        """
        logger.info(f"그래프 {graph_id}의 통계 정보 조회 중...")
        
        nodes = self.get_all_nodes(graph_id)
        edges = self.get_all_edges(graph_id)
        
        # 엔티티 유형 분포 통계
        entity_types = {}
        for node in nodes:
            for label in node.labels:
                if label not in ["Entity", "Node"]:
                    entity_types[label] = entity_types.get(label, 0) + 1
        
        # 관계 유형 분포 통계
        relation_types = {}
        for edge in edges:
            relation_types[edge.name] = relation_types.get(edge.name, 0) + 1
        
        return {
            "graph_id": graph_id,
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "entity_types": entity_types,
            "relation_types": relation_types
        }
    
    def get_simulation_context(
        self, 
        graph_id: str,
        simulation_requirement: str,
        limit: int = 30
    ) -> Dict[str, Any]:
        """
        시뮬레이션 관련 컨텍스트 정보 조회

        시뮬레이션 요구사항과 관련된 모든 정보를 종합 검색

        Args:
            graph_id: 그래프 ID
            simulation_requirement: 시뮬레이션 요구사항 설명
            limit: 각 정보 유형별 수량 제한

        Returns:
            시뮬레이션 컨텍스트 정보
        """
        logger.info(f"시뮬레이션 컨텍스트 조회: {simulation_requirement[:50]}...")

        # 시뮬레이션 요구사항 관련 정보 검색
        search_result = self.search_graph(
            graph_id=graph_id,
            query=simulation_requirement,
            limit=limit
        )
        
        # 그래프 통계 조회
        stats = self.get_graph_statistics(graph_id)
        
        # 모든 엔티티 노드 조회
        all_nodes = self.get_all_nodes(graph_id)
        
        # 실제 유형이 있는 엔티티 필터링 (순수 Entity 노드 제외)
        entities = []
        for node in all_nodes:
            custom_labels = [l for l in node.labels if l not in ["Entity", "Node"]]
            if custom_labels:
                entities.append({
                    "name": node.name,
                    "type": custom_labels[0],
                    "summary": node.summary
                })
        
        return {
            "simulation_requirement": simulation_requirement,
            "related_facts": search_result.facts,
            "graph_statistics": stats,
            "entities": entities[:limit],  # 수량 제한
            "total_entities": len(entities)
        }
    
    # ========== 핵심 검색 도구 (최적화 후) ==========
    
    def insight_forge(
        self,
        graph_id: str,
        query: str,
        simulation_requirement: str,
        report_context: str = "",
        max_sub_queries: int = 5
    ) -> InsightForgeResult:
        """
        【InsightForge - 심층 인사이트 검색】

        가장 강력한 하이브리드 검색 함수, 자동으로 문제를 분해하고 다차원 검색:
        1. LLM으로 문제를 여러 하위 질문으로 분해
        2. 각 하위 질문에 대해 의미론적 검색 수행
        3. 관련 엔티티를 추출하여 상세 정보 조회
        4. 관계 체인 추적
        5. 모든 결과를 통합하여 심층 인사이트 생성

        Args:
            graph_id: 그래프 ID
            query: 사용자 질문
            simulation_requirement: 시뮬레이션 요구사항 설명
            report_context: 보고서 컨텍스트 (선택, 더 정확한 하위 질문 생성에 활용)
            max_sub_queries: 최대 하위 질문 수

        Returns:
            InsightForgeResult: 심층 인사이트 검색 결과
        """
        logger.info(f"InsightForge 심층 인사이트 검색: {query[:50]}...")
        
        result = InsightForgeResult(
            query=query,
            simulation_requirement=simulation_requirement,
            sub_queries=[]
        )
        
        # Step 1: LLM을 사용하여 하위 질문 생성
        sub_queries = self._generate_sub_queries(
            query=query,
            simulation_requirement=simulation_requirement,
            report_context=report_context,
            max_queries=max_sub_queries
        )
        result.sub_queries = sub_queries
        logger.info(f"하위 질문 {len(sub_queries)}개 생성")

        # Step 2: 각 하위 질문에 대해 의미론적 검색 수행
        all_facts = []
        all_edges = []
        seen_facts = set()
        
        for sub_query in sub_queries:
            search_result = self.search_graph(
                graph_id=graph_id,
                query=sub_query,
                limit=15,
                scope="edges"
            )
            
            for fact in search_result.facts:
                if fact not in seen_facts:
                    all_facts.append(fact)
                    seen_facts.add(fact)
            
            all_edges.extend(search_result.edges)
        
        # 원래 질문에 대해서도 검색 수행
        main_search = self.search_graph(
            graph_id=graph_id,
            query=query,
            limit=20,
            scope="edges"
        )
        for fact in main_search.facts:
            if fact not in seen_facts:
                all_facts.append(fact)
                seen_facts.add(fact)
        
        result.semantic_facts = all_facts
        result.total_facts = len(all_facts)
        
        # Step 3: 엣지에서 관련 엔티티 UUID 추출, 해당 엔티티 정보만 조회 (전체 노드 조회 불필요)
        entity_uuids = set()
        for edge_data in all_edges:
            if isinstance(edge_data, dict):
                source_uuid = edge_data.get('source_node_uuid', '')
                target_uuid = edge_data.get('target_node_uuid', '')
                if source_uuid:
                    entity_uuids.add(source_uuid)
                if target_uuid:
                    entity_uuids.add(target_uuid)
        
        # 모든 관련 엔티티의 상세 정보 조회 (수량 제한 없음, 완전 출력)
        entity_insights = []
        node_map = {}  # 이후 관계 체인 구축에 사용

        for uuid in list(entity_uuids):  # 모든 엔티티 처리, 잘림 없음
            if not uuid:
                continue
            try:
                # 각 관련 노드 정보 개별 조회
                node = self.get_node_detail(uuid)
                if node:
                    node_map[uuid] = node
                    entity_type = next((l for l in node.labels if l not in ["Entity", "Node"]), "엔티티")

                    # 해당 엔티티 관련 모든 사실 조회 (잘림 없음)
                    related_facts = [
                        f for f in all_facts 
                        if node.name.lower() in f.lower()
                    ]
                    
                    entity_insights.append({
                        "uuid": node.uuid,
                        "name": node.name,
                        "type": entity_type,
                        "summary": node.summary,
                        "related_facts": related_facts  # 완전 출력, 잘림 없음
                    })
            except Exception as e:
                logger.debug(f"노드 {uuid} 조회 실패: {e}")
                continue
        
        result.entity_insights = entity_insights
        result.total_entities = len(entity_insights)
        
        # Step 4: 모든 관계 체인 구축 (수량 제한 없음)
        relationship_chains = []
        for edge_data in all_edges:  # 모든 엣지 처리, 잘림 없음
            if isinstance(edge_data, dict):
                source_uuid = edge_data.get('source_node_uuid', '')
                target_uuid = edge_data.get('target_node_uuid', '')
                relation_name = edge_data.get('name', '')
                
                source_name = node_map.get(source_uuid, NodeInfo('', '', [], '', {})).name or source_uuid[:8]
                target_name = node_map.get(target_uuid, NodeInfo('', '', [], '', {})).name or target_uuid[:8]
                
                chain = f"{source_name} --[{relation_name}]--> {target_name}"
                if chain not in relationship_chains:
                    relationship_chains.append(chain)
        
        result.relationship_chains = relationship_chains
        result.total_relationships = len(relationship_chains)
        
        logger.info(f"InsightForge 완료: 사실 {result.total_facts}건, 엔티티 {result.total_entities}개, 관계 {result.total_relationships}건")
        return result
    
    def _generate_sub_queries(
        self,
        query: str,
        simulation_requirement: str,
        report_context: str = "",
        max_queries: int = 5
    ) -> List[str]:
        """
        LLM을 사용하여 하위 질문 생성

        복잡한 질문을 독립적으로 검색 가능한 여러 하위 질문으로 분해
        """
        system_prompt = """You are a professional question analysis expert. Your task is to break down a complex question into multiple sub-questions that can be independently observed in a simulation world.

Requirements:
1. Each sub-question should be specific enough to find relevant Agent behaviors or events in the simulation world
2. Sub-questions should cover different dimensions of the original question (e.g., who, what, why, how, when, where)
3. Sub-questions should be relevant to the simulation scenario
4. Return in JSON format: {"sub_queries": ["sub-question 1", "sub-question 2", ...]}"""

        user_prompt = f"""Simulation background:
{simulation_requirement}

{f"Report context: {report_context[:500]}" if report_context else ""}

Please break down the following question into {max_queries} sub-questions:
{query}

Return the list of sub-questions in JSON format."""

        try:
            response = self.llm.chat_json(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3
            )
            
            sub_queries = response.get("sub_queries", [])
            # 문자열 목록인지 확인
            return [str(sq) for sq in sub_queries[:max_queries]]
            
        except Exception as e:
            logger.warning(f"하위 질문 생성 실패: {str(e)}, 기본 하위 질문 사용")
            # 대체: 원래 질문 기반 변형 반환
            return [
                query,
                f"{query}의 주요 참여자",
                f"{query}의 원인과 영향",
                f"{query}의 전개 과정"
            ][:max_queries]
    
    def panorama_search(
        self,
        graph_id: str,
        query: str,
        include_expired: bool = True,
        limit: int = 50
    ) -> PanoramaResult:
        """
        【PanoramaSearch - 광범위 검색】

        모든 관련 내용 및 이력/만료 정보를 포함한 전체 조망 획득:
        1. 모든 관련 노드 조회
        2. 모든 엣지 조회 (만료/무효화된 것 포함)
        3. 현재 유효한 정보와 이력 정보를 분류 정리

        이 도구는 이벤트 전체 현황을 파악하거나 변화 과정을 추적하는 시나리오에 적합합니다.

        Args:
            graph_id: 그래프 ID
            query: 검색 쿼리 (관련성 정렬에 사용)
            include_expired: 만료된 콘텐츠 포함 여부 (기본값 True)
            limit: 반환 결과 수 제한

        Returns:
            PanoramaResult: 광범위 검색 결과
        """
        logger.info(f"PanoramaSearch 광범위 검색: {query[:50]}...")
        
        result = PanoramaResult(query=query)
        
        # 모든 노드 조회
        all_nodes = self.get_all_nodes(graph_id)
        node_map = {n.uuid: n for n in all_nodes}
        result.all_nodes = all_nodes
        result.total_nodes = len(all_nodes)
        
        # 모든 엣지 조회 (시간 정보 포함)
        all_edges = self.get_all_edges(graph_id, include_temporal=True)
        result.all_edges = all_edges
        result.total_edges = len(all_edges)
        
        # 사실 분류
        active_facts = []
        historical_facts = []
        
        for edge in all_edges:
            if not edge.fact:
                continue
            
            # 사실에 엔티티 이름 추가
            source_name = node_map.get(edge.source_node_uuid, NodeInfo('', '', [], '', {})).name or edge.source_node_uuid[:8]
            target_name = node_map.get(edge.target_node_uuid, NodeInfo('', '', [], '', {})).name or edge.target_node_uuid[:8]
            
            # 만료/무효화 여부 판단
            is_historical = edge.is_expired or edge.is_invalid
            
            if is_historical:
                # 이력/만료 사실, 시간 태그 추가
                valid_at = edge.valid_at or "알 수 없음"
                invalid_at = edge.invalid_at or edge.expired_at or "알 수 없음"
                fact_with_time = f"[{valid_at} - {invalid_at}] {edge.fact}"
                historical_facts.append(fact_with_time)
            else:
                # 현재 유효한 사실
                active_facts.append(edge.fact)
        
        # 쿼리 기반 관련성 정렬
        query_lower = query.lower()
        keywords = [w.strip() for w in query_lower.replace(',', ' ').replace('，', ' ').split() if len(w.strip()) > 1]
        
        def relevance_score(fact: str) -> int:
            fact_lower = fact.lower()
            score = 0
            if query_lower in fact_lower:
                score += 100
            for kw in keywords:
                if kw in fact_lower:
                    score += 10
            return score
        
        # 정렬 및 수량 제한
        active_facts.sort(key=relevance_score, reverse=True)
        historical_facts.sort(key=relevance_score, reverse=True)
        
        result.active_facts = active_facts[:limit]
        result.historical_facts = historical_facts[:limit] if include_expired else []
        result.active_count = len(active_facts)
        result.historical_count = len(historical_facts)
        
        logger.info(f"PanoramaSearch 완료: 유효 {result.active_count}건, 이력 {result.historical_count}건")
        return result
    
    def quick_search(
        self,
        graph_id: str,
        query: str,
        limit: int = 10
    ) -> SearchResult:
        """
        【QuickSearch - 간단 검색】

        빠르고 가벼운 검색 도구:
        1. Zep 의미론적 검색 직접 호출
        2. 가장 관련성 높은 결과 반환
        3. 간단하고 직접적인 검색 요구에 적합

        Args:
            graph_id: 그래프 ID
            query: 검색 쿼리
            limit: 반환 결과 수

        Returns:
            SearchResult: 검색 결과
        """
        logger.info(f"QuickSearch 간단 검색: {query[:50]}...")

        # 기존 search_graph 메서드 직접 호출
        result = self.search_graph(
            graph_id=graph_id,
            query=query,
            limit=limit,
            scope="edges"
        )
        
        logger.info(f"QuickSearch 완료: 결과 {result.total_count}건")
        return result
    
    def interview_agents(
        self,
        simulation_id: str,
        interview_requirement: str,
        simulation_requirement: str = "",
        max_agents: int = 5,
        custom_questions: List[str] = None
    ) -> InterviewResult:
        """
        【InterviewAgents - 심층 인터뷰】

        실제 OASIS 인터뷰 API를 호출하여 시뮬레이션에서 실행 중인 Agent 인터뷰:
        1. 인물 설정 파일 자동 읽기, 모든 시뮬레이션 Agent 파악
        2. LLM으로 인터뷰 요구사항 분석, 가장 관련성 높은 Agent 지능적 선택
        3. LLM으로 인터뷰 질문 생성
        4. /api/simulation/interview/batch 인터페이스를 통해 실제 인터뷰 수행 (양쪽 플랫폼 동시 인터뷰)
        5. 모든 인터뷰 결과 통합, 인터뷰 보고서 생성

        【중요】이 기능은 시뮬레이션 환경이 실행 중 상태여야 합니다 (OASIS 환경 미종료)

        【사용 시나리오】
        - 다양한 역할 관점에서 이벤트에 대한 의견 파악 필요 시
        - 여러 측의 의견과 관점 수집 필요 시
        - 시뮬레이션 Agent의 실제 답변 획득 필요 시 (LLM 시뮬레이션이 아닌)

        Args:
            simulation_id: 시뮬레이션 ID (인물 설정 파일 위치 및 인터뷰 API 호출에 사용)
            interview_requirement: 인터뷰 요구사항 설명 (비구조화, 예: "학생들의 이벤트에 대한 의견 파악")
            simulation_requirement: 시뮬레이션 요구사항 배경 (선택)
            max_agents: 최대 인터뷰 Agent 수
            custom_questions: 사용자 정의 인터뷰 질문 (선택, 제공하지 않으면 자동 생성)

        Returns:
            InterviewResult: 인터뷰 결과
        """
        from .simulation_runner import SimulationRunner
        
        logger.info(f"InterviewAgents 심층 인터뷰 (실제 API): {interview_requirement[:50]}...")
        
        result = InterviewResult(
            interview_topic=interview_requirement,
            interview_questions=custom_questions or []
        )
        
        # Step 1: 인물 설정 파일 읽기
        profiles = self._load_agent_profiles(simulation_id)
        
        if not profiles:
            logger.warning(f"시뮬레이션 {simulation_id}의 인물 설정 파일을 찾을 수 없음")
            result.summary = "인터뷰 가능한 Agent 인물 설정 파일을 찾을 수 없습니다"
            return result
        
        result.total_agents = len(profiles)
        logger.info(f"Agent 인물 설정 {len(profiles)}개 로드 완료")

        # Step 2: LLM을 사용하여 인터뷰할 Agent 선택 (agent_id 목록 반환)
        selected_agents, selected_indices, selection_reasoning = self._select_agents_for_interview(
            profiles=profiles,
            interview_requirement=interview_requirement,
            simulation_requirement=simulation_requirement,
            max_agents=max_agents
        )
        
        result.selected_agents = selected_agents
        result.selection_reasoning = selection_reasoning
        logger.info(f"인터뷰할 Agent {len(selected_agents)}개 선택: {selected_indices}")

        # Step 3: 인터뷰 질문 생성 (제공되지 않은 경우)
        if not result.interview_questions:
            result.interview_questions = self._generate_interview_questions(
                interview_requirement=interview_requirement,
                simulation_requirement=simulation_requirement,
                selected_agents=selected_agents
            )
            logger.info(f"인터뷰 질문 {len(result.interview_questions)}개 생성")
        
        # 질문을 하나의 인터뷰 프롬프트로 합치기
        combined_prompt = "\n".join([f"{i+1}. {q}" for i, q in enumerate(result.interview_questions)])
        
        # 최적화 접두사 추가, Agent 응답 형식 제약
        INTERVIEW_PROMPT_PREFIX = (
            "You are being interviewed. Based on your persona, all past memories and actions, "
            "please directly answer the following questions in plain text.\n"
            "Response requirements:\n"
            "1. Answer directly in natural language, do not call any tools\n"
            "2. Do not return JSON format or tool call format\n"
            "3. Do not use Markdown headings (e.g., #, ##, ###)\n"
            "4. Answer each question by number, starting each answer with 'Question X:' (X is the question number)\n"
            "5. Separate answers with blank lines\n"
            "6. Provide substantive answers, at least 2-3 sentences per question\n\n"
        )
        optimized_prompt = f"{INTERVIEW_PROMPT_PREFIX}{combined_prompt}"
        
        # Step 4: 실제 인터뷰 API 호출 (플랫폼 미지정, 기본값으로 양쪽 플랫폼 동시 인터뷰)
        try:
            # 일괄 인터뷰 목록 구성 (플랫폼 미지정, 양쪽 플랫폼 인터뷰)
            interviews_request = []
            for agent_idx in selected_indices:
                interviews_request.append({
                    "agent_id": agent_idx,
                    "prompt": optimized_prompt  # 최적화된 프롬프트 사용
                    # 플랫폼 미지정 시 API가 twitter와 reddit 두 플랫폼에서 인터뷰
                })
            
            logger.info(f"일괄 인터뷰 API 호출 (양쪽 플랫폼): Agent {len(interviews_request)}개")

            # SimulationRunner의 일괄 인터뷰 메서드 호출 (플랫폼 미지정, 양쪽 플랫폼 인터뷰)
            api_result = SimulationRunner.interview_agents_batch(
                simulation_id=simulation_id,
                interviews=interviews_request,
                platform=None,  # 플랫폼 미지정, 양쪽 플랫폼 인터뷰
                timeout=180.0   # 양쪽 플랫폼은 더 긴 타임아웃 필요
            )
            
            logger.info(f"인터뷰 API 반환: 결과 {api_result.get('interviews_count', 0)}개, success={api_result.get('success')}")
            
            # API 호출 성공 여부 확인
            if not api_result.get("success", False):
                error_msg = api_result.get("error", "알 수 없는 오류")
                logger.warning(f"인터뷰 API 반환 실패: {error_msg}")
                result.summary = f"인터뷰 API 호출 실패: {error_msg}. OASIS 시뮬레이션 환경 상태를 확인하세요."
                return result
            
            # Step 5: API 반환 결과 파싱, AgentInterview 객체 구성
            # 양쪽 플랫폼 모드 반환 형식: {"twitter_0": {...}, "reddit_0": {...}, "twitter_1": {...}, ...}
            api_data = api_result.get("result", {})
            results_dict = api_data.get("results", {}) if isinstance(api_data, dict) else {}
            
            for i, agent_idx in enumerate(selected_indices):
                agent = selected_agents[i]
                agent_name = agent.get("realname", agent.get("username", f"Agent_{agent_idx}"))
                agent_role = agent.get("profession", "알 수 없음")
                agent_bio = agent.get("bio", "")
                
                # 두 플랫폼에서의 해당 Agent 인터뷰 결과 조회
                twitter_result = results_dict.get(f"twitter_{agent_idx}", {})
                reddit_result = results_dict.get(f"reddit_{agent_idx}", {})
                
                twitter_response = twitter_result.get("response", "")
                reddit_response = reddit_result.get("response", "")

                # 가능한 도구 호출 JSON 포장 제거
                twitter_response = self._clean_tool_call_response(twitter_response)
                reddit_response = self._clean_tool_call_response(reddit_response)

                # 항상 양쪽 플랫폼 태그 출력
                twitter_text = twitter_response if twitter_response else "（해당 플랫폼에서 응답 없음）"
                reddit_text = reddit_response if reddit_response else "（해당 플랫폼에서 응답 없음）"
                response_text = f"【Twitter 플랫폼 답변】\n{twitter_text}\n\n【Reddit 플랫폼 답변】\n{reddit_text}"

                # 핵심 인용문 추출 (양쪽 플랫폼 답변에서)
                import re
                combined_responses = f"{twitter_response} {reddit_response}"

                # 응답 텍스트 정리: 태그, 번호, Markdown 등 불필요한 요소 제거
                clean_text = re.sub(r'#{1,6}\s+', '', combined_responses)
                clean_text = re.sub(r'\{[^}]*tool_name[^}]*\}', '', clean_text)
                clean_text = re.sub(r'[*_`|>~\-]{2,}', '', clean_text)
                clean_text = re.sub(r'질문\d+[：:]\s*', '', clean_text)
                clean_text = re.sub(r'【[^】]+】', '', clean_text)

                # 전략1 (주요): 실질적인 내용이 있는 완전한 문장 추출
                sentences = re.split(r'[。！？]', clean_text)
                meaningful = [
                    s.strip() for s in sentences
                    if 20 <= len(s.strip()) <= 150
                    and not re.match(r'^[\s\W，,；;：:、]+', s.strip())
                    and not s.strip().startswith(('{', '질문'))
                ]
                meaningful.sort(key=len, reverse=True)
                key_quotes = [s + "。" for s in meaningful[:3]]

                # 전략2 (보완): 올바르게 대응된 중국어 인용부호「」 내의 긴 텍스트
                if not key_quotes:
                    paired = re.findall(r'\u201c([^\u201c\u201d]{15,100})\u201d', clean_text)
                    paired += re.findall(r'\u300c([^\u300c\u300d]{15,100})\u300d', clean_text)
                    key_quotes = [q for q in paired if not re.match(r'^[，,；;：:、]', q)][:3]
                
                interview = AgentInterview(
                    agent_name=agent_name,
                    agent_role=agent_role,
                    agent_bio=agent_bio[:1000],  # bio 길이 제한 확대
                    question=combined_prompt,
                    response=response_text,
                    key_quotes=key_quotes[:5]
                )
                result.interviews.append(interview)
            
            result.interviewed_count = len(result.interviews)
            
        except ValueError as e:
            # 시뮬레이션 환경 미실행
            logger.warning(f"인터뷰 API 호출 실패 (환경 미실행?): {e}")
            result.summary = f"인터뷰 실패: {str(e)}. 시뮬레이션 환경이 이미 종료되었을 수 있습니다. OASIS 환경이 실행 중인지 확인하세요."
            return result
        except Exception as e:
            logger.error(f"인터뷰 API 호출 예외: {e}")
            import traceback
            logger.error(traceback.format_exc())
            result.summary = f"인터뷰 과정에서 오류 발생: {str(e)}"
            return result
        
        # Step 6: 인터뷰 요약 생성
        if result.interviews:
            result.summary = self._generate_interview_summary(
                interviews=result.interviews,
                interview_requirement=interview_requirement
            )
        
        logger.info(f"InterviewAgents 완료: Agent {result.interviewed_count}개 인터뷰 (양쪽 플랫폼)")
        return result
    
    @staticmethod
    def _clean_tool_call_response(response: str) -> str:
        """Agent 응답에서 JSON 도구 호출 포장 제거, 실제 내용 추출"""
        if not response or not response.strip().startswith('{'):
            return response
        text = response.strip()
        if 'tool_name' not in text[:80]:
            return response
        import re as _re
        try:
            data = json.loads(text)
            if isinstance(data, dict) and 'arguments' in data:
                for key in ('content', 'text', 'body', 'message', 'reply'):
                    if key in data['arguments']:
                        return str(data['arguments'][key])
        except (json.JSONDecodeError, KeyError, TypeError):
            match = _re.search(r'"content"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
            if match:
                return match.group(1).replace('\\n', '\n').replace('\\"', '"')
        return response

    def _load_agent_profiles(self, simulation_id: str) -> List[Dict[str, Any]]:
        """시뮬레이션의 Agent 인물 설정 파일 로드"""
        import os
        import csv
        
        # 인물 설정 파일 경로 구성
        sim_dir = os.path.join(
            os.path.dirname(__file__), 
            f'../../uploads/simulations/{simulation_id}'
        )
        
        profiles = []
        
        # Reddit JSON 형식 우선 시도
        reddit_profile_path = os.path.join(sim_dir, "reddit_profiles.json")
        if os.path.exists(reddit_profile_path):
            try:
                with open(reddit_profile_path, 'r', encoding='utf-8') as f:
                    profiles = json.load(f)
                logger.info(f"reddit_profiles.json에서 인물 설정 {len(profiles)}개 로드")
                return profiles
            except Exception as e:
                logger.warning(f"reddit_profiles.json 읽기 실패: {e}")
        
        # Twitter CSV 형식 시도
        twitter_profile_path = os.path.join(sim_dir, "twitter_profiles.csv")
        if os.path.exists(twitter_profile_path):
            try:
                with open(twitter_profile_path, 'r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        # CSV 형식을 통합 형식으로 변환
                        profiles.append({
                            "realname": row.get("name", ""),
                            "username": row.get("username", ""),
                            "bio": row.get("description", ""),
                            "persona": row.get("user_char", ""),
                            "profession": "알 수 없음"
                        })
                logger.info(f"twitter_profiles.csv에서 인물 설정 {len(profiles)}개 로드")
                return profiles
            except Exception as e:
                logger.warning(f"twitter_profiles.csv 읽기 실패: {e}")
        
        return profiles
    
    def _select_agents_for_interview(
        self,
        profiles: List[Dict[str, Any]],
        interview_requirement: str,
        simulation_requirement: str,
        max_agents: int
    ) -> tuple:
        """
        LLM을 사용하여 인터뷰할 Agent 선택

        Returns:
            tuple: (selected_agents, selected_indices, reasoning)
                - selected_agents: 선택된 Agent의 전체 정보 목록
                - selected_indices: 선택된 Agent의 인덱스 목록 (API 호출에 사용)
                - reasoning: 선택 이유
        """

        # Agent 요약 목록 구성
        agent_summaries = []
        for i, profile in enumerate(profiles):
            summary = {
                "index": i,
                "name": profile.get("realname", profile.get("username", f"Agent_{i}")),
                "profession": profile.get("profession", "알 수 없음"),
                "bio": profile.get("bio", "")[:200],
                "interested_topics": profile.get("interested_topics", [])
            }
            agent_summaries.append(summary)
        
        system_prompt = """You are a professional interview planning expert. Your task is to select the most suitable interview subjects from a list of simulated Agents based on the interview requirements.

Selection criteria:
1. The Agent's identity/profession is relevant to the interview topic
2. The Agent may hold unique or valuable perspectives
3. Select diverse viewpoints (e.g., supporters, opponents, neutral parties, professionals, etc.)
4. Prioritize roles directly related to the event

Return in JSON format:
{
    "selected_indices": [list of selected Agent indices],
    "reasoning": "explanation of selection reasoning"
}"""

        user_prompt = f"""Interview requirements:
{interview_requirement}

Simulation background:
{simulation_requirement if simulation_requirement else "Not provided"}

List of available Agents (total {len(agent_summaries)}):
{json.dumps(agent_summaries, ensure_ascii=False, indent=2)}

Please select up to {max_agents} most suitable Agents for interview and explain the selection reasoning."""

        try:
            response = self.llm.chat_json(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3
            )
            
            selected_indices = response.get("selected_indices", [])[:max_agents]
            reasoning = response.get("reasoning", "관련성 기반 자동 선택")
            
            # 선택된 Agent의 전체 정보 조회
            selected_agents = []
            valid_indices = []
            for idx in selected_indices:
                if 0 <= idx < len(profiles):
                    selected_agents.append(profiles[idx])
                    valid_indices.append(idx)
            
            return selected_agents, valid_indices, reasoning
            
        except Exception as e:
            logger.warning(f"LLM Agent 선택 실패, 기본 선택 사용: {e}")
            # 대체: 처음 N개 선택
            selected = profiles[:max_agents]
            indices = list(range(min(max_agents, len(profiles))))
            return selected, indices, "기본 선택 전략 사용"
    
    def _generate_interview_questions(
        self,
        interview_requirement: str,
        simulation_requirement: str,
        selected_agents: List[Dict[str, Any]]
    ) -> List[str]:
        """LLM을 사용하여 인터뷰 질문 생성"""

        agent_roles = [a.get("profession", "알 수 없음") for a in selected_agents]

        system_prompt = """You are a professional journalist/interviewer. Generate 3-5 in-depth interview questions based on the interview requirements.

Question requirements:
1. Open-ended questions that encourage detailed answers
2. Questions that may have different answers for different roles
3. Cover multiple dimensions including facts, opinions, and feelings
4. Natural language, like a real interview
5. Keep each question under 50 words, concise and clear
6. Ask directly, without including background explanations or prefixes

Return in JSON format: {"questions": ["question 1", "question 2", ...]}"""

        user_prompt = f"""Interview requirements: {interview_requirement}

Simulation background: {simulation_requirement if simulation_requirement else "Not provided"}

Interviewee roles: {', '.join(agent_roles)}

Please generate 3-5 interview questions."""

        try:
            response = self.llm.chat_json(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.5
            )
            
            return response.get("questions", [f"{interview_requirement}에 대해 어떻게 생각하시나요?"])
            
        except Exception as e:
            logger.warning(f"인터뷰 질문 생성 실패: {e}")
            return [
                f"{interview_requirement}에 대한 당신의 의견은 무엇인가요?",
                "이 일이 당신 또는 당신이 대표하는 그룹에 어떤 영향을 미쳤나요?",
                "이 문제를 어떻게 해결하거나 개선해야 한다고 생각하시나요?"
            ]
    
    def _generate_interview_summary(
        self,
        interviews: List[AgentInterview],
        interview_requirement: str
    ) -> str:
        """인터뷰 요약 생성"""

        if not interviews:
            return "인터뷰를 완료하지 못했습니다"

        # 모든 인터뷰 내용 수집
        interview_texts = []
        for interview in interviews:
            interview_texts.append(f"【{interview.agent_name}（{interview.agent_role}）】\n{interview.response[:500]}")
        
        system_prompt = """You are a professional news editor. Based on responses from multiple interviewees, generate an interview summary.

Summary requirements:
1. Extract the main viewpoints from all parties
2. Point out consensus and divergence in opinions
3. Highlight valuable quotes
4. Objective and neutral, not favoring any party
5. Keep within 1000 words

Format constraints (must follow):
- Use plain text paragraphs, separated by blank lines between sections
- Do not use Markdown headings (e.g., #, ##, ###)
- Do not use dividers (e.g., ---, ***)
- Use Korean quotation marks 「」 when quoting interviewees
- Can use **bold** for keywords, but do not use other Markdown syntax"""

        user_prompt = f"""Interview topic: {interview_requirement}

Interview content:
{"".join(interview_texts)}

Please generate the interview summary."""

        try:
            summary = self.llm.chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3,
                max_tokens=800
            )
            return summary
            
        except Exception as e:
            logger.warning(f"인터뷰 요약 생성 실패: {e}")
            # 대체: 단순 연결
            return f"총 {len(interviews)}명을 인터뷰했습니다. 인터뷰 대상: " + ", ".join([i.agent_name for i in interviews])
