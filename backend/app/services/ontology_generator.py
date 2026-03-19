"""
온톨로지 생성 서비스
인터페이스 1: 텍스트 내용을 분석하여 사회 시뮬레이션에 적합한 엔티티 및 관계 유형 정의 생성
"""

import json
from typing import Dict, Any, List, Optional
from ..utils.llm_client import LLMClient


# 온톨로지 생성을 위한 시스템 프롬프트
ONTOLOGY_SYSTEM_PROMPT = """You are an expert knowledge graph ontology designer. Your task is to analyze given text content and simulation requirements to design entity types and relationship types suitable for **social media public opinion simulation**.

**IMPORTANT: You must output valid JSON format data only. Do not output any other content.**

## Core Task Background

We are building a **social media public opinion simulation system**. In this system:
- Each entity is an "account" or "subject" that can voice opinions, interact, and spread information on social media
- Entities influence each other, repost, comment, and respond to each other
- We need to simulate the reactions and information propagation paths of all parties in public opinion events

Therefore, **entities must be real subjects that exist in reality and can voice opinions and interact on social media**:

**Can be**:
- Specific individuals (public figures, persons involved, opinion leaders, experts, ordinary people)
- Companies, enterprises (including their official accounts)
- Organizations (universities, associations, NGOs, unions, etc.)
- Government departments, regulatory bodies
- Media organizations (newspapers, TV stations, self-media, websites)
- Social media platforms themselves
- Representatives of specific groups (e.g., alumni associations, fan clubs, rights protection groups, etc.)

**Cannot be**:
- Abstract concepts (e.g., "public opinion", "emotions", "trends")
- Topics/subjects (e.g., "academic integrity", "education reform")
- Opinions/attitudes (e.g., "supporters", "opponents")
- Physical objects / inanimate things (e.g., "table", "food", "rice", "meal", "dish", "product", "item", "device", "tool", "furniture", "equipment")
- Places / locations (e.g., "park", "building", "city", "region", "country", "area", "facility")
- Events / incidents (e.g., "accident", "ceremony", "festival", "meeting", "conference")
- Animals, plants, or other non-human non-organizational entities

## Output Format

Please output JSON format with the following structure:

```json
{
    "entity_types": [
        {
            "name": "EntityTypeName (English, PascalCase)",
            "description": "Brief description (English, max 100 characters)",
            "attributes": [
                {
                    "name": "attribute_name (English, snake_case)",
                    "type": "text",
                    "description": "Attribute description"
                }
            ],
            "examples": ["Example entity 1", "Example entity 2"]
        }
    ],
    "edge_types": [
        {
            "name": "RELATIONSHIP_TYPE_NAME (English, UPPER_SNAKE_CASE)",
            "description": "Brief description (English, max 100 characters)",
            "source_targets": [
                {"source": "SourceEntityType", "target": "TargetEntityType"}
            ],
            "attributes": []
        }
    ],
    "analysis_summary": "Brief analysis summary of the text content (Korean)"
}
```

## Design Guidelines (EXTREMELY IMPORTANT!)

### 1. Entity Type Design - Must Strictly Follow

**Quantity requirement: Must have exactly 10 entity types**

**Hierarchy requirements (must include both specific types and fallback types)**:

Your 10 entity types must include the following levels:

A. **Fallback types (must include, placed last 2 in the list)**:
   - `Person`: Fallback type for any individual person. When a person doesn't fit any other more specific person type, they belong here.
   - `Organization`: Fallback type for any organization. When an organization doesn't fit any other more specific organization type, it belongs here.

B. **Specific types (8 types, designed based on text content)**:
   - Design more specific types for the main roles appearing in the text
   - Example: If the text involves academic events, you might have `Student`, `Professor`, `University`
   - Example: If the text involves business events, you might have `Company`, `CEO`, `Employee`

**Why fallback types are needed**:
- Various characters appear in texts, such as "primary school teachers", "passersby", "some netizen"
- If no specific type matches, they should be categorized under `Person`
- Similarly, small organizations, temporary groups, etc. should be categorized under `Organization`

**Design principles for specific types**:
- Identify frequently appearing or key role types from the text
- Each specific type should have clear boundaries to avoid overlap
- description must clearly explain the difference between this type and the fallback type

### 2. Relationship Type Design

- Quantity: 6-10
- Relationships should reflect real connections in social media interactions
- Ensure relationship source_targets cover the entity types you define

### 3. Attribute Design

- 1-3 key attributes per entity type
- **Note**: Attribute names cannot use `name`, `uuid`, `group_id`, `created_at`, `summary` (these are system reserved words)
- Recommended: `full_name`, `title`, `role`, `position`, `location`, `description`, etc.

## Entity Type Reference

**Individual types (specific)**:
- Student: Student
- Professor: Professor/Scholar
- Journalist: Reporter
- Celebrity: Celebrity/Influencer
- Executive: Senior executive
- Official: Government official
- Lawyer: Lawyer
- Doctor: Doctor

**Individual types (fallback)**:
- Person: Any individual (used when person doesn't fit above specific types)

**Organization types (specific)**:
- University: Higher education institution
- Company: Company/enterprise
- GovernmentAgency: Government agency
- MediaOutlet: Media organization
- Hospital: Hospital
- School: Primary/secondary school
- NGO: Non-governmental organization

**Organization types (fallback)**:
- Organization: Any organization (used when organization doesn't fit above specific types)

## Relationship Type Reference

- WORKS_FOR: Works for
- STUDIES_AT: Studies at
- AFFILIATED_WITH: Affiliated with
- REPRESENTS: Represents
- REGULATES: Regulates
- REPORTS_ON: Reports on
- COMMENTS_ON: Comments on
- RESPONDS_TO: Responds to
- SUPPORTS: Supports
- OPPOSES: Opposes
- COLLABORATES_WITH: Collaborates with
- COMPETES_WITH: Competes with
"""


