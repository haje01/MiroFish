"""
OASIS Agent Profile 생성기
Zep 그래프의 엔티티를 OASIS 시뮬레이션 플랫폼에서 요구하는 Agent Profile 형식으로 변환

최적화 개선사항:
1. Zep 검색 기능을 호출하여 노드 정보를 이차적으로 보완
2. 프롬프트를 최적화하여 매우 상세한 페르소나 생성
3. 개인 엔티티와 추상 집단 엔티티 구분
"""

import json
import random
import time
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from datetime import datetime

from openai import OpenAI
from zep_cloud.client import Zep

from ..config import Config
from ..utils.logger import get_logger
from .zep_entity_reader import EntityNode, ZepEntityReader

logger = get_logger('mirofish.oasis_profile')


@dataclass
class OasisAgentProfile:
    """OASIS Agent Profile 데이터 구조"""
    # 공통 필드
    user_id: int
    user_name: str
    name: str
    bio: str
    persona: str

    # 선택 필드 - Reddit 스타일
    karma: int = 1000

    # 선택 필드 - Twitter 스타일
    friend_count: int = 100
    follower_count: int = 150
    statuses_count: int = 500

    # 추가 페르소나 정보
    age: Optional[int] = None
    gender: Optional[str] = None
    mbti: Optional[str] = None
    country: Optional[str] = None
    profession: Optional[str] = None
    interested_topics: List[str] = field(default_factory=list)

    # 소스 엔티티 정보
    source_entity_uuid: Optional[str] = None
    source_entity_type: Optional[str] = None
    
    created_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))
    
    def to_reddit_format(self) -> Dict[str, Any]:
        """Reddit 플랫폼 형식으로 변환"""
        profile = {
            "user_id": self.user_id,
            "username": self.user_name,  # OASIS 라이브러리가 요구하는 필드명 username（언더스코어 없음）
            "name": self.name,
            "bio": self.bio,
            "persona": self.persona,
            "karma": self.karma,
            "created_at": self.created_at,
        }

        # 추가 페르소나 정보 추가（있는 경우）
        if self.age:
            profile["age"] = self.age
        if self.gender:
            profile["gender"] = self.gender
        if self.mbti:
            profile["mbti"] = self.mbti
        if self.country:
            profile["country"] = self.country
        if self.profession:
            profile["profession"] = self.profession
        if self.interested_topics:
            profile["interested_topics"] = self.interested_topics

        return profile

    def to_twitter_format(self) -> Dict[str, Any]:
        """Twitter 플랫폼 형식으로 변환"""
        profile = {
            "user_id": self.user_id,
            "username": self.user_name,  # OASIS 라이브러리가 요구하는 필드명 username（언더스코어 없음）
            "name": self.name,
            "bio": self.bio,
            "persona": self.persona,
            "friend_count": self.friend_count,
            "follower_count": self.follower_count,
            "statuses_count": self.statuses_count,
            "created_at": self.created_at,
        }

        # 추가 페르소나 정보 추가
        if self.age:
            profile["age"] = self.age
        if self.gender:
            profile["gender"] = self.gender
        if self.mbti:
            profile["mbti"] = self.mbti
        if self.country:
            profile["country"] = self.country
        if self.profession:
            profile["profession"] = self.profession
        if self.interested_topics:
            profile["interested_topics"] = self.interested_topics

        return profile

    def to_dict(self) -> Dict[str, Any]:
        """완전한 딕셔너리 형식으로 변환"""
        return {
            "user_id": self.user_id,
            "user_name": self.user_name,
            "name": self.name,
            "bio": self.bio,
            "persona": self.persona,
            "karma": self.karma,
            "friend_count": self.friend_count,
            "follower_count": self.follower_count,
            "statuses_count": self.statuses_count,
            "age": self.age,
            "gender": self.gender,
            "mbti": self.mbti,
            "country": self.country,
            "profession": self.profession,
            "interested_topics": self.interested_topics,
            "source_entity_uuid": self.source_entity_uuid,
            "source_entity_type": self.source_entity_type,
            "created_at": self.created_at,
        }


