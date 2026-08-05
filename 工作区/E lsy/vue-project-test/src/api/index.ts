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

// 3. 获取特定节点的人流密度预测
export const getPredictionDataAPI = (nodeId: string) => {
  return request({
    url: '/api/v1/monitor/prediction',
    method: 'get',
    params: { nodeId } 
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

/**
 * ==========================================
 * 4. 预警与应急响应[cite: 17]
 * ==========================================
 */

// 4. 获取实时安全预警信息 (★ 左侧异常拥堵数据从这里拿)
export const getAlertsAPI = () => {
  return request({
    url: '/api/v1/security/alerts',
    method: 'get'
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