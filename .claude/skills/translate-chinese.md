---
name: translate-chinese
description: 프로젝트 내 소스 파일에서 중국어(한자)가 포함된 텍스트를 한국어로 번역할 때 사용합니다. 코드 주석·UI 문자열·LLM 프롬프트·문서 등에 남아있는 중국어를 체계적으로 찾아 번역하고 검증하는 전체 워크플로를 안내합니다.
---

# 중국어 → 한국어 번역 스킬

## 번역 원칙

| 대상 | 처리 방법 |
|---|---|
| 코드 주석 (`#`, `//`, `/* */`, docstring) | 한국어로 번역 |
| UI 텍스트, 버튼 레이블, 에러 메시지, 로그 | 한국어로 번역 |
| LLM에 전달되는 프롬프트 문자열 | **영어**로 번역 (CLAUDE.md 규칙) |
| 정규식 패턴, 딕셔너리 키, 변수명, 파일 경로 | **변경하지 않음** (코드 로직) |
| 서버-프론트 공유 상태값 문자열 | 백엔드·프론트 **동시** 확인 후 변경 |
| LLM 출력 파싱 패턴 (regex) | LLM 프롬프트와 함께 **쌍으로** 변경 |

---

## 1단계: 중국어 포함 파일 스캔

다음 Python 스크립트를 실행하여 중국어가 남아있는 파일과 라인 수를 파악합니다.

```python
import os, re

TARGET_EXTS = {'.py', '.js', '.ts', '.tsx', '.jsx', '.html', '.vue',
               '.svelte', '.md', '.txt', '.yaml', '.yml', '.toml',
               '.ini', '.cfg', '.example', '.gitignore'}
EXCLUDE_DIRS = {'node_modules', '.git', 'static', 'dist', 'build',
                '__pycache__', '.venv', 'venv', 'env', 'migrations'}

pattern = re.compile(r'[\u4e00-\u9fff]')
results = {}

for root, dirs, files in os.walk('.'):
    dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
    for fname in files:
        ext = os.path.splitext(fname)[1].lower()
        if ext not in TARGET_EXTS:
            continue
        fpath = os.path.join(root, fname)
        try:
            with open(fpath, encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
        except Exception:
            continue
        hits = [(i+1, line.rstrip()) for i, line in enumerate(lines) if pattern.search(line)]
        if hits:
            results[fpath] = hits

if not results:
    print("중국어가 포함된 파일이 없습니다.")
else:
    total = sum(len(v) for v in results.values())
    print(f"총 {len(results)}개 파일, {total}줄에 중국어 포함\n")
    for fpath, hits in sorted(results.items(), key=lambda x: -len(x[1])):
        print(f"[{len(hits):3d}줄] {fpath}")
        for lineno, text in hits:
            print(f"  {lineno:4d}: {text}")
        print()
```

스크립트를 실행하는 방법:
```bash
python3 /tmp/scan_chinese.py
# 또는
uv python /tmp/scan_chinese.py
```

---

## 2단계: 파일별 번역 작업

스캔 결과에서 중국어가 많은 파일부터 우선 처리합니다. 각 파일에 대해:

1. **Read 도구**로 파일 전체를 읽어 컨텍스트를 파악합니다.
2. 각 중국어 라인의 성격을 분류합니다:
   - 단순 주석/docstring → 한국어 번역
   - UI 문자열/로그 → 한국어 번역
   - LLM 프롬프트 내부 → 영어 번역
   - 기능과 연결된 값 → 양쪽 동시 변경 필요
3. **Edit 도구**로 수정합니다 (들여쓰기·따옴표 형식 완전히 유지).

### 병렬 처리 팁

파일이 많을 때는 Agent 도구로 여러 에이전트를 병렬 실행합니다:
- 에이전트당 3~7개 파일 할당
- 백엔드/프론트엔드/문서 카테고리별로 분리하면 효율적

### 특수 케이스 처리

**LLM 출력 파싱 패턴 (프론트엔드):**
- LLM 프롬프트에서 출력 형식을 지정하는 구조(예: `"분석 질문: {query}"`)와
  프론트엔드에서 그 형식을 파싱하는 정규식(예: `/분석 질문:\s*(.+)/`)은 **반드시 쌍으로 변경**
