import service, { requestWithRetry } from './index'

/**
 * 온톨로지 생성 (문서 및 시뮬레이션 요구사항 업로드)
 * @param {Object} data - files, simulation_requirement, project_name 등 포함
 * @returns {Promise}
 */
export function generateOntology(formData) {
  return requestWithRetry(() => 
    service({
      url: '/api/graph/ontology/generate',
      method: 'post',
      data: formData,
      headers: {
        'Content-Type': 'multipart/form-data'
      }
    })
  )
}

/**
 * 그래프 구축
 * @param {Object} data - project_id, graph_name 등 포함
 * @returns {Promise}
 */
export function buildGraph(data) {
  return requestWithRetry(() =>
    service({
      url: '/api/graph/build',
      method: 'post',
      data
    })
  )
}

/**
 * 작업 상태 조회
 * @param {String} taskId - 작업 ID
 * @returns {Promise}
 */
export function getTaskStatus(taskId) {
  return service({
    url: `/api/graph/task/${taskId}`,
    method: 'get'
  })
}

/**
 * 그래프 데이터 조회
 * @param {String} graphId - 그래프 ID
 * @returns {Promise}
 */
export function getGraphData(graphId) {
  return service({
    url: `/api/graph/data/${graphId}`,
    method: 'get'
  })
}

/**
 * 프로젝트 정보 조회
 * @param {String} projectId - 프로젝트 ID
 * @returns {Promise}
 */
export function getProject(projectId) {
  return service({
    url: `/api/graph/project/${projectId}`,
    method: 'get'
  })
}
