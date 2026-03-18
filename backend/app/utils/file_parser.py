"""
파일 파싱 도구
PDF, Markdown, TXT 파일의 텍스트 추출 지원
"""

import os
from pathlib import Path
from typing import List, Optional


def _read_text_with_fallback(file_path: str) -> str:
    """
    텍스트 파일 읽기, UTF-8 실패 시 자동으로 인코딩 감지.

    다단계 폴백 전략 채택:
    1. 먼저 UTF-8 디코딩 시도
    2. charset_normalizer를 사용하여 인코딩 감지
    3. chardet를 사용하여 인코딩 감지로 폴백
    4. 최종적으로 UTF-8 + errors='replace'로 대체

    Args:
        file_path: 파일 경로

    Returns:
        디코딩된 텍스트 내용
    """
    data = Path(file_path).read_bytes()

    # 먼저 UTF-8 시도
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        pass

    # charset_normalizer를 사용하여 인코딩 감지 시도
    encoding = None
    try:
        from charset_normalizer import from_bytes
        best = from_bytes(data).best()
        if best and best.encoding:
            encoding = best.encoding
    except Exception:
        pass

    # chardet로 폴백
    if not encoding:
        try:
            import chardet
            result = chardet.detect(data)
            encoding = result.get('encoding') if result else None
        except Exception:
            pass

    # 최종 대체: UTF-8 + replace
    if not encoding:
        encoding = 'utf-8'

    return data.decode(encoding, errors='replace')


class FileParser:
    """파일 파서"""

    SUPPORTED_EXTENSIONS = {'.pdf', '.md', '.markdown', '.txt'}

    @classmethod
    def extract_text(cls, file_path: str) -> str:
        """
        파일에서 텍스트 추출

        Args:
            file_path: 파일 경로

        Returns:
            추출된 텍스트 내용
        """
        path = Path(file_path)

        if not path.exists():
            raise FileNotFoundError(f"파일이 존재하지 않습니다: {file_path}")

        suffix = path.suffix.lower()

        if suffix not in cls.SUPPORTED_EXTENSIONS:
            raise ValueError(f"지원하지 않는 파일 형식: {suffix}")

        if suffix == '.pdf':
            return cls._extract_from_pdf(file_path)
        elif suffix in {'.md', '.markdown'}:
            return cls._extract_from_md(file_path)
        elif suffix == '.txt':
            return cls._extract_from_txt(file_path)

        raise ValueError(f"처리할 수 없는 파일 형식: {suffix}")

    @staticmethod
    def _extract_from_pdf(file_path: str) -> str:
        """PDF에서 텍스트 추출"""
        try:
            import fitz  # PyMuPDF
        except ImportError:
            raise ImportError("PyMuPDF 설치 필요: pip install PyMuPDF")

        text_parts = []
        with fitz.open(file_path) as doc:
            for page in doc:
                text = page.get_text()
                if text.strip():
                    text_parts.append(text)

        return "\n\n".join(text_parts)

    @staticmethod
    def _extract_from_md(file_path: str) -> str:
        """Markdown에서 텍스트 추출, 자동 인코딩 감지 지원"""
        return _read_text_with_fallback(file_path)

    @staticmethod
    def _extract_from_txt(file_path: str) -> str:
        """TXT에서 텍스트 추출, 자동 인코딩 감지 지원"""
        return _read_text_with_fallback(file_path)

    @classmethod
    def extract_from_multiple(cls, file_paths: List[str]) -> str:
        """
        여러 파일에서 텍스트 추출 및 병합

        Args:
            file_paths: 파일 경로 목록

        Returns:
            병합된 텍스트
        """
        all_texts = []

        for i, file_path in enumerate(file_paths, 1):
            try:
                text = cls.extract_text(file_path)
                filename = Path(file_path).name
                all_texts.append(f"=== 문서 {i}: {filename} ===\n{text}")
            except Exception as e:
                all_texts.append(f"=== 문서 {i}: {file_path} (추출 실패: {str(e)}) ===")

        return "\n\n".join(all_texts)


def split_text_into_chunks(
    text: str,
    chunk_size: int = 500,
    overlap: int = 50
) -> List[str]:
    """
    텍스트를 작은 청크로 분할

    Args:
        text: 원본 텍스트
        chunk_size: 청크당 문자 수
        overlap: 오버랩 문자 수

    Returns:
        텍스트 청크 목록
    """
    if len(text) <= chunk_size:
        return [text] if text.strip() else []

    chunks = []
    start = 0

    while start < len(text):
        end = start + chunk_size

        # 문장 경계에서 분할 시도
        if end < len(text):
            # 가장 가까운 문장 종결 부호 검색
            for sep in ['。', '！', '？', '.\n', '!\n', '?\n', '\n\n', '. ', '! ', '? ']:
                last_sep = text[start:end].rfind(sep)
                if last_sep != -1 and last_sep > chunk_size * 0.3:
                    end = start + last_sep + len(sep)
                    break

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        # 다음 청크는 오버랩 위치에서 시작
        start = end - overlap if end < len(text) else len(text)

    return chunks