class OasisProfileGenerator:
    """
    OASIS Profile 생성기

    Zep 그래프의 엔티티를 OASIS 시뮬레이션에 필요한 Agent Profile로 변환

    최적화 특성:
    1. Zep 그래프 검색 기능을 호출하여 더 풍부한 컨텍스트 획득
    2. 매우 상세한 페르소나 생성（기본 정보, 직업 경력, 성격 특성, 소셜 미디어 행동 등 포함）
    3. 개인 엔티티와 추상 집단 엔티티 구분
    """

    # MBTI 유형 목록
    MBTI_TYPES = [
        "INTJ", "INTP", "ENTJ", "ENTP",
        "INFJ", "INFP", "ENFJ", "ENFP",
        "ISTJ", "ISFJ", "ESTJ", "ESFJ",
        "ISTP", "ISFP", "ESTP", "ESFP"
    ]
    
    # 주요 국가 목록
    COUNTRIES = [
        "China", "US", "UK", "Japan", "Germany", "France",
        "Canada", "Australia", "Brazil", "India", "South Korea"
    ]

    # 개인 유형 엔티티（구체적인 페르소나 생성 필요）
    INDIVIDUAL_ENTITY_TYPES = [
        "student", "alumni", "professor", "person", "publicfigure",
        "expert", "faculty", "official", "journalist", "activist"
    ]

    # 집단/기관 유형 엔티티（집단 대표 페르소나 생성 필요）
    GROUP_ENTITY_TYPES = [
        "university", "governmentagency", "organization", "ngo",
        "mediaoutlet", "company", "institution", "group", "community"
    ]

    # 소셜 미디어 행위자가 될 수 없는 사물/장소/개념 키워드 (소문자)
    # ontology_generator.py의 동일 목록과 동기화 유지
    INANIMATE_TYPE_KEYWORDS = {
        "food", "meal", "dish", "rice", "drink", "beverage", "ingredient",
        "cuisine", "snack", "dessert", "bread", "fruit", "vegetable",
        "furniture", "table", "chair", "desk", "bed", "sofa", "shelf",
        "object", "item", "product", "device", "tool", "equipment", "machine",
        "appliance", "vehicle", "car", "bike",
        "place", "location", "area", "region", "city", "town", "village",
        "building", "facility", "park", "school", "hospital", "store",
        "restaurant", "hotel", "site",
        "event", "incident", "accident", "ceremony", "festival", "meeting",
        "conference", "occasion",
        "animal", "plant", "nature", "species", "creature",
        "concept", "idea", "theory", "topic", "issue", "trend", "phenomenon",
    }
    
    def __init__(
        self, 
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model_name: Optional[str] = None,
        zep_api_key: Optional[str] = None,
        graph_id: Optional[str] = None
    ):
        self.api_key = api_key or Config.LLM_API_KEY
        self.base_url = base_url or Config.LLM_BASE_URL
        self.model_name = model_name or Config.LLM_MODEL_NAME
        
        if not self.api_key:
            raise ValueError("LLM_API_KEY 가 설정되지 않았습니다")

        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url
        )

        # Zep 클라이언트 - 풍부한 컨텍스트 검색에 사용
        self.zep_api_key = zep_api_key or Config.ZEP_API_KEY
        self.zep_client = None
        self.graph_id = graph_id
        
        if self.zep_api_key:
            try:
                self.zep_client = Zep(api_key=self.zep_api_key)
            except Exception as e:
                logger.warning(f"Zep 클라이언트 초기화 실패: {e}")
    
    def generate_profile_from_entity(
        self,
        entity: EntityNode,
        user_id: int,
        use_llm: bool = True
    ) -> OasisAgentProfile:
        """
        Zep 엔티티로부터 OASIS Agent Profile 생성

        Args:
            entity: Zep 엔티티 노드
            user_id: 사용자 ID（OASIS용）
            use_llm: LLM을 사용하여 상세 페르소나 생성 여부

        Returns:
            OasisAgentProfile
        """
        entity_type = entity.get_entity_type() or "Entity"

        # 기본 정보
        name = entity.name
        user_name = self._generate_username(name)

        # 컨텍스트 정보 구성
        context = self._build_entity_context(entity)

        if use_llm:
            # LLM으로 상세 페르소나 생성
            profile_data = self._generate_profile_with_llm(
                entity_name=name,
                entity_type=entity_type,
                entity_summary=entity.summary,
                entity_attributes=entity.attributes,
                context=context
            )
        else:
            # 규칙 기반으로 기본 페르소나 생성
            profile_data = self._generate_profile_rule_based(
                entity_name=name,
                entity_type=entity_type,
                entity_summary=entity.summary,
                entity_attributes=entity.attributes
            )
        
        return OasisAgentProfile(
            user_id=user_id,
            user_name=user_name,
            name=name,
            bio=profile_data.get("bio", f"{entity_type}: {name}"),
            persona=profile_data.get("persona", entity.summary or f"A {entity_type} named {name}."),
            karma=profile_data.get("karma", random.randint(500, 5000)),
            friend_count=profile_data.get("friend_count", random.randint(50, 500)),
            follower_count=profile_data.get("follower_count", random.randint(100, 1000)),
            statuses_count=profile_data.get("statuses_count", random.randint(100, 2000)),
            age=profile_data.get("age"),
            gender=profile_data.get("gender"),
            mbti=profile_data.get("mbti"),
            country=profile_data.get("country"),
            profession=profile_data.get("profession"),
            interested_topics=profile_data.get("interested_topics", []),
            source_entity_uuid=entity.uuid,
            source_entity_type=entity_type,
        )
    
    def _generate_username(self, name: str) -> str:
        """사용자명 생성"""
        # 특수문자 제거 후 소문자로 변환
        username = name.lower().replace(" ", "_")
        username = ''.join(c for c in username if c.isalnum() or c == '_')

        # 중복 방지를 위한 랜덤 접미사 추가
        suffix = random.randint(100, 999)
        return f"{username}_{suffix}"
    
    def _search_zep_for_entity(self, entity: EntityNode) -> Dict[str, Any]:
        """
        Zep 그래프 하이브리드 검색으로 엔티티 관련 풍부한 정보 획득

        Zep에는 내장 하이브리드 검색 인터페이스가 없으므로
        edges와 nodes를 각각 검색한 후 결과를 병합합니다.
        병렬 요청으로 동시에 검색하여 효율성을 높입니다.

        Args:
            entity: 엔티티 노드 객체

        Returns:
            facts, node_summaries, context를 포함한 딕셔너리
        """
        import concurrent.futures
        
        if not self.zep_client:
            return {"facts": [], "node_summaries": [], "context": ""}
        
        entity_name = entity.name
        
        results = {
            "facts": [],
            "node_summaries": [],
            "context": ""
        }
        
        # 검색을 수행하려면 graph_id가 필요
        if not self.graph_id:
            logger.debug(f"Zep 검색 건너뜀: graph_id가 설정되지 않음")
            return results

        comprehensive_query = f"{entity_name}에 관한 모든 정보, 활동, 사건, 관계 및 배경"

        def search_edges():
            """에지（사실/관계）검색 - 재시도 메커니즘 포함"""
            max_retries = 3
            last_exception = None
            delay = 2.0

            for attempt in range(max_retries):
                try:
                    return self.zep_client.graph.search(
                        query=comprehensive_query,
                        graph_id=self.graph_id,
                        limit=30,
                        scope="edges",
                        reranker="rrf"
                    )
                except Exception as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        logger.debug(f"Zep 에지 검색 {attempt + 1}회 실패: {str(e)[:80]}, 재시도 중...")
                        time.sleep(delay)
                        delay *= 2
                    else:
                        logger.debug(f"Zep 에지 검색 {max_retries}회 시도 후에도 실패: {e}")
            return None

        def search_nodes():
            """노드（엔티티 요약）검색 - 재시도 메커니즘 포함"""
            max_retries = 3
            last_exception = None
            delay = 2.0

            for attempt in range(max_retries):
                try:
                    return self.zep_client.graph.search(
                        query=comprehensive_query,
                        graph_id=self.graph_id,
                        limit=20,
                        scope="nodes",
                        reranker="rrf"
                    )
                except Exception as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        logger.debug(f"Zep 노드 검색 {attempt + 1}회 실패: {str(e)[:80]}, 재시도 중...")
                        time.sleep(delay)
                        delay *= 2
                    else:
                        logger.debug(f"Zep 노드 검색 {max_retries}회 시도 후에도 실패: {e}")
            return None
        
        try:
            # edges와 nodes 검색을 병렬 실행
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                edge_future = executor.submit(search_edges)
                node_future = executor.submit(search_nodes)

                # 결과 획득
                edge_result = edge_future.result(timeout=30)
                node_result = node_future.result(timeout=30)

            # 에지 검색 결과 처리
            all_facts = set()
            if edge_result and hasattr(edge_result, 'edges') and edge_result.edges:
                for edge in edge_result.edges:
                    if hasattr(edge, 'fact') and edge.fact:
                        all_facts.add(edge.fact)
            results["facts"] = list(all_facts)

            # 노드 검색 결과 처리
            all_summaries = set()
            if node_result and hasattr(node_result, 'nodes') and node_result.nodes:
                for node in node_result.nodes:
                    if hasattr(node, 'summary') and node.summary:
                        all_summaries.add(node.summary)
                    if hasattr(node, 'name') and node.name and node.name != entity_name:
                        all_summaries.add(f"관련 엔티티: {node.name}")
            results["node_summaries"] = list(all_summaries)

            # 종합 컨텍스트 구성
            context_parts = []
            if results["facts"]:
                context_parts.append("사실 정보:\n" + "\n".join(f"- {f}" for f in results["facts"][:20]))
            if results["node_summaries"]:
                context_parts.append("관련 엔티티:\n" + "\n".join(f"- {s}" for s in results["node_summaries"][:10]))
            results["context"] = "\n\n".join(context_parts)

            logger.info(f"Zep 하이브리드 검색 완료: {entity_name}, 사실 {len(results['facts'])}건, 관련 노드 {len(results['node_summaries'])}개 획득")

        except concurrent.futures.TimeoutError:
            logger.warning(f"Zep 검색 타임아웃 ({entity_name})")
        except Exception as e:
            logger.warning(f"Zep 검색 실패 ({entity_name}): {e}")
        
        return results
    
    def _build_entity_context(self, entity: EntityNode) -> str:
        """
        엔티티의 완전한 컨텍스트 정보 구성

        포함 내용:
        1. 엔티티 자체의 에지 정보（사실）
        2. 연관 노드의 상세 정보
        3. Zep 하이브리드 검색으로 획득한 풍부한 정보
        """
        context_parts = []

        # 1. 엔티티 속성 정보 추가
        if entity.attributes:
            attrs = []
            for key, value in entity.attributes.items():
                if value and str(value).strip():
                    attrs.append(f"- {key}: {value}")
            if attrs:
                context_parts.append("### 엔티티 속성\n" + "\n".join(attrs))

        # 2. 관련 에지 정보 추가（사실/관계）
        existing_facts = set()
        if entity.related_edges:
            relationships = []
            for edge in entity.related_edges:  # 수량 제한 없음
                fact = edge.get("fact", "")
                edge_name = edge.get("edge_name", "")
                direction = edge.get("direction", "")

                if fact:
                    relationships.append(f"- {fact}")
                    existing_facts.add(fact)
                elif edge_name:
                    if direction == "outgoing":
                        relationships.append(f"- {entity.name} --[{edge_name}]--> (관련 엔티티)")
                    else:
                        relationships.append(f"- (관련 엔티티) --[{edge_name}]--> {entity.name}")

            if relationships:
                context_parts.append("### 관련 사실 및 관계\n" + "\n".join(relationships))

        # 3. 연관 노드의 상세 정보 추가
        if entity.related_nodes:
            related_info = []
            for node in entity.related_nodes:  # 수량 제한 없음
                node_name = node.get("name", "")
                node_labels = node.get("labels", [])
                node_summary = node.get("summary", "")

                # 기본 레이블 필터링
                custom_labels = [l for l in node_labels if l not in ["Entity", "Node"]]
                label_str = f" ({', '.join(custom_labels)})" if custom_labels else ""

                if node_summary:
                    related_info.append(f"- **{node_name}**{label_str}: {node_summary}")
                else:
                    related_info.append(f"- **{node_name}**{label_str}")

            if related_info:
                context_parts.append("### 연관 엔티티 정보\n" + "\n".join(related_info))

        # 4. Zep 하이브리드 검색으로 더 풍부한 정보 획득
        zep_results = self._search_zep_for_entity(entity)

        if zep_results.get("facts"):
            # 중복 제거: 이미 존재하는 사실 제외
            new_facts = [f for f in zep_results["facts"] if f not in existing_facts]
            if new_facts:
                context_parts.append("### Zep 검색으로 획득한 사실 정보\n" + "\n".join(f"- {f}" for f in new_facts[:15]))

        if zep_results.get("node_summaries"):
            context_parts.append("### Zep 검색으로 획득한 관련 노드\n" + "\n".join(f"- {s}" for s in zep_results["node_summaries"][:10]))

        return "\n\n".join(context_parts)
    
    def _is_individual_entity(self, entity_type: str) -> bool:
        """개인 유형 엔티티 여부 판단"""
        return entity_type.lower() in self.INDIVIDUAL_ENTITY_TYPES

    def _is_group_entity(self, entity_type: str) -> bool:
        """집단/기관 유형 엔티티 여부 판단"""
        return entity_type.lower() in self.GROUP_ENTITY_TYPES

    def _is_inanimate_entity_type(self, entity_type: str) -> bool:
        """사물/장소/개념 등 소셜 행위자가 될 수 없는 엔티티 타입인지 확인.

        PascalCase 이름을 단어 단위로 분리하여 매칭하므로 'FoodCompany'처럼
        사물 키워드를 포함하더라도 나머지 단어가 사회적 행위자를 나타내면 허용.
        모든 구성 단어가 사물 키워드일 때만 True 반환.
        """
        import re
        words = re.sub(r'([a-z])([A-Z])', r'\1 \2', entity_type).split()
        if not words:
            return False
        lower_words = [w.lower() for w in words]
        inanimate_words = [w for w in lower_words if w in self.INANIMATE_TYPE_KEYWORDS]
        SOCIAL_SUFFIXES = {
            "person", "people", "individual", "human",
            "company", "corporation", "firm", "enterprise", "business",
            "organization", "organisation", "agency", "authority", "body",
            "group", "community", "association", "union", "coalition",
            "media", "outlet", "platform", "network",
            "expert", "official", "leader", "officer", "representative",
            "manufacturer", "producer", "brand", "operator",
        }
        has_social_word = any(w in SOCIAL_SUFFIXES for w in lower_words)
        if has_social_word:
            return False
        return len(inanimate_words) > 0
    
    def _generate_profile_with_llm(
        self,
        entity_name: str,
        entity_type: str,
        entity_summary: str,
        entity_attributes: Dict[str, Any],
        context: str
    ) -> Dict[str, Any]:
        """
        LLM으로 매우 상세한 페르소나 생성

        엔티티 유형에 따라 구분:
        - 개인 엔티티: 구체적인 인물 설정 생성
        - 집단/기관 엔티티: 대표 계정 설정 생성
        """
        
        is_individual = self._is_individual_entity(entity_type)
        
        if is_individual:
            prompt = self._build_individual_persona_prompt(
                entity_name, entity_type, entity_summary, entity_attributes, context
            )
        else:
            prompt = self._build_group_persona_prompt(
                entity_name, entity_type, entity_summary, entity_attributes, context
            )

        # 성공하거나 최대 재시도 횟수에 도달할 때까지 여러 번 생성 시도
        max_attempts = 3
        last_error = None

        for attempt in range(max_attempts):
            try:
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=[
                        {"role": "system", "content": self._get_system_prompt(is_individual)},
                        {"role": "user", "content": prompt}
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.7 - (attempt * 0.1)  # 재시도마다 온도 낮춤
                    # max_tokens 설정하지 않음, LLM이 자유롭게 생성하도록
                )

                content = response.choices[0].message.content

                # 잘림 여부 확인（finish_reason이 'stop'이 아닌 경우）
                finish_reason = response.choices[0].finish_reason
                if finish_reason == 'length':
                    logger.warning(f"LLM 출력 잘림 (attempt {attempt+1}), 복구 시도 중...")
                    content = self._fix_truncated_json(content)

                # JSON 파싱 시도
                try:
                    result = json.loads(content)

                    # 필수 필드 검증
                    if "bio" not in result or not result["bio"]:
                        result["bio"] = entity_summary[:200] if entity_summary else f"{entity_type}: {entity_name}"
                    if "persona" not in result or not result["persona"]:
                        result["persona"] = entity_summary or f"{entity_name}은(는) {entity_type}입니다."

                    return result

                except json.JSONDecodeError as je:
                    logger.warning(f"JSON 파싱 실패 (attempt {attempt+1}): {str(je)[:80]}")

                    # JSON 복구 시도
                    result = self._try_fix_json(content, entity_name, entity_type, entity_summary)
                    if result.get("_fixed"):
                        del result["_fixed"]
                        return result

                    last_error = je

            except Exception as e:
                logger.warning(f"LLM 호출 실패 (attempt {attempt+1}): {str(e)[:80]}")
                last_error = e
                import time
                time.sleep(1 * (attempt + 1))  # 지수 백오프

        logger.warning(f"LLM 페르소나 생성 실패（{max_attempts}회 시도）: {last_error}, 규칙 기반 생성으로 전환")
        return self._generate_profile_rule_based(
            entity_name, entity_type, entity_summary, entity_attributes
        )
    
    def _fix_truncated_json(self, content: str) -> str:
        """잘린 JSON 복구（max_tokens 제한으로 출력이 잘린 경우）"""
        import re

        # JSON이 잘린 경우 닫기 시도
        content = content.strip()

        # 닫히지 않은 괄호 계산
        open_braces = content.count('{') - content.count('}')
        open_brackets = content.count('[') - content.count(']')

        # 닫히지 않은 문자열 확인
        # 간단한 확인: 마지막 인용 부호 뒤에 쉼표나 닫는 괄호가 없으면 문자열이 잘린 것일 수 있음
        if content and content[-1] not in '",}]':
            # 문자열 닫기 시도
            content += '"'

        # 괄호 닫기
        content += ']' * open_brackets
        content += '}' * open_braces
        
        return content
    
    def _try_fix_json(self, content: str, entity_name: str, entity_type: str, entity_summary: str = "") -> Dict[str, Any]:
        """손상된 JSON 복구 시도"""
        import re

        # 1. 먼저 잘린 경우 복구 시도
        content = self._fix_truncated_json(content)

        # 2. JSON 부분 추출 시도
        json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            json_str = json_match.group()

            # 3. 문자열 내 줄바꿈 문제 처리
            # 모든 문자열 값을 찾아 줄바꿈 교체
            def fix_string_newlines(match):
                s = match.group(0)
                # 문자열 내 실제 줄바꿈을 공백으로 교체
                s = s.replace('\n', ' ').replace('\r', ' ')
                # 불필요한 공백 교체
                s = re.sub(r'\s+', ' ', s)
                return s

            # JSON 문자열 값 매칭
            json_str = re.sub(r'"[^"\\]*(?:\\.[^"\\]*)*"', fix_string_newlines, json_str)

            # 4. 파싱 시도
            try:
                result = json.loads(json_str)
                result["_fixed"] = True
                return result
            except json.JSONDecodeError as e:
                # 5. 여전히 실패하면 더 공격적인 복구 시도
                try:
                    # 모든 제어 문자 제거
                    json_str = re.sub(r'[\x00-\x1f\x7f-\x9f]', ' ', json_str)
                    # 모든 연속 공백 교체
                    json_str = re.sub(r'\s+', ' ', json_str)
                    result = json.loads(json_str)
                    result["_fixed"] = True
                    return result
                except:
                    pass

        # 6. 내용에서 일부 정보 추출 시도
        bio_match = re.search(r'"bio"\s*:\s*"([^"]*)"', content)
        persona_match = re.search(r'"persona"\s*:\s*"([^"]*)', content)  # 잘렸을 수 있음

        bio = bio_match.group(1) if bio_match else (entity_summary[:200] if entity_summary else f"{entity_type}: {entity_name}")
        persona = persona_match.group(1) if persona_match else (entity_summary or f"{entity_name}은(는) {entity_type}입니다.")

        # 의미 있는 내용을 추출한 경우 복구 완료로 표시
        if bio_match or persona_match:
            logger.info(f"손상된 JSON에서 일부 정보 추출 완료")
            return {
                "bio": bio,
                "persona": persona,
                "_fixed": True
            }

        # 7. 완전히 실패하면 기본 구조 반환
        logger.warning(f"JSON 복구 실패, 기본 구조 반환")
        return {
            "bio": entity_summary[:200] if entity_summary else f"{entity_type}: {entity_name}",
            "persona": entity_summary or f"{entity_name}은(는) {entity_type}입니다."
        }
    
    def _get_system_prompt(self, is_individual: bool) -> str:
        """시스템 프롬프트 획득"""
        base_prompt = "You are an expert in generating social media user personas. Generate detailed, realistic personas for public opinion simulation, maximally reflecting existing real-world situations. Must return valid JSON format. All string values must not contain unescaped newlines. Use Korean."
        return base_prompt
    
    def _build_individual_persona_prompt(
        self,
        entity_name: str,
        entity_type: str,
        entity_summary: str,
        entity_attributes: Dict[str, Any],
        context: str
    ) -> str:
        """개인 엔티티 상세 페르소나 프롬프트 구성"""

        attrs_str = json.dumps(entity_attributes, ensure_ascii=False) if entity_attributes else "없음"
        context_str = context[:3000] if context else "추가 컨텍스트 없음"

        return f"""엔티티에 대한 상세한 소셜 미디어 사용자 페르소나를 생성하세요. 기존 현실 상황을 최대한 반영하세요.

엔티티 이름: {entity_name}
엔티티 유형: {entity_type}
엔티티 요약: {entity_summary}
엔티티 속성: {attrs_str}

컨텍스트 정보:
{context_str}

다음 필드를 포함하는 JSON을 생성하세요:

1. bio: 소셜 미디어 소개, 200자
2. persona: 상세 페르소나 설명（2000자 순수 텍스트）, 다음을 포함해야 함:
   - 기본 정보（나이, 직업, 교육 배경, 거주지）
   - 인물 배경（중요한 경험, 사건과의 관련성, 사회적 관계）
   - 성격 특성（MBTI 유형, 핵심 성격, 감정 표현 방식）
   - 소셜 미디어 행동（게시 빈도, 콘텐츠 선호, 상호작용 스타일, 언어 특성）
   - 입장과 관점（주제에 대한 태도, 흥분/감동시키는 콘텐츠）
   - 독특한 특성（말버릇, 특별한 경험, 개인 취미）
   - 개인 기억（페르소나의 중요 부분으로, 이 개인과 사건의 관련성 및 사건에서의 기존 행동과 반응 설명）
3. age: 나이 숫자（반드시 정수）
4. gender: 성별, 반드시 영문: "male" 또는 "female"
5. mbti: MBTI 유형（예: INTJ, ENFP 등）
6. country: 국가（한국어로, 예: "중국"）
7. profession: 직업
8. interested_topics: 관심 주제 배열

중요:
- 모든 필드 값은 문자열 또는 숫자여야 하며 줄바꿈 문자 사용 금지
- persona는 연속적인 텍스트 설명이어야 함
- 한국어 사용（gender 필드만 반드시 영문 male/female 사용）
- 내용은 엔티티 정보와 일치해야 함
- age는 유효한 정수여야 하고, gender는 "male" 또는 "female" 이어야 함
"""

    def _build_group_persona_prompt(
        self,
        entity_name: str,
        entity_type: str,
        entity_summary: str,
        entity_attributes: Dict[str, Any],
        context: str
    ) -> str:
        """집단/기관 엔티티 상세 페르소나 프롬프트 구성"""

        attrs_str = json.dumps(entity_attributes, ensure_ascii=False) if entity_attributes else "없음"
        context_str = context[:3000] if context else "추가 컨텍스트 없음"

        return f"""기관/집단 엔티티에 대한 상세한 소셜 미디어 계정 설정을 생성하세요. 기존 현실 상황을 최대한 반영하세요.

엔티티 이름: {entity_name}
엔티티 유형: {entity_type}
엔티티 요약: {entity_summary}
엔티티 속성: {attrs_str}

컨텍스트 정보:
{context_str}

다음 필드를 포함하는 JSON을 생성하세요:

1. bio: 공식 계정 소개, 200자, 전문적이고 격식 있는 내용
2. persona: 상세 계정 설정 설명（2000자 순수 텍스트）, 다음을 포함해야 함:
   - 기관 기본 정보（공식 명칭, 기관 성격, 설립 배경, 주요 기능）
   - 계정 포지셔닝（계정 유형, 대상 독자, 핵심 기능）
   - 발언 스타일（언어 특성, 자주 사용하는 표현, 금기 주제）
   - 게시 콘텐츠 특성（콘텐츠 유형, 게시 빈도, 활동 시간대）
   - 입장과 태도（핵심 주제에 대한 공식 입장, 논란 처리 방식）
   - 특별 사항（대표하는 집단 특성, 운영 습관）
   - 기관 기억（페르소나의 중요 부분으로, 이 기관과 사건의 관련성 및 사건에서의 기존 행동과 반응 설명）
3. age: 30으로 고정（기관 계정의 가상 나이）
4. gender: "other"로 고정（기관 계정은 비개인을 나타내는 other 사용）
5. mbti: MBTI 유형, 계정 스타일 설명용（예: ISTJ는 엄격하고 보수적임）
6. country: 국가（한국어로, 예: "중국"）
7. profession: 기관 기능 설명
8. interested_topics: 관심 분야 배열

중요:
- 모든 필드 값은 문자열 또는 숫자여야 하며 null 값 허용 안 됨
- persona는 연속적인 텍스트 설명이어야 하며 줄바꿈 문자 사용 금지
- 한국어 사용（gender 필드만 반드시 영문 "other" 사용）
- age는 정수 30이어야 하고, gender는 문자열 "other" 이어야 함
- 기관 계정의 발언은 해당 기관의 정체성에 부합해야 함"""
    
    def _generate_profile_rule_based(
        self,
        entity_name: str,
        entity_type: str,
        entity_summary: str,
        entity_attributes: Dict[str, Any]
    ) -> Dict[str, Any]:
        """규칙 기반으로 기본 페르소나 생성"""

        # 엔티티 유형에 따라 다른 페르소나 생성
        entity_type_lower = entity_type.lower()
        
        if entity_type_lower in ["student", "alumni"]:
            return {
                "bio": f"{entity_type} with interests in academics and social issues.",
                "persona": f"{entity_name} is a {entity_type.lower()} who is actively engaged in academic and social discussions. They enjoy sharing perspectives and connecting with peers.",
                "age": random.randint(18, 30),
                "gender": random.choice(["male", "female"]),
                "mbti": random.choice(self.MBTI_TYPES),
                "country": random.choice(self.COUNTRIES),
                "profession": "Student",
                "interested_topics": ["Education", "Social Issues", "Technology"],
            }
        
        elif entity_type_lower in ["publicfigure", "expert", "faculty"]:
            return {
                "bio": f"Expert and thought leader in their field.",
                "persona": f"{entity_name} is a recognized {entity_type.lower()} who shares insights and opinions on important matters. They are known for their expertise and influence in public discourse.",
                "age": random.randint(35, 60),
                "gender": random.choice(["male", "female"]),
                "mbti": random.choice(["ENTJ", "INTJ", "ENTP", "INTP"]),
                "country": random.choice(self.COUNTRIES),
                "profession": entity_attributes.get("occupation", "Expert"),
                "interested_topics": ["Politics", "Economics", "Culture & Society"],
            }
        
        elif entity_type_lower in ["mediaoutlet", "socialmediaplatform"]:
            return {
                "bio": f"Official account for {entity_name}. News and updates.",
                "persona": f"{entity_name} is a media entity that reports news and facilitates public discourse. The account shares timely updates and engages with the audience on current events.",
                "age": 30,  # 기관 가상 나이
                "gender": "other",  # 기관은 other 사용
                "mbti": "ISTJ",  # 기관 스타일: 엄격하고 보수적
                "country": "중국",
                "profession": "Media",
                "interested_topics": ["General News", "Current Events", "Public Affairs"],
            }

        elif entity_type_lower in ["university", "governmentagency", "ngo", "organization"]:
            return {
                "bio": f"Official account of {entity_name}.",
                "persona": f"{entity_name} is an institutional entity that communicates official positions, announcements, and engages with stakeholders on relevant matters.",
                "age": 30,  # 기관 가상 나이
                "gender": "other",  # 기관은 other 사용
                "mbti": "ISTJ",  # 기관 스타일: 엄격하고 보수적
                "country": "중국",
                "profession": entity_type,
                "interested_topics": ["Public Policy", "Community", "Official Announcements"],
            }
        
        else:
            # 기본 인설
            return {
                "bio": entity_summary[:150] if entity_summary else f"{entity_type}: {entity_name}",
                "persona": entity_summary or f"{entity_name} is a {entity_type.lower()} participating in social discussions.",
                "age": random.randint(25, 50),
                "gender": random.choice(["male", "female"]),
                "mbti": random.choice(self.MBTI_TYPES),
                "country": random.choice(self.COUNTRIES),
                "profession": entity_type,
                "interested_topics": ["General", "Social Issues"],
            }
    
    def set_graph_id(self, graph_id: str):
        """Zep 검색에 사용할 그래프 ID 설정"""
        self.graph_id = graph_id

    def generate_profiles_from_entities(
        self,
        entities: List[EntityNode],
        use_llm: bool = True,
        progress_callback: Optional[callable] = None,
        graph_id: Optional[str] = None,
        parallel_count: int = 5,
        realtime_output_path: Optional[str] = None,
        output_platform: str = "reddit"
    ) -> List[OasisAgentProfile]:
        """
        엔티티로부터 Agent Profile 일괄 생성（병렬 생성 지원）

        Args:
            entities: 엔티티 목록
            use_llm: LLM을 사용하여 상세 페르소나 생성 여부
            progress_callback: 진행 콜백 함수 (current, total, message)
            graph_id: Zep 검색으로 더 풍부한 컨텍스트를 얻기 위한 그래프 ID
            parallel_count: 병렬 생성 수, 기본값 5
            realtime_output_path: 실시간 쓰기 파일 경로（제공 시 하나 생성할 때마다 한 번씩 쓰기）
            output_platform: 출력 플랫폼 형식 ("reddit" 또는 "twitter")

        Returns:
            Agent Profile 목록
        """
        import concurrent.futures
        from threading import Lock
        
        # Zep 검색에 사용할 graph_id 설정
        if graph_id:
            self.graph_id = graph_id

        # 사물/장소/개념 엔티티 사전 필터링
        filtered_entities = []
        skipped_names = []
        for entity in entities:
            etype = entity.get_entity_type() or "Entity"
            if self._is_inanimate_entity_type(etype):
                skipped_names.append(f"{entity.name}({etype})")
            else:
                filtered_entities.append(entity)
        if skipped_names:
            logger.warning(f"사물/장소/개념으로 판단된 엔티티 {len(skipped_names)}개 스킵: {', '.join(skipped_names)}")
        entities = filtered_entities

        total = len(entities)
        profiles = [None] * total  # 순서 유지를 위한 리스트 사전 할당
        completed_count = [0]  # 클로저에서 수정하기 위해 리스트 사용
        lock = Lock()

        # 실시간 파일 쓰기 보조 함수
        def save_profiles_realtime():
            """생성된 profiles를 파일에 실시간 저장"""
            if not realtime_output_path:
                return
            
            with lock:
                # 이미 생성된 profiles 필터링
                existing_profiles = [p for p in profiles if p is not None]
                if not existing_profiles:
                    return

                try:
                    if output_platform == "reddit":
                        # Reddit JSON 형식
                        profiles_data = [p.to_reddit_format() for p in existing_profiles]
                        with open(realtime_output_path, 'w', encoding='utf-8') as f:
                            json.dump(profiles_data, f, ensure_ascii=False, indent=2)
                    else:
                        # Twitter CSV 형식
                        import csv
                        profiles_data = [p.to_twitter_format() for p in existing_profiles]
                        if profiles_data:
                            fieldnames = list(profiles_data[0].keys())
                            with open(realtime_output_path, 'w', encoding='utf-8', newline='') as f:
                                writer = csv.DictWriter(f, fieldnames=fieldnames)
                                writer.writeheader()
                                writer.writerows(profiles_data)
                except Exception as e:
                    logger.warning(f"profiles 실시간 저장 실패: {e}")

        def generate_single_profile(idx: int, entity: EntityNode) -> tuple:
            """단일 profile 생성 작업 함수"""
            entity_type = entity.get_entity_type() or "Entity"

            try:
                profile = self.generate_profile_from_entity(
                    entity=entity,
                    user_id=idx,
                    use_llm=use_llm
                )

                # 생성된 페르소나를 콘솔과 로그에 실시간 출력
                self._print_generated_profile(entity.name, entity_type, profile)

                return idx, profile, None

            except Exception as e:
                logger.error(f"엔티티 {entity.name} 의 페르소나 생성 실패: {str(e)}")
                # 기본 profile 생성
                fallback_profile = OasisAgentProfile(
                    user_id=idx,
                    user_name=self._generate_username(entity.name),
                    name=entity.name,
                    bio=f"{entity_type}: {entity.name}",
                    persona=entity.summary or f"A participant in social discussions.",
                    source_entity_uuid=entity.uuid,
                    source_entity_type=entity_type,
                )
                return idx, fallback_profile, str(e)
        
        logger.info(f"{total}개 Agent 페르소나 병렬 생성 시작（병렬 수: {parallel_count}）...")
        print(f"\n{'='*60}")
        print(f"Agent 페르소나 생성 시작 - 총 {total}개 엔티티, 병렬 수: {parallel_count}")
        print(f"{'='*60}\n")

        # 스레드 풀을 사용하여 병렬 실행
        with concurrent.futures.ThreadPoolExecutor(max_workers=parallel_count) as executor:
            # 모든 작업 제출
            future_to_entity = {
                executor.submit(generate_single_profile, idx, entity): (idx, entity)
                for idx, entity in enumerate(entities)
            }

            # 결과 수집
            for future in concurrent.futures.as_completed(future_to_entity):
                idx, entity = future_to_entity[future]
                entity_type = entity.get_entity_type() or "Entity"

                try:
                    result_idx, profile, error = future.result()
                    profiles[result_idx] = profile

                    with lock:
                        completed_count[0] += 1
                        current = completed_count[0]

                    # 파일에 실시간 쓰기
                    save_profiles_realtime()

                    if progress_callback:
                        progress_callback(
                            current,
                            total,
                            f"완료 {current}/{total}: {entity.name}（{entity_type}）"
                        )

                    if error:
                        logger.warning(f"[{current}/{total}] {entity.name} 대체 페르소나 사용: {error}")
                    else:
                        logger.info(f"[{current}/{total}] 페르소나 생성 성공: {entity.name} ({entity_type})")

                except Exception as e:
                    logger.error(f"엔티티 {entity.name} 처리 중 예외 발생: {str(e)}")
                    with lock:
                        completed_count[0] += 1
                    profiles[idx] = OasisAgentProfile(
                        user_id=idx,
                        user_name=self._generate_username(entity.name),
                        name=entity.name,
                        bio=f"{entity_type}: {entity.name}",
                        persona=entity.summary or "A participant in social discussions.",
                        source_entity_uuid=entity.uuid,
                        source_entity_type=entity_type,
                    )
                    # 대체 페르소나도 파일에 실시간 쓰기
                    save_profiles_realtime()

        print(f"\n{'='*60}")
        print(f"페르소나 생성 완료! 총 {len([p for p in profiles if p])}개 Agent 생성")
        print(f"{'='*60}\n")
        
        return profiles
    
    def _print_generated_profile(self, entity_name: str, entity_type: str, profile: OasisAgentProfile):
        """생성된 페르소나를 콘솔에 실시간 출력（전체 내용, 잘림 없음）"""
        separator = "-" * 70

        # 전체 출력 내용 구성（잘림 없음）
        topics_str = ', '.join(profile.interested_topics) if profile.interested_topics else '없음'

        output_lines = [
            f"\n{separator}",
            f"[생성 완료] {entity_name} ({entity_type})",
            f"{separator}",
            f"사용자명: {profile.user_name}",
            f"",
            f"【소개】",
            f"{profile.bio}",
            f"",
            f"【상세 페르소나】",
            f"{profile.persona}",
            f"",
            f"【기본 속성】",
            f"나이: {profile.age} | 성별: {profile.gender} | MBTI: {profile.mbti}",
            f"직업: {profile.profession} | 국가: {profile.country}",
            f"관심 주제: {topics_str}",
            separator
        ]
        
        output = "\n".join(output_lines)
        
        # 콘솔에만 출력（중복 방지, logger는 더 이상 전체 내용 출력 안 함）
        print(output)
    
    def save_profiles(
        self,
        profiles: List[OasisAgentProfile],
        file_path: str,
        platform: str = "reddit"
    ):
        """
        Profile을 파일에 저장（플랫폼에 따라 올바른 형식 선택）

        OASIS 플랫폼 형식 요구사항:
        - Twitter: CSV 형식
        - Reddit: JSON 형식

        Args:
            profiles: Profile 목록
            file_path: 파일 경로
            platform: 플랫폼 유형 ("reddit" 또는 "twitter")
        """
        if platform == "twitter":
            self._save_twitter_csv(profiles, file_path)
        else:
            self._save_reddit_json(profiles, file_path)
    
    def _save_twitter_csv(self, profiles: List[OasisAgentProfile], file_path: str):
        """
        Twitter Profile을 CSV 형식으로 저장（OASIS 공식 요구사항 준수）

        OASIS Twitter가 요구하는 CSV 필드:
        - user_id: 사용자 ID（CSV 순서 기준 0부터 시작）
        - name: 사용자 실명
        - username: 시스템 내 사용자명
        - user_char: 상세 페르소나 설명（LLM 시스템 프롬프트에 주입하여 Agent 행동 지도）
        - description: 짧은 공개 소개（사용자 프로필 페이지에 표시）

        user_char vs description 차이:
        - user_char: 내부 사용, LLM 시스템 프롬프트, Agent 사고 및 행동 방식 결정
        - description: 외부 표시, 다른 사용자가 볼 수 있는 소개
        """
        import csv
        
        # 파일 확장자가 .csv인지 확인
        if not file_path.endswith('.csv'):
            file_path = file_path.replace('.json', '.csv')

        with open(file_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)

            # OASIS가 요구하는 헤더 작성
            headers = ['user_id', 'name', 'username', 'user_char', 'description']
            writer.writerow(headers)

            # 데이터 행 작성
            for idx, profile in enumerate(profiles):
                # user_char: 전체 페르소나（bio + persona）, LLM 시스템 프롬프트 용도
                user_char = profile.bio
                if profile.persona and profile.persona != profile.bio:
                    user_char = f"{profile.bio} {profile.persona}"
                # 줄바꿈 처리（CSV에서는 공백으로 대체）
                user_char = user_char.replace('\n', ' ').replace('\r', ' ')
                # 게시물/댓글은 항상 한국어로 작성하도록 지시
                user_char = f"{user_char} Always write your posts and comments in Korean."
                
                # description: 짧은 소개, 외부 표시용
                description = profile.bio.replace('\n', ' ').replace('\r', ' ')

                row = [
                    idx,                    # user_id: 0부터 시작하는 순서 ID
                    profile.name,           # name: 실명
                    profile.user_name,      # username: 사용자명
                    user_char,              # user_char: 전체 페르소나（내부 LLM 사용）
                    description             # description: 짧은 소개（외부 표시）
                ]
                writer.writerow(row)

        logger.info(f"{len(profiles)}개 Twitter Profile을 {file_path}에 저장 완료 (OASIS CSV 형식)")
    
    def _normalize_gender(self, gender: Optional[str]) -> str:
        """
        gender 필드를 OASIS가 요구하는 영문 형식으로 표준화

        OASIS 요구사항: male, female, other
        """
        if not gender:
            return "other"

        gender_lower = gender.lower().strip()

        # 중국어/한국어 매핑
        gender_map = {
            "男": "male",
            "女": "female",
            "남": "male",
            "여": "female",
            "机构": "other",
            "其他": "other",
            "기관": "other",
            "기타": "other",
            # 영문 이미 있음
            "male": "male",
            "female": "female",
            "other": "other",
        }
        
        return gender_map.get(gender_lower, "other")
    
    def _save_reddit_json(self, profiles: List[OasisAgentProfile], file_path: str):
        """
        Reddit Profile을 JSON 형식으로 저장

        to_reddit_format()과 일치하는 형식을 사용하여 OASIS가 올바르게 읽을 수 있도록 함.
        user_id 필드를 반드시 포함해야 함 - OASIS agent_graph.get_agent() 매칭의 핵심!

        필수 필드:
        - user_id: 사용자 ID（정수, initial_posts의 poster_agent_id와 매칭）
        - username: 사용자명
        - name: 표시 이름
        - bio: 소개
        - persona: 상세 페르소나
        - age: 나이（정수）
        - gender: "male", "female", 또는 "other"
        - mbti: MBTI 유형
        - country: 국가
        """
        data = []
        for idx, profile in enumerate(profiles):
            # to_reddit_format()과 일관된 형식 사용
            item = {
                "user_id": profile.user_id if profile.user_id is not None else idx,  # 핵심: 반드시 user_id 포함
                "username": profile.user_name,
                "name": profile.name,
                "bio": profile.bio[:150] if profile.bio else f"{profile.name}",
                "persona": (profile.persona or f"{profile.name} is a participant in social discussions.") + " Always write your posts and comments in Korean.",
                "karma": profile.karma if profile.karma else 1000,
                "created_at": profile.created_at,
                # OASIS 필수 필드 - 기본값 보장
                "age": profile.age if profile.age else 30,
                "gender": self._normalize_gender(profile.gender),
                "mbti": profile.mbti if profile.mbti else "ISTJ",
                "country": profile.country if profile.country else "중국",
            }
            
            # 선택적 필드
            if profile.profession:
                item["profession"] = profile.profession
            if profile.interested_topics:
                item["interested_topics"] = profile.interested_topics
            
            data.append(item)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"{len(profiles)}개 Reddit Profile을 {file_path}에 저장 완료 (JSON 형식, user_id 필드 포함)")

    # 하위 호환성을 위해 이전 메서드명을 별칭으로 유지
    def save_profiles_to_json(
        self,
        profiles: List[OasisAgentProfile],
        file_path: str,
        platform: str = "reddit"
    ):
        """[사용 중단] save_profiles() 메서드를 사용하세요"""
        logger.warning("save_profiles_to_json은 사용 중단되었습니다. save_profiles 메서드를 사용하세요.")
        self.save_profiles(profiles, file_path, platform)

