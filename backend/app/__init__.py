"""
MiroFish Backend - Flask 앱 팩토리
"""

import os
import warnings

# multiprocessing resource_tracker 경고 억제 (transformers 등 서드파티 라이브러리에서 발생)
# 다른 모든 임포트 전에 설정 필요
warnings.filterwarnings("ignore", message=".*resource_tracker.*")

from flask import Flask, request
from flask_cors import CORS

from .config import Config
from .utils.logger import setup_logger, get_logger


def create_app(config_class=Config):
    """Flask 앱 팩토리 함수"""
    app = Flask(__name__)
    app.config.from_object(config_class)

    # JSON 인코딩 설정: 한글이 직접 표시되도록 함 (\uXXXX 형식 대신)
    # Flask >= 2.3은 app.json.ensure_ascii 사용, 구버전은 JSON_AS_ASCII 설정 사용
    if hasattr(app, 'json') and hasattr(app.json, 'ensure_ascii'):
        app.json.ensure_ascii = False

    # 로그 설정
    logger = setup_logger('mirofish')

    # reloader 자식 프로세스에서만 시작 정보 출력 (debug 모드에서 두 번 출력 방지)
    is_reloader_process = os.environ.get('WERKZEUG_RUN_MAIN') == 'true'
    debug_mode = app.config.get('DEBUG', False)
    should_log_startup = not debug_mode or is_reloader_process

    if should_log_startup:
        logger.info("=" * 50)
        logger.info("MiroFish Backend 시작 중...")
        logger.info("=" * 50)

    # CORS 활성화
    CORS(app, resources={r"/api/*": {"origins": "*"}})

    # 시뮬레이션 프로세스 정리 함수 등록 (서버 종료 시 모든 시뮬레이션 프로세스 종료 보장)
    from .services.simulation_runner import SimulationRunner
    SimulationRunner.register_cleanup()
    if should_log_startup:
        logger.info("시뮬레이션 프로세스 정리 함수 등록 완료")

    # 요청 로그 미들웨어
    @app.before_request
    def log_request():
        logger = get_logger('mirofish.request')
        logger.debug(f"요청: {request.method} {request.path}")
        if request.content_type and 'json' in request.content_type:
            logger.debug(f"요청 본문: {request.get_json(silent=True)}")

    @app.after_request
    def log_response(response):
        logger = get_logger('mirofish.request')
        logger.debug(f"응답: {response.status_code}")
        return response

    # 블루프린트 등록
    from .api import graph_bp, simulation_bp, report_bp
    app.register_blueprint(graph_bp, url_prefix='/api/graph')
    app.register_blueprint(simulation_bp, url_prefix='/api/simulation')
    app.register_blueprint(report_bp, url_prefix='/api/report')

    # 헬스 체크
    @app.route('/health')
    def health():
        return {'status': 'ok', 'service': 'MiroFish Backend'}

    if should_log_startup:
        logger.info("MiroFish Backend 시작 완료")

    return app