class OntologyGenerator:
    """
    온톨로지 생성기
    텍스트 내용을 분석하여 엔티티 및 관계 유형 정의 생성
    """

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm_client = llm_client or LLMClient()

    def generate(
        self,
        document_texts: List[str],
        simulation_requirement: str,
        additional_context: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        온톨로지 정의 생성

        Args:
            document_texts: 문서 텍스트 목록
            simulation_requirement: 시뮬레이션 요구사항 설명
            additional_context: 추가 컨텍스트

        Returns:
            온톨로지 정의 (entity_types, edge_types 등)
        """
        # 사용자 메시지 구성
        user_message = self._build_user_message(
            document_texts,
            simulation_requirement,
            additional_context
        )

        messages = [
            {"role": "system", "content": ONTOLOGY_SYSTEM_PROMPT},
            {"role": "user", "content": user_message}
        ]

        # LLM 호출
        result = self.llm_client.chat_json(
            messages=messages,
            temperature=0.3,
            max_tokens=4096
        )

        # 검증 및 후처리
        result = self._validate_and_process(result)

        return result

    # LLM에 전달할 텍스트 최대 길이 (5만 자)
    MAX_TEXT_LENGTH_FOR_LLM = 50000

    def _build_user_message(
        self,
        document_texts: List[str],
        simulation_requirement: str,
        additional_context: Optional[str]
    ) -> str:
        """사용자 메시지 구성"""

        # 텍스트 합치기
        combined_text = "\n\n---\n\n".join(document_texts)
        original_length = len(combined_text)

        # 텍스트가 5만 자를 초과하면 자르기 (LLM에 전달되는 내용에만 영향, 그래프 구축에는 영향 없음)
        if len(combined_text) > self.MAX_TEXT_LENGTH_FOR_LLM:
            combined_text = combined_text[:self.MAX_TEXT_LENGTH_FOR_LLM]
            combined_text += f"\n\n...(원문 총 {original_length}자, 온톨로지 분석을 위해 앞 {self.MAX_TEXT_LENGTH_FOR_LLM}자를 사용)..."

        message = f"""## 시뮬레이션 요구사항

{simulation_requirement}

## 문서 내용

{combined_text}
"""

        if additional_context:
            message += f"""
## 추가 설명

{additional_context}
"""

        message += """
Please design entity types and relationship types suitable for social public opinion simulation based on the above content.

**Rules that must be followed**:
1. Must output exactly 10 entity types
2. The last 2 must be fallback types: Person (individual fallback) and Organization (organization fallback)
3. The first 8 are specific types designed based on the text content
4. All entity types must be subjects that can voice opinions in reality, not abstract concepts
5. Attribute names cannot use reserved words like name, uuid, group_id; use full_name, org_name, etc. instead
6. NEVER include physical objects, food, places, events, or animals as entity types — these cannot have social media accounts
"""

        return message
    
    # 소셜 미디어 행위자가 될 수 없는 사물/장소/개념 키워드 (소문자)
    INANIMATE_KEYWORDS = {
        # 음식/식품
        "food", "meal", "dish", "rice", "drink", "beverage", "ingredient",
        "cuisine", "snack", "dessert", "bread", "fruit", "vegetable",
        # 가구/사물
        "furniture", "table", "chair", "desk", "bed", "sofa", "shelf",
        "object", "item", "product", "device", "tool", "equipment", "machine",
        "appliance", "vehicle", "car", "bike",
        # 장소/지역
        "place", "location", "area", "region", "city", "town", "village",
        "building", "facility", "park", "school", "hospital", "store",
        "restaurant", "hotel", "site",
        # 사건/이벤트
        "event", "incident", "accident", "ceremony", "festival", "meeting",
        "conference", "occasion",
        # 동식물/자연
        "animal", "plant", "nature", "species", "creature",
        # 추상 개념
        "concept", "idea", "theory", "topic", "issue", "trend", "phenomenon",
    }

    def _is_inanimate_entity_type(self, entity_name: str) -> bool:
        """사물/장소/개념 등 소셜 행위자가 될 수 없는 엔티티 타입인지 확인.

        PascalCase 이름을 단어 단위로 분리하여 매칭하므로 'FoodCompany'처럼
        사물 키워드를 포함하더라도 나머지 단어가 사회적 행위자를 나타내면 허용.
        모든 구성 단어가 사물 키워드일 때만 True 반환.
        """
        import re
        # PascalCase/camelCase → 단어 분리 (예: "FoodCompany" → ["Food", "Company"])
        words = re.sub(r'([a-z])([A-Z])', r'\1 \2', entity_name).split()
        if not words:
            return False
        lower_words = [w.lower() for w in words]
        inanimate_words = [w for w in lower_words if w in self.INANIMATE_KEYWORDS]
        # 사회적 행위자를 나타내는 단어가 하나라도 있으면 허용
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
        # 사물 키워드가 하나라도 있으면 필터
        return len(inanimate_words) > 0

    def _validate_and_process(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """결과 검증 및 후처리"""

        # 필수 필드 존재 확인
        if "entity_types" not in result:
            result["entity_types"] = []
        if "edge_types" not in result:
            result["edge_types"] = []
        if "analysis_summary" not in result:
            result["analysis_summary"] = ""

        # 엔티티 유형 검증
        for entity in result["entity_types"]:
            if "attributes" not in entity:
                entity["attributes"] = []
            if "examples" not in entity:
                entity["examples"] = []
            # description이 100자를 초과하지 않도록 확인
            if len(entity.get("description", "")) > 100:
                entity["description"] = entity["description"][:97] + "..."

        # 관계 유형 검증
        for edge in result["edge_types"]:
            if "source_targets" not in edge:
                edge["source_targets"] = []
            if "attributes" not in edge:
                edge["attributes"] = []
            if len(edge.get("description", "")) > 100:
                edge["description"] = edge["description"][:97] + "..."

        # Zep API 제한: 최대 10개의 커스텀 엔티티 유형, 최대 10개의 커스텀 엣지 유형
        MAX_ENTITY_TYPES = 10
        MAX_EDGE_TYPES = 10

        # 폴백 유형 정의
        person_fallback = {
            "name": "Person",
            "description": "Any individual person not fitting other specific person types.",
            "attributes": [
                {"name": "full_name", "type": "text", "description": "Full name of the person"},
                {"name": "role", "type": "text", "description": "Role or occupation"}
            ],
            "examples": ["ordinary citizen", "anonymous netizen"]
        }

        organization_fallback = {
            "name": "Organization",
            "description": "Any organization not fitting other specific organization types.",
            "attributes": [
                {"name": "org_name", "type": "text", "description": "Name of the organization"},
                {"name": "org_type", "type": "text", "description": "Type of organization"}
            ],
            "examples": ["small business", "community group"]
        }

        # 이미 폴백 유형이 있는지 확인
        entity_names = {e["name"] for e in result["entity_types"]}
        has_person = "Person" in entity_names
        has_organization = "Organization" in entity_names

        # 추가해야 할 폴백 유형
        fallbacks_to_add = []
        if not has_person:
            fallbacks_to_add.append(person_fallback)
        if not has_organization:
            fallbacks_to_add.append(organization_fallback)

        if fallbacks_to_add:
            current_count = len(result["entity_types"])
            needed_slots = len(fallbacks_to_add)

            # 추가 후 10개를 초과하면 기존 유형 일부 제거 필요
            if current_count + needed_slots > MAX_ENTITY_TYPES:
                # 제거할 개수 계산
                to_remove = current_count + needed_slots - MAX_ENTITY_TYPES
                # 끝에서부터 제거 (앞의 더 중요한 구체적 유형 보존)
                result["entity_types"] = result["entity_types"][:-to_remove]

            # 폴백 유형 추가
            result["entity_types"].extend(fallbacks_to_add)

        # 최종적으로 제한을 초과하지 않도록 확인 (방어적 프로그래밍)
        if len(result["entity_types"]) > MAX_ENTITY_TYPES:
            result["entity_types"] = result["entity_types"][:MAX_ENTITY_TYPES]

        if len(result["edge_types"]) > MAX_EDGE_TYPES:
            result["edge_types"] = result["edge_types"][:MAX_EDGE_TYPES]

        return result
    
    def generate_python_code(self, ontology: Dict[str, Any]) -> str:
        """
        온톨로지 정의를 Python 코드로 변환 (ontology.py와 유사)

        Args:
            ontology: 온톨로지 정의

        Returns:
            Python 코드 문자열
        """
        code_lines = [
            '"""',
            '커스텀 엔티티 유형 정의',
            'MiroFish에 의해 자동 생성, 사회 여론 시뮬레이션용',
            '"""',
            '',
            'from pydantic import Field',
            'from zep_cloud.external_clients.ontology import EntityModel, EntityText, EdgeModel',
            '',
            '',
            '# ============== 엔티티 유형 정의 ==============',
            '',
        ]

        # 엔티티 유형 생성
        for entity in ontology.get("entity_types", []):
            name = entity["name"]
            desc = entity.get("description", f"A {name} entity.")

            code_lines.append(f'class {name}(EntityModel):')
            code_lines.append(f'    """{desc}"""')

            attrs = entity.get("attributes", [])
            if attrs:
                for attr in attrs:
                    attr_name = attr["name"]
                    attr_desc = attr.get("description", attr_name)
                    code_lines.append(f'    {attr_name}: EntityText = Field(')
                    code_lines.append(f'        description="{attr_desc}",')
                    code_lines.append(f'        default=None')
                    code_lines.append(f'    )')
            else:
                code_lines.append('    pass')

            code_lines.append('')
            code_lines.append('')

        code_lines.append('# ============== 관계 유형 정의 ==============')
        code_lines.append('')

        # 관계 유형 생성
        for edge in ontology.get("edge_types", []):
            name = edge["name"]
            # PascalCase 클래스명으로 변환
            class_name = ''.join(word.capitalize() for word in name.split('_'))
            desc = edge.get("description", f"A {name} relationship.")

            code_lines.append(f'class {class_name}(EdgeModel):')
            code_lines.append(f'    """{desc}"""')

            attrs = edge.get("attributes", [])
            if attrs:
                for attr in attrs:
                    attr_name = attr["name"]
                    attr_desc = attr.get("description", attr_name)
                    code_lines.append(f'    {attr_name}: EntityText = Field(')
                    code_lines.append(f'        description="{attr_desc}",')
                    code_lines.append(f'        default=None')
                    code_lines.append(f'    )')
            else:
                code_lines.append('    pass')

            code_lines.append('')
            code_lines.append('')

        # 유형 딕셔너리 생성
        code_lines.append('# ============== 유형 설정 ==============')
        code_lines.append('')
        code_lines.append('ENTITY_TYPES = {')
        for entity in ontology.get("entity_types", []):
            name = entity["name"]
            code_lines.append(f'    "{name}": {name},')
        code_lines.append('}')
        code_lines.append('')
        code_lines.append('EDGE_TYPES = {')
        for edge in ontology.get("edge_types", []):
            name = edge["name"]
            class_name = ''.join(word.capitalize() for word in name.split('_'))
            code_lines.append(f'    "{name}": {class_name},')
        code_lines.append('}')
        code_lines.append('')

        # 엣지의 source_targets 매핑 생성
        code_lines.append('EDGE_SOURCE_TARGETS = {')
        for edge in ontology.get("edge_types", []):
            name = edge["name"]
            source_targets = edge.get("source_targets", [])
            if source_targets:
                st_list = ', '.join([
                    f'{{"source": "{st.get("source", "Entity")}", "target": "{st.get("target", "Entity")}"}}'
                    for st in source_targets
                ])
                code_lines.append(f'    "{name}": [{st_list}],')
        code_lines.append('}')

        return '\n'.join(code_lines)

