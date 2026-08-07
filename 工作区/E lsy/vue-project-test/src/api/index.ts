// src/api/index.ts
import request from '../utils/request'

/**
 * ==========================================
 * 1. 管理员系统模块
 * ==========================================
 */

// 管理员注册
export const registerAPI = (data: any) => {
  return request({
    url: '/api/v1/admin/register',
    method: 'post',
    data // 包含 username 和 password
  })
}

// 管理员登录
export const loginAPI = (data: any) => {
  return request({
    url: '/api/v1/admin/login',
    method: 'post',
    data // 包含 username 和 password
  })
}

// 获取管理员信息
export const getAdminInfoAPI = (username: string) => {
  return request({
    url: '/api/v1/admin/info',
    method: 'get',
    params: { username } // Query 参数
  })
}

/**
 * ==========================================
 * 2. 宏观交通与人流监控[cite: 17]
 * ==========================================
 */

// 1. 获取实时人流热力图 (假设后端这个没变)
export const getHeatmapDataAPI = (time: string = 'now') => {
  return request({
    url: '/api/v1/monitor/heatmap',
    method: 'get',
    params: { time } 
  })
}

// 2. 获取路网节点状态 (★ 修改为后端给的真实路径 network/nodes)
export const getNodesDataAPI = () => {
  return request({
    url: '/api/v1/network/nodes', 
    method: 'get'
  })
}

// ★ 历史热力图查询：GET /api/v1/network/history?timestamp=YYYY-MM-DD HH:MM:SS
// 后端按 timestamp 就近匹配最近帧（tick=精确匹配，查不到会 404「该帧不存在」）
// 返回 data.tick / data.timestamp / data.nodes[]（61节点快照，nodeId 与 topology.json 一致）
export const getHistoryHeatmapAPI = (timestamp: string) => {
  return request({
    url: '/api/v1/network/history',
    method: 'get',
    params: { timestamp }
  })
}

// 3. 获取特定节点的人流密度预测
export const getPredictionDataAPI = (nodeId: string) => {
  return request({
    url: '/api/v1/monitor/prediction',
    method: 'get',
    params: { nodeId } 
  })
}

// ★ 新增：获取全网节点未来预测密度（时间.docx：GET /api/v1/network/predictions）
// ?limit=N 可取最近 N 条历史快照，用于画节点密度趋势曲线
export const getNetworkPredictionsAPI = (limit?: number) => {
  return request({
    url: '/api/v1/network/predictions',
    method: 'get',
    params: limit ? { limit } : {}
  })
}

/**
 * ==========================================
 * 3. 微观车联网与准入管理[cite: 17]
 * ==========================================
 */

// 车辆准入判断与路径规划
export const getVehicleRoutingAPI = (carId: string) => {
  return request({
    url: '/api/v1/vehicle/routing',
    method: 'get',
    params: { carId } // Query 参数[cite: 17]
  })
}

// 触发随机人、车数据生成 (用于配合宏微观模拟环境)
export const triggerSimulationAPI = (personCount: number = 0, carCount: number = 0) => {
  return request({
    url: '/api/simulation/random',
    method: 'post',
    data: { personCount, carCount } // 请求体参数[cite: 17]
  })
}

// ★ CAV 小车编队演示：GET /api/v1/vehicle/cav-formation
// 返回 data.path{startNodeId, endNodeId, routeNodes} + data.cavFleet[]（4辆车 speed/acceleration/distanceToFront）
export const getCavFormationAPI = (startNodeId: string, endNodeId: string) => {
  return request({
    url: '/api/v1/vehicle/cav-formation',
    method: 'get',
    params: { startNodeId, endNodeId }
  })
}

/**
 * ==========================================
 * 4. 预警与应急响应[cite: 17]
 * ==========================================
 */

// 4. 获取实时安全预警信息 (增加 params 支持自定义获取条数和状态)
export const getAlertsAPI = (params?: any) => {
  return request({
    url: '/api/v1/security/alerts',
    method: 'get',
    params
  })
}

// ★ 新增：更新预警状态为已解决
export const resolveAlertAPI = (alertId: string, actionTaken: string) => {
  return request({
    url: `/api/v1/security/alerts/${alertId}/resolve`,
    method: 'put',
    data: { actionTaken } 
  })
}

// 动态调节/控制门闸
export const controlGateAPI = (gateId: string, action: string) => {
  return request({
    url: '/api/v1/gate/control',
    method: 'post',
    data: { gateId, action } // 请求体参数[cite: 17]
  })
}

/**
 * ==========================================
 * 5. 交通拓扑高级分析与路径规划
 * ==========================================
 */

// 获取路网节点高级热度指标 (PageRank, 中介中心性, 吸引力)
export const getHotnessDataAPI = (limit?: number) => {
  return request({
    url: '/api/v1/network/hotness',
    method: 'get',
    params: { limit }
  })
}

// 获取两点间的最短路径 (Dijkstra)
export const getShortestPathAPI = (src: string, dst: string) => {
  return request({
    url: '/api/v1/network/shortest-path',
    method: 'get',
    params: { src, dst }
  })
}

// 获取热点区域分析结果 (AttractRank)
export const getHotspotsAPI = () => {
  return request({
    url: '/api/v1/network/hotspots',
    method: 'get'
  })
}