- 백엔드 서비스의 `to_text()` 메서드에서 실제 출력 포맷을 확인한 후 프론트엔드 정규식을 맞춤

**서버-프론트 공유 상태값:**
```python
# 백엔드 예시
progress_callback("generating_profiles")  # 영어 상수 사용 권장
```
```javascript
// 프론트엔드 예시 (중국어 제거 후 영어만)
if (newStage === 'generating_profiles') { ... }
```

---

## 3단계: 번역 완료 검증

번역 후 1단계 스크립트를 재실행하거나 아래 간단 검증을 사용합니다.

### 곡선 따옴표 검사 (Vue/JS 필수)

번역 과정에서 직선 따옴표(`'`)가 곡선 따옴표(`'` U+2018/U+2019)로 바뀌는 경우가 있습니다.
`:class`, `:style`, `{{ }}` 등 **Vue/JS 표현식 안**에 들어가면 컴파일 오류가 발생합니다.

```python
import os, re

CURLY = re.compile(r'[\u2018\u2019\u201c\u201d]')
root = '.'
for dirpath, dirs, files in os.walk(root):
    dirs[:] = [d for d in dirs if d not in {'node_modules', '.git', 'dist'}]
    for fname in files:
        if not any(fname.endswith(e) for e in ['.vue', '.js', '.ts']):
            continue
        fpath = os.path.join(dirpath, fname)
        try:
            with open(fpath, encoding='utf-8') as f:
                for i, line in enumerate(f, 1):
                    if CURLY.search(line):
                        print(f'{fpath}:{i}: {line.rstrip()}')
        except Exception:
            pass
```

곡선 따옴표가 발견되면 해당 파일 전체를 일괄 교체합니다:

```python
fpath = '해당파일.vue'
with open(fpath, encoding='utf-8') as f:
    content = f.read()
content = content.replace('\u2018', "'").replace('\u2019', "'")
content = content.replace('\u201c', '"').replace('\u201d', '"')
with open(fpath, 'w', encoding='utf-8') as f:
    f.write(content)
```

> HTML 텍스트 내 한국어 인용 표시(`'커스텀 모드'`, `"예시"`)도 직선 따옴표로 바뀌지만,
> 기능에는 영향 없으므로 그대로 두어도 됩니다.

### 중국어 잔존 검사

```python
import os, re

pattern = re.compile(r'[\u4e00-\u9fff]')
EXCLUDE_DIRS = {'node_modules', '.git', 'static', 'dist', '__pycache__', '.venv', 'venv'}
TARGET_EXTS = {'.py', '.js', '.ts', '.vue', '.md', '.txt', '.toml', '.yml', '.yaml', '.example'}

remaining = []
for root, dirs, files in os.walk('.'):
    dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
    for fname in files:
        if os.path.splitext(fname)[1].lower() in TARGET_EXTS:
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, encoding='utf-8', errors='ignore') as f:
                    for i, line in enumerate(f, 1):
                        if pattern.search(line):
                            remaining.append(f"{fpath}:{i}: {line.rstrip()}")
            except Exception:
                pass

if not remaining:
    print("✓ 번역 완료 - 중국어 없음")
else:
    print(f"남은 중국어 {len(remaining)}줄:")
    for r in remaining:
        print(" ", r)
```

남아있는 중국어가 모두 **기능적으로 필요한 데이터**(예: 중국어 입력 처리용 딕셔너리 키, 이미지 파일 경로)인 경우 완료로 간주합니다.

---

## 빠른 참고: 자주 나오는 번역 패턴

| 중국어 패턴 | 처리 |
|---|---|
| `# 注释` / `"""独스트링"""` | 한국어 주석/독스트링 |
| `print("提示")` / `logger.info("消息")` | 한국어 문자열 |
| `"正在加载..."` (UI) | `"불러오는 중..."` |
| `"处理中"` (상태값) | 백엔드-프론트 동시 확인 |
| `"男": "male"` (데이터 딕셔너리) | 변경 안 함 (기능 필수) |
| `/分析问题:\s*/` (LLM 출력 파싱 regex) | LLM 프롬프트 변경 시 함께 변경 |
| `src="中文파일명.png"` (파일 경로) | 변경 안 함 |
