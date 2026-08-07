<template>
  <div class="dashboard-layout">
    <!-- 顶部导航 -->
    <header class="top-header">
      <h1 class="glow-text">园区安全智能调控大屏</h1>
      <div class="mode-switch">
        <button :class="{ active: currentMode === 'heatmap' }" @click="switchMode('heatmap')">
          🔥 实时热力与拥堵 (3D实景)
        </button>
        <button :class="{ active: currentMode === 'topology' }" @click="switchMode('topology')">
          🕸️ 交通枢纽分析 (网络计算)
        </button>
        <button :class="{ active: currentMode === 'history' }" @click="switchMode('history')">
          🕰️ 历史热力图 (回放)
        </button>
        <button :class="{ active: currentMode === 'cav' }" @click="switchMode('cav')">
          🚗 CAV 编队演示
        </button>
      </div>
    </header>

    <div class="main-body">
      <!-- 左侧动态面板 (防乱码，完美高亮)；CAV 模式隐藏，只留路线居中 -->
      <aside v-show="currentMode !== 'cav'" class="left-panel">
        <div v-if="currentMode === 'heatmap'" class="panel-box">
          <h3 class="panel-title">⚠️ 实时安全异常预警</h3>
          <ul class="alert-list">
            <li 
              v-for="(alert, index) in alertList" 
              :key="index" 
              class="alert-item"
              :style="{ 
                borderLeftColor: getAlertColor(alert.riskLevel),
                backgroundColor: getAlertBgColor(alert.riskLevel)
              }"
            >
              <div class="alert-content-wrapper">
                <!-- ===== OPENCODE-EDIT-15 预警信息分行展示（地点/密度/预计持续到） ===== -->
                <div class="alert-content" :style="{ color: getAlertColor(alert.riskLevel) }">
                  <div class="alert-head">
                    <strong>[{{ getAlertLabel(alert.riskLevel) }}]</strong>
                    <button
                      v-if="alert.status === 'pending' || !alert.status"
                      class="cyber-btn small-btn"
                      @click="handleResolveAlert(alert.alertId)"
                    >
                      处理
                    </button>
                  </div>
                  <div class="alert-line">📍 地点：{{ getAlertInfo(alert).nodeName }}</div>
                  <div class="alert-line">👥 密度：{{ getAlertInfo(alert).density }}</div>
                  <div class="alert-line">⏱ 预计持续到：{{ getAlertInfo(alert).endText }}</div>
                </div>
                <!-- ===== OPENCODE-EDIT-15 END ===== -->
              </div>
            </li>
            <li v-if="alertList.length === 0" class="empty-text">✅ 当前园区畅通，无待处理异常事件</li>
          </ul>
        </div>

        <div v-else-if="currentMode === 'topology'" class="panel-box topology-menu">
          <h3 class="panel-title">🧮 枢纽算法选择</h3>
          <ul class="nav-menu">
            <li :class="{ active: algoType === 'pagerank' }" @click="changeAlgo('pagerank')">
              <span class="tab-text">🌐 PageRank<br><small>网页等级算法</small></span>
            </li>
            <li :class="{ active: algoType === 'betweenness' }" @click="changeAlgo('betweenness')">
              <span class="tab-text">🔀 Betweenness<br><small>中介中心性</small></span>
            </li>
            <li :class="{ active: algoType === 'heatScore' }" @click="changeAlgo('heatScore')">
              <span class="tab-text">🔥 AttractRank<br><small>热点吸引力</small></span>
            </li>
          </ul>
        </div>

        <!-- ===== OPENCODE-EDIT-16 历史热力图回放控制面板（老虎机滚轮选择器） ===== -->
        <div v-else-if="currentMode === 'history'" class="panel-box history-panel">
          <h3 class="panel-title">🕰️ 历史热力图回放</h3>
          <p class="subtitle">滚轮选择 日期 · 时刻（每 10s 一帧）</p>

          <!-- 三列滚轮：日 | 时 | 秒档 -->
          <div class="reel-group">
            <div class="reel-col">
              <p class="reel-label">日</p>
              <div
                class="wheel-container"
                @wheel.prevent="spinReel('day', $event)"
                @click="wheelJump('day', $event)"
              >
                <div class="select-highlight"></div>
                <div class="wheel-track" :style="{ transform: `translateY(${-selectedDay * REEL_H_DAY + REEL_H_DAY}px)` }">
                  <div
                    v-for="(d, i) in HISTORY_DAYS"
                    :key="d.week"
                    class="wheel-item wheel-item-day"
                    :class="{ active: i === selectedDay }"
                    :data-idx="i"
                    :style="{ height: REEL_H_DAY + 'px' }"
                  >
                    <b>{{ d.week }}</b><small>{{ d.date }}</small>
                  </div>
                </div>
              </div>
            </div>

            <div class="reel-col">
              <p class="reel-label">时</p>
              <div
                class="wheel-container"
                @wheel.prevent="spinReel('hour', $event)"
                @click="wheelJump('hour', $event)"
              >
                <div class="select-highlight"></div>
                <div class="wheel-track" :style="{ transform: `translateY(${-(selectedHour - 6) * REEL_H + REEL_H}px)` }">
                  <div
                    v-for="(h, i) in HISTORY_HOURS"
                    :key="h"
                    class="wheel-item"
                    :class="{ active: h === selectedHour }"
                    :data-idx="i"
                    :style="{ height: REEL_H + 'px' }"
                  >
                    {{ String(h).padStart(2, '0') }}
                  </div>
                </div>
              </div>
            </div>

            <div class="reel-col">
              <p class="reel-label">秒档</p>
              <div
                class="wheel-container"
                @wheel.prevent="spinReel('sec', $event)"
                @click="wheelJump('sec', $event)"
              >
                <div class="select-highlight"></div>
                <div class="wheel-track" :style="{ transform: `translateY(${-selectedSecIndex * REEL_H + REEL_H}px)` }">
                  <div
                    v-for="(s, i) in SEC_SLOTS"
                    :key="s"
                    class="wheel-item"
                    :class="{ active: i === selectedSecIndex }"
                    :data-idx="i"
                    :style="{ height: REEL_H + 'px' }"
                  >
                    {{ String(s).padStart(2, '0') }}
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- 结果文本 + 操作按钮 -->
          <div class="reel-result">{{ resultText }}</div>
          <div class="btn-row">
            <button class="cyber-btn flex-btn" @click="loadHistorySnapshot" :disabled="histLoading">
              🔍 查询该时刻
            </button>
            <button class="cyber-btn ghost flex-btn" @click="togglePlay" :disabled="histLoading">
              {{ isPlaying ? '⏸ 暂停' : '▶ 回放' }}
            </button>
          </div>

          <!-- ===== OPENCODE-EDIT-18 显示后端实际命中帧（具体日期）+ 就近匹配提示 ===== -->
          <div class="hist-info">
            <div class="hist-row"><span>实际帧时间</span><b class="hist-ts">{{ histTimestamp || '--' }}</b></div>
            <div class="hist-row"><span>命中帧 tick</span><b>{{ histTick ?? '--' }}</b></div>
          </div>
          <div v-if="histMismatch" class="hist-status warn">
            ⚠ 所选日期暂无数据，已就近匹配到 {{ (histTimestamp || '').slice(0, 10) }} 的最近帧
          </div>
          <!-- ===== OPENCODE-EDIT-18 END ===== -->

          <div v-if="histLoading" class="hist-status">⏳ 正在加载该帧快照...</div>
          <div v-else-if="histError" class="hist-status error">{{ histError }}</div>
          <div v-else class="hist-status">✅ 已载入该帧全部节点密度</div>
        </div>
        <!-- ===== OPENCODE-EDIT-16 END ===== -->
      </aside>

      <!-- 中间渲染区域 (ECharts 与 百度3D地图 容器分离) -->
      <main class="center-panel">
        <div v-show="currentMode === 'topology' || currentMode === 'cav'" ref="chartRef" class="chart-container"></div>
        <div v-show="currentMode === 'heatmap' || currentMode === 'history'" id="bmap-container" class="chart-container"></div>



        <!-- ===== OPENCODE-EDIT-10 连接状态指示器（悬浮右上角） ===== -->
        <div class="status-indicator map-float-status" :class="{ offline: dataStatus === 'offline' }">
          <span class="status-dot"></span>
          <span class="status-text">{{ dataStatus === 'online' ? '实时连接' : '连接中断' }}</span>
        </div>
        <!-- ===== OPENCODE-EDIT-10 END ===== -->

        <!-- ===== OPENCODE-EDIT-11 仿真时间显示（悬浮左上角，来源 /api/v1/network/nodes 的 data.simTime） ===== -->
        <!-- ===== OPENCODE-EDIT-17 历史模式隐藏（非实时，避免误导） ===== -->
        <div v-show="currentMode !== 'history'" class="map-float-time" :class="{ offline: !simTime }">
          <div class="time-top">
            <span class="time-label">仿真时间 · SIM TIME</span>
            <span class="time-date">{{ simDate }}</span>
          </div>
          <div class="time-clock">{{ simClock }}</div>
        </div>
        <!-- ===== OPENCODE-EDIT-11 END ===== -->

        <!-- ===== OPENCODE-EDIT-10 热力图图例（悬浮左下角） ===== -->
        <div v-show="currentMode === 'heatmap' || currentMode === 'history'" class="map-legend">
          <div class="legend-title">人流密度</div>
          <div class="legend-item"><span class="legend-color" style="background:#2ED573"></span>畅通（&lt; 0.5）</div>
          <div class="legend-item"><span class="legend-color" style="background:#FF8C00"></span>拥堵（0.5 ~ 0.8）</div>
          <div class="legend-item"><span class="legend-color" style="background:#FF0000"></span>严重（≥ 0.8）</div>
          <!-- ===== OPENCODE-EDIT-18 点击柱体/建筑名可查看预测曲线提示 ===== -->
          <div class="legend-tip">💡 点击柱体或建筑名可查看该点预测曲线</div>
          <!-- ===== OPENCODE-EDIT-18 END ===== -->
        </div>
        <!-- ===== OPENCODE-EDIT-10 END ===== -->
      </main>

      <!-- 右侧面板 (完美分割，双轨排行榜，流光雷达) -->
      <aside v-if="currentMode === 'topology'" class="right-panel">
        <div class="panel-box ranking-box">
          <h3 class="panel-title">🏆 Top 5 核心枢纽排行榜</h3>
          <p class="subtitle">基于 {{ algoNameMap[algoType] }} 算法计算</p>
          
          <div class="ranking-list">
            <div v-for="(node, index) in top5Hubs" :key="node.nodeId" class="rank-item">
              <div class="rank-info">
                <span class="rank-num" :class="'top-' + (index + 1)">{{ index + 1 }}</span>
                <span class="rank-name">{{ node.nodeName || node.nodeId }}</span>
                <span class="rank-score">{{ Number(node[algoType] || node.heatScore).toFixed(2) }}</span>
              </div>
              <div class="bar-track">
                <div class="bar-fill" :style="{ width: ((node[algoType] || node.heatScore) / maxAlgoValue) * 100 + '%' }"></div>
              </div>
            </div>
          </div>
        </div>

        <div class="panel-box hud-card-box">
          <div class="bracket top-left"></div>
          <div class="bracket bottom-right"></div>
          
          <div class="laser-track">
            <span class="laser top"></span>
            <span class="laser right"></span>
            <span class="laser bottom"></span>
            <span class="laser left"></span>
          </div>

          <div class="hud-content-inner">
            <h3 class="hud-title-inline">Dijkstra 最短路径雷达</h3>
            <div class="path-controls-vertical">
              <div class="select-row">
                <select v-model="pathSrc" class="cyber-select full-select">
                  <option value="" disabled>起点节点</option>
                  <option v-for="node in topologyNodes" :key="node.id" :value="node.id">{{ node.name }}</option>
                </select>
                <span class="arrow-v">➔</span>
                <select v-model="pathDst" class="cyber-select full-select">
                  <option value="" disabled>终点节点</option>
                  <option v-for="node in topologyNodes" :key="node.id" :value="node.id">{{ node.name }}</option>
                </select>
              </div>
              <div class="btn-row">
                <button class="cyber-btn flex-btn" @click="calculatePath">高亮路径</button>
                <button class="cyber-btn ghost flex-btn" @click="clearPath">清 除</button>
              </div>
            </div>
            <div v-if="travelTime > 0" class="time-display-inline">
              预计通行时间：<span class="highlight">{{ Number(travelTime).toFixed(2) }}</span> 分钟
            </div>
          </div>
        </div>
      </aside>

      <!-- ===== OPENCODE-EDIT-19 CAV 小车编队演示控制面板 ===== -->
      <aside v-else-if="currentMode === 'cav'" class="right-panel cav-right-panel">
        <div class="panel-box">
          <h3 class="panel-title">🚗 CAV 小车编队演示</h3>
          <div class="hist-info">
            <div class="hist-row"><span>起点</span><b>{{ cavStartName }}</b></div>
            <div class="hist-row"><span>终点</span><b>{{ cavEndName }}</b></div>
            <div class="hist-row"><span>路线节点数</span><b>{{ cavRouteNodes.length }}</b></div>
          </div>
          <div class="btn-row">
            <button class="cyber-btn flex-btn" @click="startCavAnim" :disabled="cavLoading || !cavFleet.length">
              {{ cavRunning ? '▶ 行驶中' : '▶ 开始演示' }}
            </button>
            <button class="cyber-btn ghost flex-btn" @click="restartCavDemo" :disabled="cavLoading">
              ↻ 重新演示
            </button>
          </div>
          <div v-if="cavFinished" class="hist-status">✅ 演示完成，全部到达终点</div>
          <div v-else-if="cavRunning" class="hist-status">⏳ 编队行驶中...</div>
          <div v-else-if="cavLoading" class="hist-status">⏳ 正在获取编队数据...</div>
          <div v-if="cavError" class="hist-status error">{{ cavError }}</div>
        </div>

        <div class="panel-box">
          <h3 class="panel-title">📊 车队实时数据</h3>
          <table class="cav-table">
            <thead>
              <tr><th>车辆</th><th>车速 m/s</th><th>加速度</th><th>距前车</th><th>进度</th></tr>
            </thead>
            <tbody>
              <tr v-for="row in cavCarRows" :key="row.carId">
                <td>
                  <span class="cav-dot" :style="{ background: row.carId === 'CAV_L1' ? '#FFD700' : '#a855f7' }"></span>
                  {{ row.carId }}
                </td>
                <td>{{ Number(row.speed).toFixed(1) }}</td>
                <td>{{ Number(row.acceleration).toFixed(1) }}</td>
                <td>{{ row.carId === 'CAV_L1' ? '—' : Number(row.distanceToFront).toFixed(1) + 'm' }}</td>
                <td>{{ row.progressPct }}%</td>
              </tr>
            </tbody>
          </table>
        </div>
      </aside>
      <!-- ===== OPENCODE-EDIT-19 END ===== -->
    </div>

    <!-- 左下角：HUD 返回后台按钮 -->
    <div class="hud-back-btn" @click="goToAdmin" title="返回管理后台">
      <div class="hud-ring"></div>
      <div class="hud-icon">
        <span class="hud-text">后台<br>ADMIN</span>
      </div>
    </div>

    <!-- 节点详情弹窗 -->
    <div v-if="showModal" class="modal-overlay" @click.self="closeModal">
      <div class="modal-box">
        <h3>📍 节点详情：{{ currentNode?.name }}</h3>
        <p style="margin-bottom: 10px; color: #c4b5fd;">节点 ID: {{ currentNode?.id }} | 节点类型: {{ currentNode?.type }}</p>
        
        <p v-if="currentMode === 'heatmap'"><strong>实时人流密度：</strong>{{ currentNode?.density || 0 }}</p>
        <p v-if="currentMode === 'topology'"><strong>{{ algoNameMap[algoType] }}：</strong>{{ currentNode?.algoScore || 0 }}</p>
        
        <h4 style="margin-top: 20px; color: #a855f7;">📈 MindSpore 未来 10 分钟拥堵预测</h4>
        <div ref="predictChartRef" class="predict-chart"></div>
        <p v-if="predictEmpty" class="predict-empty">暂无预测数据</p>
        
        <button class="close-btn" @click="closeModal">关闭面板</button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, nextTick, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import * as echarts from 'echarts'
import topologyData from '../mock/topology.json' 
import request from '../utils/request'
import { getNodesDataAPI, getAlertsAPI, getShortestPathAPI, getNetworkPredictionsAPI, getHistoryHeatmapAPI, getCavFormationAPI } from '../api/index'

const router = useRouter()
const currentMode = ref<'heatmap' | 'topology' | 'history' | 'cav'>('heatmap')
const alertList = ref<any[]>([])

// ===== OPENCODE-EDIT-07 实时连接状态（后端正常=online，连续失败=offline） =====
const dataStatus = ref<'online' | 'offline'>('online')
let dataFailCount = 0
let isRefreshing = false
let dataTimer: number | undefined
let alertTimer: number | undefined
let sweepTimer: number | undefined
// ===== OPENCODE-EDIT-07 END =====

// ===== OPENCODE-EDIT-11 仿真时间（来自 /api/v1/network/nodes 响应 data.simTime） =====
const simTime = ref<string | null>(null)
const simDate = computed(() => simTime.value?.slice(0, 10) || '---- -- --')
const simClock = computed(() => simTime.value?.slice(11, 19) || '--:--:--')
// ===== OPENCODE-EDIT-11 END =====

const chartRef = ref<HTMLElement | null>(null)
let mainChart: echarts.ECharts | null = null

const bmapInstance = ref<any>(null)
const showModal = ref(false)
const currentNode = ref<any>(null)
const predictChartRef = ref<HTMLElement | null>(null)
let predictChart: echarts.ECharts | null = null

let nodeDynamicDataMap: Record<string, any> = {}
let hotnessDataMap = ref<Record<string, any>>({})
let hotspotsList = ref<any[]>([]) 

const topologyNodes = topologyData.nodes

// ===== OPENCODE-EDIT-16 历史热力图查询（老虎机滚轮选择器） =====
const HISTORY_DAYS = [
  { week: '周一', date: '8.3' },
  { week: '周二', date: '8.4' },
  { week: '周三', date: '8.5' },
  { week: '周四', date: '8.6' },
  { week: '周五', date: '8.7' },
  { week: '周六', date: '8.8' },
  { week: '周日', date: '8.9' }
]
const HISTORY_HOURS = Array.from({ length: 17 }, (_, i) => i + 6) // 06 ~ 22
const SEC_SLOTS = [0, 10, 20, 30, 40, 50]
const HIST_PLAY_MS = 2000 // 自动回放每帧间隔

const REEL_H = 52 // 单行滚轮项高度（对齐参考时间选择器）
const REEL_H_DAY = 52 // 日期滚轮项高度（两行：周几+日期）

const selectedDay = ref(0)
const selectedHour = ref(6)
const selectedSec = ref(0)

const isPlaying = ref(false)
const histLoading = ref(false)
const histError = ref('')
const histTick = ref<number | null>(null)
const histTimestamp = ref('')
let playTimer: number | undefined

const selectedSecIndex = computed(() => SEC_SLOTS.indexOf(selectedSec.value))

const dateText = computed(() => {
  const day = HISTORY_DAYS[selectedDay.value]!
  return `${day.date} ${day.week}`
})
const timeText = computed(() => `${String(selectedHour.value).padStart(2, '0')}:00:${String(selectedSec.value).padStart(2, '0')}`)
const resultText = computed(() => `${dateText.value} · ${timeText.value}`)

// 滚轮滚动：deltaY 向下 → 索引 +1
const spinReel = (key: 'day' | 'hour' | 'sec', e: WheelEvent) => {
  const step = e.deltaY > 0 ? 1 : -1
  if (key === 'day') {
    selectedDay.value = Math.min(HISTORY_DAYS.length - 1, Math.max(0, selectedDay.value + step))
  } else if (key === 'hour') {
    selectedHour.value = Math.min(22, Math.max(6, selectedHour.value + step))
  } else {
    const idx = Math.min(SEC_SLOTS.length - 1, Math.max(0, selectedSecIndex.value + step))
    selectedSec.value = SEC_SLOTS[idx]!
  }
}

// 点击滚轮任意项 → 直接跳选
const wheelJump = (key: 'day' | 'hour' | 'sec', e: MouseEvent) => {
  const el = (e.target as HTMLElement).closest('.wheel-item') as HTMLElement | null
  if (!el) return
  const idx = Number(el.dataset.idx)
  if (Number.isNaN(idx)) return
  if (key === 'day') selectedDay.value = idx
  else if (key === 'hour') selectedHour.value = HISTORY_HOURS[idx]!
  else selectedSec.value = SEC_SLOTS[idx]!
}

// 后端按具体日期录制，前端直接按 timestamp 就近匹配查询（不再换算 tick）
const computeHistoryTimestamp = () => {
  const day = 3 + selectedDay.value // day0=8.3 → 08-03 ... day6=8.9 → 08-09
  return `2026-08-${String(day).padStart(2, '0')} ${String(selectedHour.value).padStart(2, '0')}:00:${String(selectedSec.value).padStart(2, '0')}`
}
const expectedHistoryDate = computed(() => `2026-08-${String(3 + selectedDay.value).padStart(2, '0')}`)
// 返回的帧日期 ≠ 所选日期 → 该日期暂无数据，已就近匹配
const histMismatch = computed(() => {
  if (!histTimestamp.value) return false
  return !histTimestamp.value.startsWith(expectedHistoryDate.value)
})

const loadHistorySnapshot = async () => {
  if (currentMode.value !== 'history') return
  histLoading.value = true
  histError.value = ''
  const ts = computeHistoryTimestamp()
  try {
    const res: any = await getHistoryHeatmapAPI(ts)
    if ((res.code === 0 || res.code === 200) && res.data) {
      const nodes = Array.isArray(res.data.nodes) ? res.data.nodes : []
      nodeDynamicDataMap = {}
      nodes.forEach((item: any) => {
        nodeDynamicDataMap[item.nodeId] = item
        nodeDynamicDataMap[item.nodeName] = item
      })
      histTick.value = res.data.tick ?? null
      histTimestamp.value = res.data.timestamp ?? ''
      renderBaiduMap3D()
    } else {
      histError.value = res.msg || '查询失败'
    }
  } catch (e) {
    histError.value = '查询异常，请确认后端已启动'
    console.error('历史热力图查询失败', e)
  } finally {
    histLoading.value = false
  }
}

const advancePlay = () => {
  const maxHour = HISTORY_HOURS[HISTORY_HOURS.length - 1]!
  const secIdx = SEC_SLOTS.indexOf(selectedSec.value)
  if (secIdx < SEC_SLOTS.length - 1) { selectedSec.value = SEC_SLOTS[secIdx + 1]!; return }
  selectedSec.value = 0
  if (selectedHour.value < maxHour) { selectedHour.value += 1; return }
  selectedHour.value = 6
  selectedDay.value = selectedDay.value >= HISTORY_DAYS.length - 1 ? 0 : selectedDay.value + 1
}

const togglePlay = () => {
  isPlaying.value = !isPlaying.value
  if (isPlaying.value) {
    playTimer = window.setInterval(advancePlay, HIST_PLAY_MS)
  } else if (playTimer) {
    window.clearInterval(playTimer)
    playTimer = undefined
  }
}

watch([selectedDay, selectedHour, selectedSec], () => {
  loadHistorySnapshot()
}, { immediate: true })
// ===== OPENCODE-EDIT-16 END =====

// ===== OPENCODE-EDIT-19 CAV 小车编队演示（时间映射压缩动画） =====
// 方案：前端自算真实时长 = 路线长度/领航车速（拓扑单位≈1m），压缩到 ≤20s 播放；动图与真实速度/车距一致
const CAV_DEFAULT_START = 'gate_south'
const CAV_DEFAULT_END = 'canteen_1'
const CAV_MAX_PLAY_SEC = 20 // 真实时长超过此值才压缩
// ===== OPENCODE-EDIT-20 可视化放缩：车间距视觉放大，真实车距仍按表内数值展示 =====
const CAV_SPACING_SCALE = 3
// ===== OPENCODE-EDIT-20 END =====

const cavData = ref<any>(null)
const cavFleet = ref<any[]>([])
const cavRouteNodes = ref<string[]>([])
const cavRunning = ref(false)
const cavFinished = ref(false)
const cavLoading = ref(false)
const cavError = ref('')
const cavElapsed = ref(0)
const cavCarRows = ref<any[]>([])

let cavAnimFrame: number | undefined
let cavLastTs = 0
let cavRoutePts: { x: number; y: number }[] = []
let cavRouteCum: number[] = []
let cavRouteLen = 0
let cavK = 1 // 时间压缩系数
let cavGaps: number[] = []
let cavSpeeds: number[] = []

const cavStartName = computed(() => {
  const id = cavRouteNodes.value[0]
  const n = id ? nodeById[id] : null
  return n ? n.name : (id || '--')
})
const cavEndName = computed(() => {
  const id = cavRouteNodes.value[cavRouteNodes.value.length - 1]
  const n = id ? nodeById[id] : null
  return n ? n.name : (id || '--')
})

// 沿路线折线按路程 d 插值出坐标
const pointAtDistance = (d: number) => {
  if (!Number.isFinite(d)) d = 0
  if (cavRouteLen <= 0 || cavRoutePts.length === 0) return { x: 50, y: 50 }
  const dd = Math.max(0, Math.min(d, cavRouteLen))
  for (let i = 0; i < cavRouteCum.length - 1; i++) {
    if (dd <= cavRouteCum[i + 1]!) {
      const segLen = (cavRouteCum[i + 1]!) - (cavRouteCum[i]!)
      const a = cavRoutePts[i]!
      const b = cavRoutePts[i + 1]!
      const t = segLen > 0 ? (dd - cavRouteCum[i]!) / segLen : 0
      return { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t }
    }
  }
  const last = cavRoutePts[cavRoutePts.length - 1]!
  return { x: last.x, y: last.y }
}

const buildCavRoute = () => {
  cavRoutePts = cavRouteNodes.value.map(id => {
    const n = nodeById[id]
    return n ? { x: n.x, y: n.y } : null
  }).filter(Boolean) as { x: number; y: number }[]
  cavRouteCum = [0]
  let cum = 0
  for (let i = 1; i < cavRoutePts.length; i++) {
    const a = cavRoutePts[i - 1]!
    const b = cavRoutePts[i]!
    cum += Math.sqrt((b.x - a.x) ** 2 + (b.y - a.y) ** 2)
    cavRouteCum.push(cum)
  }
  cavRouteLen = cum
}

// 更新 ECharts 中 4 辆小车的位置（每帧只更新纯坐标，样式在系列级一次性配置）
const updateCavCars = (dists: number[]) => {
  if (!mainChart) return
  const data = dists.map((d, i) => {
    const p = pointAtDistance(d)
    return {
      value: [p.x, p.y],
      label: { formatter: (cavFleet.value[i] as any)?.carId || `CAV_${i + 1}` }
    }
  })
  mainChart.setOption({ series: [{ id: 'cav-cars', data }] })
}

const renderCavChart = () => {
  if (!mainChart) return
  const routeIds = cavRouteNodes.value
  const startId = routeIds[0]
  const endId = routeIds[routeIds.length - 1]

  // 只展示路线上途经的点（起点/终点/中间点），其余拓扑元素不画
  const graphNodes = routeIds.map((id) => {
    const n = nodeById[id]
    if (!n) return null
    const isStart = id === startId
    const isEnd = id === endId
    const isMid = !isStart && !isEnd
    return {
      id: n.id,
      name: n.name,
      value: [n.x, n.y],
      symbol: isStart || isEnd ? 'diamond' : 'circle',
      symbolSize: isStart || isEnd ? 36 : 18,
      itemStyle: {
        color: isStart ? '#2ED573' : (isEnd ? '#FF4757' : '#facc15'),
        borderColor: '#fff',
        borderWidth: 2,
        shadowBlur: isStart || isEnd ? 20 : 8,
        shadowColor: isStart ? '#2ED573' : (isEnd ? '#FF4757' : '#facc15')
      },
      label: {
        show: true,
        color: '#fff',
        fontWeight: 'bold',
        fontSize: 11,
        position: isMid ? 'right' : 'bottom',
        textBorderColor: '#000',
        textBorderWidth: 2
      }
    }
  }).filter(Boolean)

  const graphEdges = routeIds.slice(0, -1).map((id, i) => ({
    source: id,
    target: routeIds[i + 1],
    lineStyle: {
      width: 4,
      opacity: 1,
      color: '#facc15',
      curveness: 0.02
    }
  }))

  // 由路线包围盒居中缩放，使路线填满图表
  const xs = graphNodes.map((nd: any) => nd.value[0])
  const ys = graphNodes.map((nd: any) => nd.value[1])
  const xMin = Math.min(...xs)
  const xMax = Math.max(...xs)
  const yMin = Math.min(...ys)
  const yMax = Math.max(...ys)
  const xRange = xMax - xMin
  const yRange = yMax - yMin
  const xPad = xRange > 0 ? xRange * 0.2 : 15
  const yPad = yRange > 0 ? yRange * 0.25 : 15
  const axMin = Math.max(0, xMin - xPad)
  const axMax = Math.min(100, xMax + xPad)
  const ayMin = Math.max(0, yMin - yPad)
  const ayMax = Math.min(100, yMax + yPad)

  const option = {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'item',
      backgroundColor: 'rgba(15, 10, 30, 0.85)',
      borderColor: '#a855f7',
      textStyle: { color: '#fff' }
    },
    grid: { top: '6%', bottom: '6%', left: '4%', right: '4%' },
    xAxis: { type: 'value', min: axMin, max: axMax, show: false },
    yAxis: { type: 'value', min: ayMin, max: ayMax, show: false },
    series: [
      {
        type: 'graph',
        coordinateSystem: 'cartesian2d',
        data: graphNodes,
        links: graphEdges,
        label: { show: false }
      },
      {
        // 小车层：系列级统一配置，每帧仅更新坐标；关闭动画避免快速移动 tween 跑偏
        type: 'scatter',
        id: 'cav-cars',
        coordinateSystem: 'cartesian2d',
        animation: false,
        zlevel: 5,
        symbol: 'circle',
        data: [],
        itemStyle: {
          color: (params: any) => (params.dataIndex === 0 ? '#FFD700' : '#a855f7'),
          borderColor: '#fff',
          borderWidth: 1.5,
          shadowBlur: 14,
          shadowColor: (params: any) => (params.dataIndex === 0 ? '#FFD700' : '#a855f7')
        },
        label: {
          show: true,
          position: 'top',
          color: '#fff',
          fontSize: 10,
          fontWeight: 'bold',
          textBorderColor: '#000',
          textBorderWidth: 2
        },
        symbolSize: (params: any) => (params.dataIndex === 0 ? 20 : 16)
      }
    ]
  }
  mainChart.setOption(option, true)
  const zero = cavFleet.value.map(() => 0)
  updateCavCars(zero)
}

const cavTick = (ts: number) => {
  if (cavLastTs === 0) cavLastTs = ts
  const dt = (ts - cavLastTs) / 1000
  cavLastTs = ts
  if (!cavRunning.value) return
  cavElapsed.value += dt
  const tReal = cavElapsed.value / cavK
  const dists = cavFleet.value.map((_, i) => cavSpeeds[i]! * Math.max(0, tReal - cavGaps[i]!))
  updateCavCars(dists)
  cavCarRows.value = cavFleet.value.map((car, i) => ({
    ...car,
    progressPct: Math.min(100, Math.round((Math.min(dists[i]!, cavRouteLen) / (cavRouteLen || 1)) * 100))
  }))
  if (dists.every(d => d >= cavRouteLen)) {
    cavRunning.value = false
    cavFinished.value = true
    cavElapsed.value = 0
    return
  }
  cavAnimFrame = requestAnimationFrame(cavTick)
}

const stopCavAnim = () => {
  cavRunning.value = false
  if (cavAnimFrame) cancelAnimationFrame(cavAnimFrame)
  cavAnimFrame = undefined
  cavLastTs = 0
}

const startCavAnim = () => {
  if (!cavFleet.value.length || cavRouteLen <= 0) return
  stopCavAnim()
  cavElapsed.value = 0
  cavFinished.value = false
  cavRunning.value = true
  cavAnimFrame = requestAnimationFrame(cavTick)
}

const restartCavDemo = () => {
  loadCavDemo()
}

const loadCavDemo = async () => {
  if (currentMode.value !== 'cav') return
  stopCavAnim()
  cavLoading.value = true
  cavError.value = ''
  cavFinished.value = false
  cavCarRows.value = []
  try {
    const res: any = await getCavFormationAPI(CAV_DEFAULT_START, CAV_DEFAULT_END)
    if ((res.code === 0 || res.code === 200) && res.data) {
      cavData.value = res.data
      const path = res.data.path || {}
      const route = Array.isArray(path.routeNodes) ? path.routeNodes : []
      const fleet = Array.isArray(res.data.cavFleet) ? res.data.cavFleet : []
      cavRouteNodes.value = route
      cavFleet.value = fleet
      if (!route.length || !fleet.length) {
        cavError.value = '后端未返回有效路线或车队数据'
        return
      }
      buildCavRoute()
      const v0 = parseFloat(fleet[0]?.speed) || 15
      cavSpeeds = fleet.map((car: any) => parseFloat(car.speed) || v0)
      // ===== OPENCODE-EDIT-20 真实车距 × 视觉放缩倍数 =====
      cavGaps = fleet.map((car: any, i: number) => {
        if (i === 0) return 0
        const v = parseFloat(car.speed) || v0
        const gap = parseFloat(car.distanceToFront) || 10
        return i * ((gap * CAV_SPACING_SCALE) / v)
      })
      // ===== OPENCODE-EDIT-20 END =====
      const realLeader = cavRouteLen / v0
      const realTotal = (cavGaps[cavGaps.length - 1] || 0) + realLeader
      cavK = realTotal <= CAV_MAX_PLAY_SEC ? 1 : CAV_MAX_PLAY_SEC / realTotal
      renderCavChart()
      startCavAnim()
    } else {
      cavError.value = res.msg || '获取 CAV 数据失败'
    }
  } catch (e) {
    cavError.value = '获取 CAV 数据异常，请确认后端已启动'
    console.error('CAV 数据获取失败', e)
  } finally {
    cavLoading.value = false
  }
}
// ===== OPENCODE-EDIT-19 END =====

// ===== OPENCODE-EDIT-08 地图增量更新：偏移表 / 节点工具 / overlay 引用表 =====
// （原 EDIT-05 的 MAP_LAT_OFFSET 与 EDIT-06 的节点工具函数已上提为模块级，供每秒增量更新复用）
const MAP_LAT_OFFSET: Record<string, number> = {
  exp_1: 0.0006,
  exp_2: 0.0006,
  exp_3: 0.0006,
  exp_4: 0.0006,
  science_hall: 0.0006, // 理学馆跟随实组上移
  struct_center: 0.0012,
  env_inst: 0.0012,
  biomed_inst: 0.0012
}

const nodeById: Record<string, any> = {}
topologyNodes.forEach((n: any) => { nodeById[n.id] = n })

const getNodePos = (n: any) => {
  const { lng, lat: baseLat } = mapToRealLngLat(n.x, n.y)
  return { lng, lat: baseLat + (MAP_LAT_OFFSET[n.id] || 0) }
}

const getRoadDensity = (id: string) => {
  const n = nodeById[id]
  if (!n) return 0.1
  const dynamicData = nodeDynamicDataMap[n.name] || nodeDynamicDataMap[n.id] || {}
  return parseFloat(dynamicData.density) || 0.1
}

const getDensityColor = (density: number) => {
  if (density >= 0.8) return '#FF0000'
  if (density >= 0.5) return '#FF8C00'
  return '#2ED573'
}

const getHeightMultiplier = (density: number) => {
  if (density >= 0.8) return 600
  if (density >= 0.5) return 350
  return 120
}

// ===== OPENCODE-EDIT-17 柱体高度封顶 + 标签最低悬停高度 =====
const MAX_COLUMN_HEIGHT = 450 // 柱体最高封顶，防止密度高时狂飙
const MIN_LABEL_HEIGHT = 180 // 建筑名标签最低显示高度（柱矮时悬停在可读高度）
const getColumnHeight = (density: number) => Math.min(density * getHeightMultiplier(density), MAX_COLUMN_HEIGHT)
const getLabelHeight = (density: number) => Math.max(getColumnHeight(density), MIN_LABEL_HEIGHT)
// ===== OPENCODE-EDIT-17 END =====

// ===== OPENCODE-EDIT-12 建筑名字绑定柱顶高度：锚点换算工具 =====
const METERS_PER_DEG_LAT = 111320
const VERTICAL_TILT_DEG = 60 // 与 setTilt(60) 保持一致

// 估算文字像素宽度的一半（12px 字体，中英混排近似），用于横向居中
const getLabelHalfWidth = (text: string) => Math.round(text.length * 6)

// 把柱体顶端（再上移一点）的屏幕像素换算成经纬度锚点：
// 柱高(米) × 纬度方向 px/米 × tan(俯仰角) ≈ 柱体在屏幕上的投影高度。
// 锚点是真实经纬度，地图缩放后仍能贴合柱顶。
const getLabelTopPoint = (map: any, lng: number, lat: number, heightMeters: number) => {
  const BMapGL = (window as any).BMapGL
  const base = map.pointToPixel(new BMapGL.Point(lng, lat))
  const ref = map.pointToPixel(new BMapGL.Point(lng, lat + 0.001))
  const pxPerMeter = Math.abs(ref.y - base.y) / (0.001 * METERS_PER_DEG_LAT)
  const verticalPx = heightMeters * pxPerMeter * Math.tan((VERTICAL_TILT_DEG * Math.PI) / 180)
  const topPoint = map.pixelToPoint({ x: base.x, y: base.y - verticalPx - 8 })
  return topPoint
}
// ===== OPENCODE-EDIT-12 END =====

let prismRefs: Record<string, any> = {}
let labelRefs: Record<string, any> = {}
let polylineRefs: Record<string, any> = {}
let lastNodeDensity: Record<string, number> = {}

// ===== OPENCODE-EDIT-09 道路渐变：颜色插值 + 分段渐变线构建 =====
// 每段在线段参数 t ∈ [0,1] 处取源色→目标色的插值色
const hexToRgb = (hex: string): [number, number, number] => {
  const h = hex.replace('#', '')
  return [
    parseInt(h.slice(0, 2), 16),
    parseInt(h.slice(2, 4), 16),
    parseInt(h.slice(4, 6), 16)
  ]
}

const rgbToHex = (r: number, g: number, b: number): string => {
  const to = (v: number) => Math.round(Math.max(0, Math.min(255, v))).toString(16).padStart(2, '0')
  return `#${to(r)}${to(g)}${to(b)}`
}

const interpolateColor = (c1: string, c2: string, t: number): string => {
  const a = hexToRgb(c1)
  const b = hexToRgb(c2)
  return rgbToHex(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)
}

const SEGMENTS = 5

const buildRoadSegments = (
  BMapGL: any,
  srcPos: { lng: number; lat: number },
  dstPos: { lng: number; lat: number },
  srcColor: string,
  dstColor: string,
  width: number
): any[] => {
  const segments: any[] = []
  if (srcColor === dstColor) {
    segments.push(new BMapGL.Polyline([
      new BMapGL.Point(srcPos.lng, srcPos.lat),
      new BMapGL.Point(dstPos.lng, dstPos.lat)
    ], {
      strokeColor: srcColor,
      strokeWeight: width,
      strokeOpacity: 0.7
    }))
    return segments
  }
  for (let i = 0; i < SEGMENTS; i++) {
    const t0 = i / SEGMENTS
    const t1 = (i + 1) / SEGMENTS
    const p0 = {
      lng: srcPos.lng + (dstPos.lng - srcPos.lng) * t0,
      lat: srcPos.lat + (dstPos.lat - srcPos.lat) * t0
    }
    const p1 = {
      lng: srcPos.lng + (dstPos.lng - srcPos.lng) * t1,
      lat: srcPos.lat + (dstPos.lat - srcPos.lat) * t1
    }
    const color = interpolateColor(srcColor, dstColor, (t0 + t1) / 2)
    segments.push(new BMapGL.Polyline([
      new BMapGL.Point(p0.lng, p0.lat),
      new BMapGL.Point(p1.lng, p1.lat)
    ], {
      strokeColor: color,
      strokeWeight: width,
      strokeOpacity: 0.7
    }))
  }
  return segments
}

// 记录每条边当前的 [源色, 目标色]，用于判断档位是否变化需要重建
let lastRoadTier: Record<string, string[]> = {}
// ===== OPENCODE-EDIT-09 END =====

const algoType = ref<'pagerank' | 'betweenness' | 'heatScore'>('pagerank')
const algoNameMap = {
  pagerank: 'PageRank',
  betweenness: '中介中心性',
  heatScore: '热点吸引力'
}

const pathSrc = ref('')
const pathDst = ref('')
const travelTime = ref(0)
const shortestPathEdges = ref<Set<string>>(new Set())

// ★ ID 翻译引擎
const backendIdToTopologyId: Record<string, string> = {
  'zone_canteen': 'canteen_1',
  'cross_4': 'cross_zh_south',
  'zone_gate_north': 'gate_south',
  'cross_1': 'cross_zh_mid',
  'diverg_1': 'underpass',
  'zone_library': 'library',
  'zone_gym': 'sports_gym',
  '食堂': 'canteen_1',
  '北门': 'gate_south',
  '图书馆': 'library',
  '体育馆': 'sports_gym',
  '教一': 'teach_1',
  '教二': 'teach_2'
}

const getRealId = (backendId: string) => {
  return backendIdToTopologyId[backendId] || backendId;
}

const maxAlgoValue = computed(() => {
  if (algoType.value === 'heatScore') {
    if (hotspotsList.value.length === 0) return 1
    const vals = hotspotsList.value.map(s => s.attractScore || 0)
    return Math.max(...vals, 0.0001)
  }
  if (Object.keys(hotnessDataMap.value).length === 0) return 1
  const values = Object.values(hotnessDataMap.value).map(item => item[algoType.value] || 0)
  return Math.max(...values, 0.0001) 
})

const top5Hubs = computed(() => {
  if (algoType.value === 'heatScore') {
    return hotspotsList.value.map(spot => ({
      nodeId: spot.region,
      nodeName: spot.region, 
      heatScore: spot.attractScore
    })).sort((a, b) => b.heatScore - a.heatScore).slice(0, 5)
  }

  const arr = Object.values(hotnessDataMap.value).filter(item => item[algoType.value] !== undefined)
  arr.forEach(item => {
    if (!item.nodeName) {
      const topoNode = topologyNodes.find(n => n.id === item.nodeId)
      item.nodeName = topoNode ? topoNode.name : item.nodeId
    }
  })
  return arr.sort((a, b) => (b[algoType.value] || 0) - (a[algoType.value] || 0)).slice(0, 5)
})

// ==========================================
// ★ 百度 3D 地图核心引擎 
// ==========================================
const loadBaiduMapSDK = (): Promise<void> => {
  return new Promise((resolve, reject) => {
    if ((window as any).BMapGL) { resolve(); return; }
    const script = document.createElement('script');
    // 👇 替换为你申请的真实百度地图 AK
    script.src = `https://api.map.baidu.com/api?v=1.0&type=webgl&ak=YTz871wq3pE4J03bcGyRGStseWeCuvSy&callback=initBMapCallback`;
    script.onerror = reject;
    (window as any).initBMapCallback = () => { resolve(); };
    document.head.appendChild(script);
  });
}

// ===== OPENCODE-EDIT-02 5锚点仿射对齐（原 mapToRealLngLat 整体注释保留） =====
// 旧代码（含 EDIT-01 一并保留，整段已注释）
// const mapToRealLngLat = (x: number, y: number) => {
//   const lngMin = 113.385;
//   const lngMax = 113.415;
//   const latMin = 23.035;
//   const latMax = 23.060;
//   const lng = lngMin + (x / 100) * (lngMax - lngMin);
//   // ===== OPENCODE-EDIT-01 修复上下颠倒（旧代码注释保留，可回退） =====
//   /* 旧代码
//   const lat = latMax - (y / 100) * (latMax - latMin);
//   */
//   const lat = latMin + (y / 100) * (latMax - latMin);
//   return { lng, lat };
// }
// -------- 新代码：5 锚点最小二乘仿射映射 --------
const realAnchors = [
  { x: 65, y: 5,  lng: 113.403209, lat: 23.039845 }, // 南大门
  { x: 8,  y: 55, lng: 113.389947, lat: 23.048883 }, // 西门出口
  { x: 92, y: 55, lng: 113.407678, lat: 23.044725 }, // 东门出口
  { x: 58, y: 95, lng: 113.400746, lat: 23.050812 }, // 校医院
  { x: 58, y: 38, lng: 113.401959, lat: 23.044147 }, // 图书馆
]

const solveAffine = (v: number[]): [number, number, number] => {
  const ATA = [0, 0, 0, 0, 0, 0, 0, 0, 0];
  const ATv = [0, 0, 0];
  realAnchors.forEach((a, i) => {
    const row = [a.x, a.y, 1];
    for (let m = 0; m < 3; m++) {
      ATv[m] = ATv[m]! + row[m]! * v[i]!;
      for (let n = 0; n < 3; n++) ATA[m * 3 + n] = ATA[m * 3 + n]! + row[m]! * row[n]!;
    }
  });
  const det = (m: number[]): number =>
    m[0]! * (m[4]! * m[8]! - m[5]! * m[7]!) - m[1]! * (m[3]! * m[8]! - m[5]! * m[6]!) + m[2]! * (m[3]! * m[7]! - m[4]! * m[6]!);
  const D = det(ATA);
  const result: number[] = [];
  for (let k = 0; k < 3; k++) {
    const M = ATA.slice();
    for (let r = 0; r < 3; r++) M[r * 3 + k] = ATv[r]!;
    result.push(det(M) / D);
  }
  return [result[0]!, result[1]!, result[2]!];
}

const [ax, ay, ac] = solveAffine(realAnchors.map(a => a.lng));
const [dx, dy, dc] = solveAffine(realAnchors.map(a => a.lat));

const mapToRealLngLat = (x: number, y: number) => {
  const lng = ax * x + ay * y + ac;
  const lat = dx * x + dy * y + dc;
  return { lng, lat };
}

// 拓扑中心点 (50,50) 仿射后的真实坐标，用于地图初始定位
const campusCenter = mapToRealLngLat(50, 50);
// ===== OPENCODE-EDIT-02 END =====

const renderBaiduMap3D = () => {
  const BMapGL = (window as any).BMapGL;
  if (!BMapGL) return;

  if (!bmapInstance.value) {
    const map = new BMapGL.Map('bmap-container');
    // ===== OPENCODE-EDIT-03 地图初始定位到校区中心（旧：大学城中心） =====
    /* 旧代码
    map.centerAndZoom(new BMapGL.Point(113.400, 23.048), 16);
    */
    map.centerAndZoom(new BMapGL.Point(campusCenter.lng, campusCenter.lat), 16);
    // ===== OPENCODE-EDIT-03 END =====
    map.enableScrollWheelZoom(true);
    map.setTilt(60); 
    map.setHeading(20); 
    map.setMapStyleV2({ styleId: '861a15ed1e83fdfbfd7328dd3017a024' }); 
    bmapInstance.value = map;
  }

  const map = bmapInstance.value;
  map.clearOverlays(); 

  // ===== OPENCODE-EDIT-05 指定节点纬度北移（实组+1底座=0.0006，结构组+2底座=0.0012，理学馆跟随实组） =====
  /* 旧代码（已上提为模块级，见 OPENCODE-EDIT-08，避免每秒增量更新重复计算）
  const MAP_LAT_OFFSET: Record<string, number> = {
    exp_1: 0.0006,
    exp_2: 0.0006,
    exp_3: 0.0006,
    exp_4: 0.0006,
    science_hall: 0.0006, // 理学馆跟随实组上移
    struct_center: 0.0012,
    env_inst: 0.0012,
    biomed_inst: 0.0012
  }
  */
  // ===== OPENCODE-EDIT-05 END =====

  // ===== OPENCODE-EDIT-06 新增：拓扑道路渲染（宽度≈底座一半，按拥堵着色） =====
  /* 旧代码（nodeById / getNodePos / getRoadDensity 已上提为模块级，见 OPENCODE-EDIT-08）
  const nodeById: Record<string, any> = {}
  topologyNodes.forEach((n: any) => { nodeById[n.id] = n })

  const getNodePos = (n: any) => {
    const { lng, lat: baseLat } = mapToRealLngLat(n.x, n.y)
    return { lng, lat: baseLat + (MAP_LAT_OFFSET[n.id] || 0) }
  }
  */

  const roadWidthPx = (() => {
    const base = map.pointToPixel(new BMapGL.Point(campusCenter.lng, campusCenter.lat))
    const delta = map.pointToPixel(new BMapGL.Point(campusCenter.lng + 0.0003, campusCenter.lat))
    return Math.max(2, Math.abs(delta.x - base.x))
  })()

  /* 旧代码（getRoadDensity 已上提为模块级）
  const getRoadDensity = (id: string) => {
    const n = nodeById[id]
    if (!n) return 0.1
    const dynamicData = nodeDynamicDataMap[n.name] || nodeDynamicDataMap[n.id] || {}
    return parseFloat(dynamicData.density) || 0.1
  }
  */

  polylineRefs = {}
  lastRoadTier = {}
  topologyData.edges.forEach((e: any) => {
    const srcNode = nodeById[e.source]
    const dstNode = nodeById[e.target]
    if (!srcNode || !dstNode) return

    const srcPos = getNodePos(srcNode)
    const dstPos = getNodePos(dstNode)
    // ===== OPENCODE-EDIT-09 道路两端分别取各自建筑颜色，分段渐变渲染 =====
    /* 旧代码（整条路取两端密度较大值单色渲染）
    const roadDensity = Math.max(getRoadDensity(e.source), getRoadDensity(e.target))
    let roadColor = getDensityColor(roadDensity)

    const polyline = new BMapGL.Polyline([
      new BMapGL.Point(srcPos.lng, srcPos.lat),
      new BMapGL.Point(dstPos.lng, dstPos.lat)
    ], {
      strokeColor: roadColor,
      strokeWeight: roadWidthPx,
      strokeOpacity: 0.7
    })
    map.addOverlay(polyline)
    polylineRefs[`${e.source}-${e.target}`] = polyline
    */
    const srcColor = getDensityColor(getRoadDensity(e.source))
    const dstColor = getDensityColor(getRoadDensity(e.target))
    const edgeKey = `${e.source}-${e.target}`
    const segments = buildRoadSegments(BMapGL, srcPos, dstPos, srcColor, dstColor, roadWidthPx)
    segments.forEach((s: any) => map.addOverlay(s))
    polylineRefs[edgeKey] = segments
    lastRoadTier[edgeKey] = [srcColor, dstColor]
    // ===== OPENCODE-EDIT-09 END =====
  })
  // ===== OPENCODE-EDIT-06 END =====

  // ===== OPENCODE-EDIT-08 全量渲染后重置引用表（clearOverlays 已清空旧 overlay） =====
  prismRefs = {}
  labelRefs = {}
  lastNodeDensity = {}
  // ===== OPENCODE-EDIT-08 END =====

  topologyNodes.forEach((n: any) => {
    const dynamicData = nodeDynamicDataMap[n.name] || nodeDynamicDataMap[n.id] || {};
    const density = parseFloat(dynamicData.density) || 0.1;
    // ===== OPENCODE-EDIT-05 应用北移偏移（柱子和标签共用此 lat） =====
    /* 旧代码
    const { lng, lat } = mapToRealLngLat(n.x, n.y);
    */
    const { lng, lat: baseLat } = mapToRealLngLat(n.x, n.y);
    const lat = baseLat + (MAP_LAT_OFFSET[n.id] || 0);
    // ===== OPENCODE-EDIT-05 END =====

    // ===== OPENCODE-EDIT-04 柱高与底座改用折中值（旧：offset 0.0006 / 柱高 200·1200·700） =====
    /* 旧代码（颜色/高度判断已封装为模块级 getDensityColor / getHeightMultiplier，见 OPENCODE-EDIT-08）
    let baseColor = '#2ED573';
    let heightMultiplier = 200;

    if (density >= 0.8) {
      baseColor = '#FF0000';
      heightMultiplier = 1200;
    } else if (density >= 0.5) {
      baseColor = '#FF8C00';
      heightMultiplier = 700;
    }

    const offset = 0.0006;
    */
    let baseColor = getDensityColor(density);
    const offset = 0.0003; // 折中值（原 0.0006 → 阶段A 0.00012）
    // ===== OPENCODE-EDIT-04 END =====
    const prismBase = [
      new BMapGL.Point(lng - offset, lat - offset),
      new BMapGL.Point(lng + offset, lat - offset),
      new BMapGL.Point(lng + offset, lat + offset),
      new BMapGL.Point(lng - offset, lat + offset),
    ];
    
    // ===== OPENCODE-EDIT-17 柱体高度改用封顶后的 getColumnHeight =====
    const prism = new BMapGL.Prism(prismBase, getColumnHeight(density), {
      topFillColor: baseColor,
      topFillOpacity: 0.9,
      sideFillColor: baseColor,
      sideFillOpacity: 0.6
    });

    const openNodeDetail = () => {
      currentNode.value = { ...n, density: density };
      showModal.value = true;
      nextTick().then(() => renderPredictChart(currentNode.value.id));
    }

    prism.addEventListener('click', openNodeDetail);

    map.addOverlay(prism);
    prismRefs[n.id] = prism
    lastNodeDensity[n.id] = density

    /* ===== OPENCODE-EDIT-12 建筑名字绑定柱顶（旧代码注释保留，可回退） =====
    const label = new BMapGL.Label(n.name, {
      position: new BMapGL.Point(lng, lat),
      offset: new BMapGL.Size(-15, -15)
    });
    label.setStyle({
      color: '#fff',
      background: 'transparent',
      border: 'none',
      fontSize: '12px',
      fontWeight: 'bold',
      textShadow: `0 0 5px ${baseColor}`
    });
    map.addOverlay(label);
    labelRefs[n.id] = label
    */
    // ===== OPENCODE-EDIT-17 标签位置用 getLabelHeight（柱矮时悬停可读高度，不再沉底） =====
    const labelTopPoint = getLabelTopPoint(map, lng, lat, getLabelHeight(density))
    const label = new BMapGL.Label(n.name, {
      position: labelTopPoint,
      offset: new BMapGL.Size(-getLabelHalfWidth(n.name), -4)
    });
    label.setStyle({
      color: '#fff',
      background: 'transparent',
      border: 'none',
      fontSize: '12px',
      fontWeight: 'bold',
      textShadow: `0 0 5px ${baseColor}`
    });
    // ===== OPENCODE-EDIT-17 标签可点击 → 直接查看节点详情/预测折线图 =====
    label.addEventListener('click', openNodeDetail);
    // ===== OPENCODE-EDIT-17 END =====
    map.addOverlay(label);
    labelRefs[n.id] = label
    // ===== OPENCODE-EDIT-12 END =====
  });
}

// ===== OPENCODE-EDIT-08 增量更新地图拥堵（不清空整图，只更新密度变化的柱子/标签/道路） =====
const updateBaiduMapCongestion = () => {
  const BMapGL = (window as any).BMapGL;
  const map = bmapInstance.value;
  if (!BMapGL || !map) return;

  // 1. 更新柱子与标签
  topologyNodes.forEach((n: any) => {
    const dynamicData = nodeDynamicDataMap[n.name] || nodeDynamicDataMap[n.id] || {};
    const density = parseFloat(dynamicData.density) || 0.1;

    // 密度几乎没变 → 跳过，避免频繁重建
    if (Math.abs(density - (lastNodeDensity[n.id] ?? -1)) < 0.02) return;

    lastNodeDensity[n.id] = density;

    const { lng, lat: baseLat } = mapToRealLngLat(n.x, n.y);
    const lat = baseLat + (MAP_LAT_OFFSET[n.id] || 0);
    const offset = 0.0003;
    const baseColor = getDensityColor(density);

    // 柱子：Prism 无原生改色改高接口，只能移除单个再重建（不清空整图）
    if (prismRefs[n.id]) {
      map.removeOverlay(prismRefs[n.id]);
      prismRefs[n.id] = null;
    }
    const prismBase = [
      new BMapGL.Point(lng - offset, lat - offset),
      new BMapGL.Point(lng + offset, lat - offset),
      new BMapGL.Point(lng + offset, lat + offset),
      new BMapGL.Point(lng - offset, lat + offset),
    ];
    const prism = new BMapGL.Prism(prismBase, getColumnHeight(density), {
      topFillColor: baseColor,
      topFillOpacity: 0.9,
      sideFillColor: baseColor,
      sideFillOpacity: 0.6
    });
    prism.addEventListener('click', () => {
      currentNode.value = { ...n, density: density };
      showModal.value = true;
      nextTick().then(() => renderPredictChart(currentNode.value.id));
    });
    map.addOverlay(prism);
    prismRefs[n.id] = prism;

    // ===== OPENCODE-EDIT-12 标签随柱高变化：更新颜色 + 位置（旧代码注释保留） =====
    /* 旧代码
    if (labelRefs[n.id]) {
      labelRefs[n.id].setStyle({
        color: '#fff',
        background: 'transparent',
        border: 'none',
        fontSize: '12px',
        fontWeight: 'bold',
        textShadow: `0 0 5px ${baseColor}`
      });
    }
    */
    if (labelRefs[n.id]) {
      labelRefs[n.id].setStyle({
        color: '#fff',
        background: 'transparent',
        border: 'none',
        fontSize: '12px',
        fontWeight: 'bold',
        textShadow: `0 0 5px ${baseColor}`
      });
      // ===== OPENCODE-EDIT-17 标签位置用 getLabelHeight（柱矮时悬停可读高度） =====
      labelRefs[n.id].setPosition(getLabelTopPoint(map, lng, lat, getLabelHeight(density)));
      // ===== OPENCODE-EDIT-17 END =====
    }
    // ===== OPENCODE-EDIT-12 END =====
  });

  // 2. 更新道路颜色（档位变化才重建，否则原地改色）
  // ===== OPENCODE-EDIT-09 渐变道路增量更新 =====
  topologyData.edges.forEach((e: any) => {
    const srcNode = nodeById[e.source]
    const dstNode = nodeById[e.target]
    if (!srcNode || !dstNode) return

    const edgeKey = `${e.source}-${e.target}`
    const srcColor = getDensityColor(getRoadDensity(e.source))
    const dstColor = getDensityColor(getRoadDensity(e.target))

    const lastTier = lastRoadTier[edgeKey]
    // 档位没变 → 只原地重涂已存在的线段（渐变内部颜色无需变，两端色即档位色）
    if (lastTier && lastTier[0] === srcColor && lastTier[1] === dstColor) return

    // 档位变化（含 同→不同、不同→同、档位跳动）→ 重建该边线段
    const oldSegments = polylineRefs[edgeKey]
    if (Array.isArray(oldSegments)) {
      oldSegments.forEach((s: any) => map.removeOverlay(s))
    }

    const srcPos = getNodePos(srcNode)
    const dstPos = getNodePos(dstNode)
    const roadWidthPx = (() => {
      const base = map.pointToPixel(new BMapGL.Point(campusCenter.lng, campusCenter.lat))
      const delta = map.pointToPixel(new BMapGL.Point(campusCenter.lng + 0.0003, campusCenter.lat))
      return Math.max(2, Math.abs(delta.x - base.x))
    })()
    const segments = buildRoadSegments(BMapGL, srcPos, dstPos, srcColor, dstColor, roadWidthPx)
    segments.forEach((s: any) => map.addOverlay(s))
    polylineRefs[edgeKey] = segments
    lastRoadTier[edgeKey] = [srcColor, dstColor]
  })
  // ===== OPENCODE-EDIT-09 END =====
}
// ===== OPENCODE-EDIT-08 END =====

// ===== OPENCODE-EDIT-15 预警信息解析（地点/密度/预计持续到，来源见 QG中期 预警接口.md） =====
/* 旧代码（OPENCODE-EDIT-15 注释保留，可回退）
const formatAlertText = (alert: any) => {
  if (typeof alert === 'string') return alert;
  const nodeName = alert.nodeName || alert.nodeId || '未知节点'
  const desc = alert.description || ''
  
  const densityMatch = desc.match(/密度.*?([\d.]+)/)
  const durationMatch = desc.match(/持续.*?(\d+)/)
  const density = densityMatch ? densityMatch[1] : '未知'
  const duration = durationMatch ? `${durationMatch[1]}分钟` : '未知'
  return `预警节点：${nodeName} | 当前密度：${density} | 预计持续：${duration}`
}
*/

// 预警产生时间（毫秒）：优先 createdAt（YYYY-MM-DD HH:MM:SS），兜底常见时间字段
const parseAlertStartMs = (alert: any): number | null => {
  const raw = alert?.createdAt ?? alert?.alertTime ?? alert?.time ?? alert?.timestamp ?? null
  if (raw === null || raw === undefined || raw === '') return null
  const ms = new Date(String(raw).replace('T', ' ').slice(0, 19)).getTime()
  return isNaN(ms) ? null : ms
}

// 预计持续分钟数：从 description「预计持续:N分钟」抠取
const getAlertDurationMin = (alert: any): number => {
  const desc = alert?.description || ''
  const m = desc.match(/持续.*?(\d+)/)
  const n = m ? parseFloat(m[1]) : 0
  return isNaN(n) ? 0 : n
}

// 预计结束时间（毫秒）：产生时间 + 持续分钟；算不出返回 null
const getAlertEndMs = (alert: any): number | null => {
  const startMs = parseAlertStartMs(alert)
  if (startMs === null) return null
  const durMin = getAlertDurationMin(alert)
  if (durMin <= 0) return null
  return startMs + durMin * 60000
}

const pad2 = (n: number) => String(n).padStart(2, '0')

// 给模板用的结构化信息：地点 / 密度 / 预计持续到
const getAlertInfo = (alert: any) => {
  const nodeName = alert.nodeName || alert.nodeId || '未知节点'
  const desc = alert.description || ''
  const densityMatch = desc.match(/密度.*?([\d.]+)/)
  const density = densityMatch ? densityMatch[1] : '未知'

  const endMs = getAlertEndMs(alert)
  let endText = '未知'
  if (endMs !== null) {
    const d = new Date(endMs)
    endText = `${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`
  }
  return { nodeName, density, endText }
}
// ===== OPENCODE-EDIT-15 END =====

const getAlertColor = (level: string) => {
  if (level === 'red') return '#FF0000' 
  if (level === 'orange') return '#FF8C00' 
  return '#FFD700' 
}

const getAlertBgColor = (level: string) => {
  if (level === 'red') return 'rgba(255, 0, 0, 0.15)'
  if (level === 'orange') return 'rgba(255, 140, 0, 0.15)'
  return 'rgba(255, 215, 0, 0.15)' 
}

const getAlertLabel = (level: string) => {
  if (level === 'red') return '高度风险'
  if (level === 'orange') return '中度风险'
  return '轻度风险' 
}

// ===== OPENCODE-EDIT-15 同地点去重 + 自动处理相关状态 =====
const autoResolvedIds = new Set<string>()
const resolvingSet = new Set<string>()
// ===== OPENCODE-EDIT-15 END =====

const fetchAlertsList = async () => {
  try {
    const alertsRes: any = await request({
      url: '/api/v1/security/alerts',
      method: 'get',
      params: { pageSize: 100, status: 'pending' }
    })
    if (alertsRes.code === 200 || alertsRes.code === 0) {
      const rawList = alertsRes.data?.items || alertsRes.data || []
      // 过滤掉已自动处理过的 id，防止后端未及时更新又弹回
      const filtered = Array.isArray(rawList) ? rawList.filter((a: any) => !autoResolvedIds.has(a.alertId)) : []
      // 按 nodeId 去重：同地点已有「未过期」预警则跳过新重复项；已过期则用新预警替换
      const now = Date.now()
      const dedupMap = new Map<string, any>()
      filtered.forEach((alert: any) => {
        const key = alert.nodeId || alert.nodeName || alert.alertId
        const existing = dedupMap.get(key)
        if (!existing) { dedupMap.set(key, alert); return }
        const existingEnd = getAlertEndMs(existing)
        if (existingEnd === null || existingEnd <= now) {
          dedupMap.set(key, alert)
        }
      })
      alertList.value = Array.from(dedupMap.values())
    }
  } catch (e) {
    console.error("预警接口请求失败", e)
  }
}

// 到期自动处理（不弹窗）：达到预计结束时间后静默 resolve
const autoResolveAlert = async (alert: any) => {
  if (!alert?.alertId || resolvingSet.has(alert.alertId)) return
  resolvingSet.add(alert.alertId)
  try {
    const res: any = await request({
      url: `/api/v1/security/alerts/${alert.alertId}/resolve`,
      method: 'put',
      data: { actionTaken: '自动解除（达到预计结束时间）' }
    })
    if (res.code === 0 || res.code === 200) {
      autoResolvedIds.add(alert.alertId)
      alertList.value = alertList.value.filter(a => a.alertId !== alert.alertId)
    } else {
      console.warn('自动处理预警失败:', res.message)
    }
  } catch (err) {
    console.warn('自动处理预警发生异常', err)
  } finally {
    resolvingSet.delete(alert.alertId)
  }
}

// 每秒扫描：到期预警自动处理
const sweepExpiredAlerts = () => {
  const now = Date.now()
  alertList.value.forEach((alert: any) => {
    const endMs = getAlertEndMs(alert)
    if (endMs !== null && endMs <= now) {
      autoResolveAlert(alert)
    }
  })
}
// ===== OPENCODE-EDIT-15 END =====

const handleResolveAlert = async (alertId: string) => {
  if (!alertId) return alert('预警 ID 异常')
  const actionTaken = prompt('请输入处置措施（例如：已安排保安前往疏导、门闸已限流）', '已派发安保人员前往疏散')
  if (!actionTaken) return 
  try {
    const res: any = await request({
      url: `/api/v1/security/alerts/${alertId}/resolve`,
      method: 'put',
      data: { actionTaken } 
    })
    if (res.code === 0 || res.code === 200) {
      alert('✅ 预警处理成功！')
      await fetchAlertsList() 
    } else {
      alert(`处理失败: ${res.message}`)
    }
  } catch (err) {
    console.error("处理预警发生异常", err)
  }
}

const goToAdmin = () => router.push('/admin')

const changeAlgo = (type: 'pagerank' | 'betweenness' | 'heatScore') => {
  algoType.value = type
  renderMainChart()
}

const switchMode = (mode: 'heatmap' | 'topology' | 'history' | 'cav') => {
  if (mode !== 'history' && isPlaying.value) togglePlay()
  if (mode !== 'cav') stopCavAnim()
  currentMode.value = mode
  nextTick(() => {
    if (mode === 'topology') {
      if (mainChart) mainChart.resize()
      renderMainChart()
    } else if (mode === 'history') {
      loadHistorySnapshot()
    } else if (mode === 'cav') {
      if (mainChart) mainChart.resize()
      loadCavDemo()
    } else {
      renderBaiduMap3D()
    }
  })
}

onMounted(async () => {
  mainChart = echarts.init(chartRef.value!)
  
  await fetchAlertsList() 
  await fetchDynamicData()

  try {
    await loadBaiduMapSDK();
    renderBaiduMap3D();
  } catch(e) {
    console.warn("百度地图 SDK 加载失败，可能缺少 AK", e)
  }

  mainChart.on('click', (params: any) => {
    if (params.dataType === 'node') {
      currentNode.value = params.data
      showModal.value = true
      nextTick().then(() => renderPredictChart(currentNode.value.id))
    }
  })
  window.addEventListener('resize', handleResize)

  // ===== OPENCODE-EDIT-07 实时轮询定时器 =====
  // 1 秒数据轮询：密度/算法数值/热力图拥堵（带防重入，后端失败不堆积请求）
  const dataTick = async () => {
    if (isRefreshing) return
    // ===== OPENCODE-EDIT-16 历史/CAV 模式暂停实时轮询，避免实时数据覆盖专用视图 =====
    if (currentMode.value === 'history' || currentMode.value === 'cav') return
    // ===== OPENCODE-EDIT-16 END =====
    isRefreshing = true
    try {
      const ok = await fetchDynamicData()
      if (ok) {
        renderMainChart()
        updateBaiduMapCongestion()
        dataFailCount = 0
        dataStatus.value = 'online'
      } else {
        dataFailCount++
        if (dataFailCount >= 3) dataStatus.value = 'offline'
      }
    } catch (e) {
      dataFailCount++
      if (dataFailCount >= 3) dataStatus.value = 'offline'
      console.warn('数据轮询失败', e)
    } finally {
      isRefreshing = false
    }
  }
  dataTimer = window.setInterval(dataTick, 1000)

  // ===== OPENCODE-EDIT-15 每秒扫描到期预警，自动解除（无需人工点击） =====
  sweepTimer = window.setInterval(() => sweepExpiredAlerts(), 1000)
  // ===== OPENCODE-EDIT-15 END =====

  // 1 分钟预警轮询
  alertTimer = window.setInterval(() => {
    fetchAlertsList().catch((e: any) => console.warn('预警轮询失败', e))
  }, 60000)
  // ===== OPENCODE-EDIT-07 END =====
})

onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
  // ===== OPENCODE-EDIT-07 清理定时器 =====
  if (dataTimer) window.clearInterval(dataTimer)
  if (alertTimer) window.clearInterval(alertTimer)
  // ===== OPENCODE-EDIT-16 清理历史回放定时器 =====
  if (playTimer) window.clearInterval(playTimer)
  // ===== OPENCODE-EDIT-16 END =====
  // ===== OPENCODE-EDIT-19 清理 CAV 动画 =====
  stopCavAnim()
  // ===== OPENCODE-EDIT-19 END =====
  // ===== OPENCODE-EDIT-15 清理到期自动扫描定时器 =====
  if (sweepTimer) window.clearInterval(sweepTimer)
  // ===== OPENCODE-EDIT-15 END =====
  // ===== OPENCODE-EDIT-07 END =====
  mainChart?.dispose()
  predictChart?.dispose()
})

const handleResize = () => {
  mainChart?.resize()
  predictChart?.resize()
}

const fetchDynamicData = async (): Promise<boolean> => {
  try {
    const nodeRes: any = await getNodesDataAPI()
    if ((nodeRes.code === 200 || nodeRes.code === 0) && nodeRes.data) {
      const nodeArray = Array.isArray(nodeRes.data.nodes) ? nodeRes.data.nodes
        : (Array.isArray(nodeRes.data) ? nodeRes.data : [])
      nodeArray.forEach((item: any) => { nodeDynamicDataMap[item.nodeId || item.nodeName] = item })
      // ===== OPENCODE-EDIT-11 读取后端仿真时间（data.simTime，随拥堵数据每秒刷新） =====
      simTime.value = nodeRes.data.simTime ?? null
      // ===== OPENCODE-EDIT-11 END =====
    }

    const hotnessRes: any = await request({ url: '/api/v1/network/hotness', method: 'get' })
    if ((hotnessRes.code === 200 || hotnessRes.code === 0)) {
      const items = Array.isArray(hotnessRes.data?.items) ? hotnessRes.data.items
        : (Array.isArray(hotnessRes.items) ? hotnessRes.items : [])
      items.forEach((item: any) => {
        const realId = getRealId(item.nodeId)
        if (!hotnessDataMap.value[realId]) hotnessDataMap.value[realId] = { nodeId: realId }
        hotnessDataMap.value[realId] = { ...hotnessDataMap.value[realId], ...item }
      })
    }

    const hotspotsRes: any = await request({ url: '/api/v1/network/hotspots', method: 'get' })
    if ((hotspotsRes.code === 200 || hotspotsRes.code === 0)) {
      const hotspots = Array.isArray(hotspotsRes.data?.hotspots) ? hotspotsRes.data.hotspots
        : (Array.isArray(hotspotsRes.hotspots) ? hotspotsRes.hotspots : [])
      hotspotsList.value = hotspots 
      
      // 强制清空幽灵数据，防止未受算法影响的节点被错误放大
      Object.values(hotnessDataMap.value).forEach((node: any) => {
        node.heatScore = 0;
      });

      hotspots.forEach((spot: any) => {
        const score = spot.attractScore || 0
        let nodes = spot.nodeIds || []
        if (typeof nodes === 'string') nodes = [nodes]
        if (!Array.isArray(nodes)) nodes = []
        
        nodes.forEach((nId: string) => {
          const realId = getRealId(nId)
          if (!hotnessDataMap.value[realId]) {
            hotnessDataMap.value[realId] = { nodeId: realId }
          }
          hotnessDataMap.value[realId].heatScore = score
        })
      })
    }
  } catch (err) {
    console.error('动态数据拉取失败', err)
    return false
  }
  return true
}

const calculatePath = async () => {
  if (!pathSrc.value || !pathDst.value) return alert('请先选择起点和终点！')
  try {
    const res: any = await getShortestPathAPI(pathSrc.value, pathDst.value)
    if (res.code === 0 || res.code === 200) {
      const pathArray = res.data.path || []
      travelTime.value = res.data.travelTime || 0
      shortestPathEdges.value.clear()
      for (let i = 0; i < pathArray.length - 1; i++) {
        shortestPathEdges.value.add(`${pathArray[i]}-${pathArray[i+1]}`)
        shortestPathEdges.value.add(`${pathArray[i+1]}-${pathArray[i]}`) 
      }
      renderMainChart()
    } else {
      alert('无法计算最短路径：' + res.message)
    }
  } catch (e) {
    console.error("路径规划失败", e)
  }
}

const clearPath = () => {
  pathSrc.value = ''
  pathDst.value = ''
  travelTime.value = 0
  shortestPathEdges.value.clear()
  renderMainChart()
}

const renderMainChart = () => {
  if (!mainChart) return

  const nodeDensityMap: Record<string, number> = {}
  const nodeSizeMap: Record<string, number> = {}

  const graphNodes = topologyNodes.map((n: any) => {
    const dynamicData = nodeDynamicDataMap[n.name] || nodeDynamicDataMap[n.id] || {}
    const hotnessData = hotnessDataMap.value[n.id] || {}
    
    const density = parseFloat(dynamicData.density) || 0.1
    
    let algoScore = 0;
    if (algoType.value === 'heatScore') {
      const spot = hotspotsList.value.find(s => {
        let ids = s.nodeIds || [];
        if (typeof ids === 'string') ids = [ids]; 
        return ids.some((id: string) => getRealId(id) === n.id);
      });
      if (spot) algoScore = parseFloat(spot.attractScore) || 0;
    } else {
      algoScore = parseFloat(hotnessData[algoType.value]) || 0;
    }
    
    nodeDensityMap[n.id] = density
    nodeDensityMap[n.name] = density

    const baseColor = (topologyData.node_colors as any)[n.type] || '#c4b5fd'

    let symbolSize = 30 
    let itemColor = baseColor

    if (currentMode.value === 'heatmap') {
      symbolSize = 34 
      if (density >= 0.8) itemColor = '#FF0000' 
      else if (density >= 0.5) itemColor = '#FF8C00' 
      else itemColor = '#2ED573' 
    } else {
      symbolSize = 15 + (algoScore / maxAlgoValue.value) * 45
      if (shortestPathEdges.value.size > 0 && 
         (pathSrc.value === n.id || pathDst.value === n.id || 
          Array.from(shortestPathEdges.value).some(edge => edge.includes(n.id)))) {
        itemColor = '#facc15' 
      } else {
        itemColor = baseColor 
      }
    }

    nodeSizeMap[n.id] = symbolSize

    return {
      id: n.id,
      name: n.name,
      type: n.type,
      value: [n.x, n.y],
      density: density, 
      algoScore: Number(algoScore).toFixed(4),
      symbol: 'circle', 
      symbolSize,
      itemStyle: { 
        color: itemColor, 
        shadowBlur: currentMode.value === 'heatmap' ? 18 : 15, 
        shadowColor: itemColor 
      }
    }
  })

  const graphEdges = topologyData.edges.map((e: any) => {
    const sourceDensity = nodeDensityMap[e.source] || 0.1
    const targetDensity = nodeDensityMap[e.target] || 0.1
    const roadDensity = Math.max(sourceDensity, targetDensity)

    // ★ 永远保持清爽细长的道路宽度！
    const roadRadiusWidth = 15 

    let roadColor = '#a855f7' 
    let lineWidth = roadRadiusWidth
    let isShortestPath = false

    if (currentMode.value === 'heatmap') {
      if (roadDensity >= 0.8) roadColor = '#FF0000'      
      else if (roadDensity >= 0.5) roadColor = '#FF8C00' 
      else roadColor = '#2ED573'                         
    } else {
      if (shortestPathEdges.value.has(`${e.source}-${e.target}`)) {
        isShortestPath = true
        roadColor = '#facc15' 
        lineWidth = Math.max(roadRadiusWidth, 6) 
      }
    }

    return {
      source: e.source,
      target: e.target,
      lineStyle: { 
        width: lineWidth, 
        opacity: currentMode.value === 'heatmap' ? 0.7 : (isShortestPath ? 1 : 0.25), 
        color: roadColor,
        shadowBlur: isShortestPath ? 20 : 0, 
        shadowColor: '#facc15',
        curveness: 0.05 
      },
      z: isShortestPath ? 100 : 1 
    }
  })

  const option = {
    backgroundColor: 'transparent',
    tooltip: { 
      trigger: 'item',
      backgroundColor: 'rgba(15, 10, 30, 0.85)',
      borderColor: '#a855f7',
      textStyle: { color: '#fff' },
      formatter: function(params: any) {
        if (params.dataType === 'node') {
          if (currentMode.value === 'heatmap') {
            return `📍 ${params.data.name}<br/>实时密度: <b style="color:#FF4757">${params.data.density}</b>`;
          } else {
            return `📍 ${params.data.name}<br/>${algoNameMap[algoType.value as keyof typeof algoNameMap]}: <b style="color:#facc15">${params.data.algoScore}</b>`;
          }
        }
        return params.name;
      }
    },
    grid: { top: '2%', bottom: '2%', left: '2%', right: '2%' },
    xAxis: { type: 'value', min: 0, max: 100, show: false },
    yAxis: { type: 'value', min: 0, max: 100, show: false },
    dataZoom: [{ type: 'inside', xAxisIndex: 0, yAxisIndex: 0, filterMode: 'empty' }],
    series: [
      {
        type: 'graph',
        coordinateSystem: 'cartesian2d',
        data: graphNodes,
        links: graphEdges,
        label: { 
          show: true, 
          position: 'inside', 
          color: '#ffffff',   
          fontWeight: 'bold', 
          fontSize: 12,
          textBorderColor: '#000',
          textBorderWidth: 2
        }
      }
    ]
  }
  mainChart.setOption(option, true)
}

// ===== OPENCODE-EDIT-13 节点预测曲线：改用 /api/v1/network/predictions（history + 当前快照），标题 30 分钟 → 10 分钟 =====
const predictEmpty = ref(true)

/* 旧代码（OPENCODE-EDIT-13 注释保留，可回退）
const renderPredictChart = async (nodeId: string) => {
  if (predictChartRef.value) {
    predictChart = echarts.init(predictChartRef.value)
    let times: string[] = []
    let values: number[] = []
    try {
      const res: any = await getPredictionDataAPI(nodeId)
      if ((res.code === 200 || res.code === 0) && res.data) {
        times = res.data.timestamps
        values = res.data.predictedValues
      }
    } catch (e) {
      console.error("预测数据获取失败")
    }

    const option = {
      tooltip: { trigger: 'axis' },
      xAxis: { type: 'category', data: times, axisLabel: { color: '#c4b5fd' } },
      yAxis: { type: 'value', max: 1.0, axisLabel: { color: '#c4b5fd' } },
      series: [{
        name: '预测密度',
        type: 'line',
        data: values,
        smooth: true,
        lineStyle: { color: '#a855f7', width: 3 },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(168,85,247,0.5)' },
            { offset: 1, color: 'rgba(168,85,247,0)' }
          ])
        },
        markLine: {
          data: [{ yAxis: 0.8, name: '拥堵警戒线' }],
          lineStyle: { color: '#FF0000', type: 'dashed' }
        }
      }]
    }
    predictChart.setOption(option)
  }
}
*/

// 节点 ID 规范化匹配：忽略大小写与下划线（如 canteen_1 ↔ canteen1、gate_south ↔ gatesouth）
const normalizeNodeKey = (k: string) => k.toLowerCase().replace(/[^a-z0-9]/g, '')

const matchPredictionKey = (stats: Record<string, any>, nodeId: string) => {
  if (!stats || typeof stats !== 'object') return null
  if (stats[nodeId] !== undefined) return nodeId
  const norm = normalizeNodeKey(nodeId)
  return Object.keys(stats).find(k => normalizeNodeKey(k) === norm) || null
}

const renderPredictChart = async (nodeId: string) => {
  if (!predictChartRef.value) return
  predictChart = echarts.init(predictChartRef.value)
  let times: string[] = []
  let values: number[] = []
  try {
    // ?limit=60：拉最近约 10 分钟的历史快照（每帧间隔约 10s）
    const res: any = await getNetworkPredictionsAPI(60)
    if ((res.code === 200 || res.code === 0) && res.data) {
      const prediction = res.data.prediction
      const history = Array.isArray(res.data.history) ? res.data.history : []
      const key = matchPredictionKey(prediction?.densityStats, nodeId)
      if (key) {
        const snapshots = [...history, prediction].filter((s: any) => s && s.densityStats)
        snapshots.forEach((snap: any) => {
          const val = parseFloat(snap.densityStats?.[key])
          const ts = snap.timestamp
          if (!isNaN(val) && ts) {
            times.push(ts.length >= 19 ? ts.slice(11, 19) : ts)
            values.push(val)
          }
        })
      }
    }
  } catch (e) {
    console.error("预测数据获取失败", e)
  }

  predictEmpty.value = values.length === 0
  if (values.length === 0) return

  // ===== OPENCODE-EDIT-18 后端预测值可能 >1（如 canteen1: 3.6），Y 轴动态上限避免曲线被截断 =====
  const dataMax = Math.max(...values, 0)
  const yMax = Math.max(dataMax * 1.15, 1)
  // ===== OPENCODE-EDIT-18 END =====

  const option = {
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: times, axisLabel: { color: '#c4b5fd' } },
    yAxis: { type: 'value', max: yMax, axisLabel: { color: '#c4b5fd' } },
    series: [{
      name: '预测密度',
      type: 'line',
      data: values,
      smooth: true,
      lineStyle: { color: '#a855f7', width: 3 },
      areaStyle: {
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: 'rgba(168,85,247,0.5)' },
          { offset: 1, color: 'rgba(168,85,247,0)' }
        ])
      },
      markLine: {
        data: [{ yAxis: 0.8, name: '拥堵警戒线' }],
        lineStyle: { color: '#FF0000', type: 'dashed' }
      }
    }]
  }
  predictChart.setOption(option)
}
// ===== OPENCODE-EDIT-13 END =====

const closeModal = () => {
  showModal.value = false
  predictChart?.dispose()
}
</script>

<style scoped>
.dashboard-layout {
  height: 100vh;
  background-color: #0b0714; 
  position: relative; 
  display: flex;
  flex-direction: column;
}
.top-header {
  height: 70px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0 30px;
  background-color: #130b21; 
  border-bottom: 1px solid #2d1b4e;
  z-index: 10;
}
.glow-text {
  color: #fff;
  font-size: 22px;
  margin: 0;
  text-shadow: 0 0 10px rgba(168, 85, 247, 0.8);
}
/* ===== OPENCODE-EDIT-07 实时连接状态指示器样式 ===== */
.status-indicator {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 14px;
  border-radius: 20px;
  background: rgba(46, 213, 115, 0.12);
  border: 1px solid rgba(46, 213, 115, 0.5);
}
.status-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: #2ED573;
  box-shadow: 0 0 8px #2ED573;
  animation: status-pulse 1.6s infinite;
}
.status-text {
  color: #2ED573;
  font-size: 13px;
  font-weight: bold;
}
.status-indicator.offline {
  background: rgba(255, 61, 61, 0.12);
  border-color: rgba(255, 61, 61, 0.5);
}
.status-indicator.offline .status-dot {
  background: #FF3B30;
  box-shadow: 0 0 8px #FF3B30;
  animation: none;
}
.status-indicator.offline .status-text {
  color: #FF3B30;
}
@keyframes status-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.35; }
}
/* ===== OPENCODE-EDIT-07 END ===== */
/* ===== OPENCODE-EDIT-10 悬浮连接状态指示器 + 热力图图例样式 ===== */
.map-float-status {
  position: absolute;
  top: 30px;
  right: 30px;
  z-index: 20;
  background: rgba(11, 7, 20, 0.7);
}
.map-legend {
  position: absolute;
  left: 30px;
  bottom: 30px;
  z-index: 20;
  padding: 12px 16px;
  border-radius: 8px;
  background: rgba(11, 7, 20, 0.75);
  border: 1px solid #2d1b4e;
  font-size: 13px;
  color: #c4b5fd;
  pointer-events: none;
}
.legend-title {
  font-weight: bold;
  color: #fff;
  margin-bottom: 8px;
}
.legend-item {
  display: flex;
  align-items: center;
  gap: 8px;
  line-height: 1.7;
}
.legend-color {
  width: 14px;
  height: 14px;
  border-radius: 3px;
  flex-shrink: 0;
}
/* ===== OPENCODE-EDIT-18 图例点击提示 ===== */
.legend-tip {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid rgba(168, 85, 247, 0.25);
  font-size: 11px;
  color: #a855f7;
  line-height: 1.5;
}
/* ===== OPENCODE-EDIT-18 END ===== */
/* ===== OPENCODE-EDIT-10 END ===== */
/* ===== OPENCODE-EDIT-11 仿真时间悬浮面板样式（左上角） ===== */
.map-float-time {
  position: absolute;
  top: 30px;
  left: 30px;
  z-index: 20;
  min-width: 160px;
  padding: 10px 16px;
  border-radius: 8px;
  background: rgba(11, 7, 20, 0.75);
  border: 1px solid #2d1b4e;
  color: #c4b5fd;
  pointer-events: none;
  box-shadow: 0 0 12px rgba(168, 85, 247, 0.15);
}
.map-float-time.offline {
  opacity: 0.6;
}
.time-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 4px;
}
.time-label {
  font-size: 11px;
  font-weight: bold;
  letter-spacing: 1px;
  color: #a855f7;
}
.time-date {
  font-size: 12px;
  font-family: monospace;
  color: #c4b5fd;
}
.time-clock {
  font-size: 30px;
  font-weight: bold;
  line-height: 1.2;
  letter-spacing: 2px;
  font-family: 'Courier New', monospace;
  color: #d8b4fe;
  text-shadow: 0 0 10px rgba(168, 85, 247, 0.8);
}
/* ===== OPENCODE-EDIT-11 END ===== */
.mode-switch {
  display: flex;
  gap: 15px;
}
.mode-switch button {
  background: #1e1332;
  color: #c4b5fd;
  border: 1px solid #3b2563;
  padding: 10px 20px;
  font-size: 16px;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.3s;
}
.mode-switch button.active {
  background: linear-gradient(90deg, #7c3aed, #9333ea);
  color: white;
  border-color: #a855f7;
  box-shadow: 0 0 15px rgba(168, 85, 247, 0.5);
}

.main-body {
  flex: 1;
  display: flex;
  overflow: hidden;
  position: relative;
}

.left-panel {
  width: 320px;
  background-color: rgba(19, 11, 33, 0.8);
  backdrop-filter: blur(10px);
  border-right: 1px solid #2d1b4e;
  padding: 20px;
  overflow-y: auto;
  z-index: 5;
  display: flex;
  flex-direction: column;
}
.panel-title { 
  color: #e9d5ff; 
  border-bottom: 1px solid #3b2563; 
  padding-bottom: 12px; 
  margin-top: 0;
}
.subtitle {
  color: #a855f7;
  font-size: 12px;
  margin-top: -5px;
  margin-bottom: 20px;
}
.alert-list { padding: 0; margin: 0; list-style: none; }
.alert-item {
  padding: 12px;
  margin-bottom: 12px;
  border-radius: 6px;
  font-size: 14px;
  border-left: 4px solid transparent; 
  line-height: 1.5;
}
.alert-content-wrapper {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
}
.alert-content { flex: 1; }
/* ===== OPENCODE-EDIT-15 预警信息分行展示样式 ===== */
.alert-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  margin-bottom: 6px;
}
.alert-line {
  line-height: 1.6;
  font-size: 13px;
}
/* ===== OPENCODE-EDIT-15 END ===== */
.empty-text { color: #2ED573; font-size: 14px; margin-top: 10px;}

.topology-menu .nav-menu {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
}
.topology-menu .nav-menu li {
  position: relative;
  margin: 15px 0;
  padding: 15px;
  cursor: pointer;
  transition: all 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275);
  background: linear-gradient(90deg, transparent 0%, rgba(168, 85, 247, 0.15) 20%, transparent 100%);
  border-radius: 4px;
  color: rgba(255, 255, 255, 0.6);
  font-weight: bold;
}
.topology-menu .nav-menu li:hover, .topology-menu .nav-menu li.active {
  color: #fff;
  background: linear-gradient(90deg, #7c3aed, #a855f7);
  transform: translateX(15px) scale(1.02);
  border-radius: 4px 12px 12px 4px;
  border-left: 5px solid #fff;
  box-shadow: 5px 5px 15px rgba(124, 58, 237, 0.6);
}
.topology-menu .tab-text small {
  font-weight: normal;
  font-size: 11px;
  opacity: 0.8;
  display: block;
  margin-top: 4px;
}

.right-panel {
  width: 320px;
  background-color: rgba(19, 11, 33, 0.8);
  backdrop-filter: blur(10px);
  border-left: 1px solid #2d1b4e;
  padding: 20px;
  z-index: 5;
  display: flex;
  flex-direction: column;
  justify-content: space-between; 
  height: 100%; 
}

.ranking-box {
  height: 64%; 
  flex: none;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.ranking-list {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 15px;
}
.rank-item {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.rank-info {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 13px;
  color: #fff;
}
.rank-num { font-weight: bold; font-style: italic; margin-right: 8px;}
.top-1 { color: #facc15; font-size: 16px;}
.top-2 { color: #e2e8f0; font-size: 15px;}
.top-3 { color: #d97706; font-size: 14px;}
.rank-name { flex: 1; color: #c4b5fd; }
.rank-score { font-family: monospace; color: #a855f7; }

.bar-track {
  width: 100%;
  height: 8px;
  background: rgba(168, 85, 247, 0.1);
  border-radius: 4px;
  overflow: hidden;
}
.bar-fill {
  height: 100%;
  background: linear-gradient(90deg, #7c3aed, #d8b4fe);
  border-radius: 4px;
  transition: width 0.8s cubic-bezier(0.175, 0.885, 0.32, 1.275);
  box-shadow: 0 0 10px rgba(168, 85, 247, 0.8);
}

.hud-card-box {
  height: 32%;
  flex: none; 
  position: relative;
  background: linear-gradient(145deg, rgba(20, 12, 38, 0.7), rgba(15, 10, 30, 0.9));
  border: 1px solid rgba(168, 85, 247, 0.15); 
  border-radius: 8px;
  padding: 15px;
  box-shadow: inset 0 0 20px rgba(168, 85, 247, 0.05), 0 4px 15px rgba(0, 0, 0, 0.3);
  overflow: hidden;
  display: flex;
  flex-direction: column;
  justify-content: center;
}

.bracket {
  position: absolute;
  width: 20px;
  height: 20px;
  border: 2px solid #a855f7;
  transition: all 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275);
  pointer-events: none; 
  z-index: 5;
}
.bracket.top-left {
  top: 2px; left: 2px; 
  border-right: none; border-bottom: none;
  border-top-left-radius: 6px;
}
.bracket.bottom-right {
  bottom: 2px; right: 2px; 
  border-left: none; border-top: none;
  border-bottom-right-radius: 6px;
}
.hud-card-box:hover .bracket {
  width: 26px; height: 26px;
  border-color: #facc15; 
  box-shadow: 0 0 10px rgba(250, 204, 21, 0.4);
}

.laser-track {
  position: absolute;
  top: 2px; left: 2px; right: 2px; bottom: 2px; 
  border-radius: 6px;
  z-index: 1;
  pointer-events: none;
}
.laser {
  position: absolute;
  display: none; 
  pointer-events: none;
}
.hud-card-box:hover .laser {
  display: block;
}
.laser.top {
  top: 0; left: -100%; width: 100%; height: 2px;
  background: linear-gradient(90deg, transparent, #facc15);
  animation: traceTop 2s linear infinite;
}
@keyframes traceTop {
  0% { left: -100%; }
  50%, 100% { left: 100%; }
}
.laser.right {
  top: -100%; right: 0; width: 2px; height: 100%;
  background: linear-gradient(180deg, transparent, #facc15);
  animation: traceRight 2s linear infinite;
  animation-delay: 0.5s; 
}
@keyframes traceRight {
  0% { top: -100%; }
  50%, 100% { top: 100%; }
}
.laser.bottom {
  bottom: 0; right: -100%; width: 100%; height: 2px;
  background: linear-gradient(270deg, transparent, #facc15);
  animation: traceBottom 2s linear infinite;
  animation-delay: 1s; 
}
@keyframes traceBottom {
  0% { right: -100%; }
  50%, 100% { right: 100%; }
}
.laser.left {
  bottom: -100%; left: 0; width: 2px; height: 100%;
  background: linear-gradient(360deg, transparent, #facc15);
  animation: traceLeft 2s linear infinite;
  animation-delay: 1.5s;
}
@keyframes traceLeft {
  0% { bottom: -100%; }
  50%, 100% { bottom: 100%; }
}

.hud-content-inner {
  position: relative;
  z-index: 10;
}
.hud-title-inline {
  color: #facc15;
  font-size: 14px;
  border-bottom: 1px solid rgba(250, 204, 21, 0.2);
  padding-bottom: 8px;
  margin-bottom: 12px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.hud-title-inline::before {
  content: '';
  display: inline-block;
  width: 8px;
  height: 8px;
  background-color: #facc15;
  border-radius: 50%;
  box-shadow: 0 0 8px #facc15;
  animation: radarPulse 2s infinite;
}
@keyframes radarPulse {
  0% { box-shadow: 0 0 0 0 rgba(250, 204, 21, 0.7); }
  70% { box-shadow: 0 0 0 6px rgba(250, 204, 21, 0); }
  100% { box-shadow: 0 0 0 0 rgba(250, 204, 21, 0); }
}

.path-controls-vertical {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.select-row {
  display: flex;
  align-items: center;
  gap: 6px;
}
.arrow-v { color: #a855f7; font-weight: bold; }
.full-select {
  flex: 1;
  width: 100%;
  padding: 6px;
  font-size: 12px;
}
.btn-row {
  display: flex;
  gap: 10px;
}
.flex-btn {
  flex: 1;
  padding: 6px;
  font-size: 12px;
  letter-spacing: 1px;
}
.small-btn {
  padding: 6px 12px;
  font-size: 12px;
  white-space: nowrap;
}
.time-display-inline {
  color: #c4b5fd;
  font-size: 12px;
  margin-top: 10px;
  text-align: center;
}

.center-panel { 
  flex: 1; 
  position: relative;
}
.chart-container {
  position: absolute;
  top: 20px; left: 20px; right: 20px; bottom: 20px;
  background-image: 
    linear-gradient(rgba(147, 51, 234, 0.15) 1px, transparent 1px),
    linear-gradient(90deg, rgba(147, 51, 234, 0.15) 1px, transparent 1px);
  background-size: 40px 40px; 
  border-radius: 8px;
  overflow: hidden;
}

#bmap-container {
  background-image: none;
  background-color: #0b0714;
}

:deep(.BMap_cpyCtrl), :deep(.anchorBL) {
  display: none !important;
}

.cyber-select {
  background: rgba(0,0,0,0.5);
  border: 1px solid #a855f7;
  color: #fff;
  border-radius: 4px;
  outline: none;
}
.cyber-btn {
  background: linear-gradient(90deg, #7c3aed, #9333ea);
  color: #fff;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  font-weight: bold;
  box-shadow: 0 0 10px rgba(168, 85, 247, 0.4);
}
.cyber-btn:hover { background: linear-gradient(90deg, #8b5cf6, #a855f7);}
.cyber-btn.ghost {
  background: transparent;
  border: 1px solid #FF4757;
  color: #FF4757;
  box-shadow: none;
}
.cyber-btn.ghost:hover {
  background: rgba(255, 71, 87, 0.2);
}

.hud-back-btn {
  position: fixed; 
  bottom: 30px;
  left: 30px;
  width: 100px;
  height: 100px;
  cursor: pointer;
  z-index: 100;
  display: flex;
  justify-content: center;
  align-items: center;
  transition: transform 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275);
}
.hud-back-btn:hover { transform: scale(1.15); }
.hud-icon {
  width: 55px;
  height: 55px;
  background: rgba(147, 51, 234, 0.3);
  border: 2px solid #a855f7;
  border-radius: 50%;
  display: flex;
  justify-content: center;
  align-items: center;
  text-align: center;
  box-shadow: 0 0 15px rgba(168, 85, 247, 0.6), inset 0 0 10px rgba(168, 85, 247, 0.4);
  z-index: 2;
  backdrop-filter: blur(4px);
}
.hud-text {
  color: #fff;
  font-size: 11px;
  font-weight: bold;
  letter-spacing: 1px;
  line-height: 1.2;
  text-shadow: 0 0 5px #fff;
}
.hud-ring {
  position: absolute;
  width: 100%;
  height: 100%;
  border-radius: 50%;
  border: 2px dashed rgba(196, 181, 253, 0.3);
  border-top-color: #d8b4fe; 
  border-bottom-color: #a855f7; 
  animation: hudSpin 5s linear infinite;
  z-index: 1;
}
@keyframes hudSpin { 100% { transform: rotate(360deg); } }

.modal-overlay {
  position: fixed; top: 0; left: 0; right: 0; bottom: 0;
  background: rgba(0,0,0,0.75);
  display: flex; justify-content: center; align-items: center;
  z-index: 1000;
}
.modal-box {
  background: #130b21;
  border: 1px solid #a855f7;
  padding: 25px;
  border-radius: 10px;
  width: 450px;
  color: #fff;
  box-shadow: 0 0 20px rgba(168, 85, 247, 0.3);
}
.predict-chart { width: 100%; height: 260px; margin-top: 15px;}
.predict-empty {
  color: #c4b5fd;
  font-size: 14px;
  text-align: center;
  padding: 40px 0;
}
.close-btn {
  width: 100%; margin-top: 20px; padding: 12px;
  background: linear-gradient(90deg, #7c3aed, #9333ea); 
  color: #fff; border: none; border-radius: 6px; cursor: pointer;
  font-size: 16px;
}
.close-btn:hover { background: linear-gradient(90deg, #8b5cf6, #a855f7); }

/* ===== OPENCODE-EDIT-16 历史热力图：老虎机滚轮选择器 + 回放面板 ===== */
.reel-group {
  display: flex;
  gap: 8px;
  margin-bottom: 4px;
}
.reel-col {
  flex: 1;
  min-width: 0;
}
.reel-label {
  text-align: center;
  font-size: 11px;
  font-weight: bold;
  letter-spacing: 2px;
  color: #a855f7;
  text-transform: uppercase;
  margin: 0 0 6px;
}
.wheel-container {
  height: 156px;
  overflow: hidden;
  position: relative;
  border-radius: 8px;
  background: rgba(168, 130, 255, 0.04);
  border: 1px solid rgba(168, 130, 255, 0.12);
  cursor: pointer;
  mask-image: linear-gradient(to bottom, transparent 0%, black 30%, black 70%, transparent 100%);
  -webkit-mask-image: linear-gradient(to bottom, transparent 0%, black 30%, black 70%, transparent 100%);
}
.wheel-track {
  transition: transform 0.2s cubic-bezier(0.25, 0.46, 0.45, 0.94);
}
.wheel-item {
  height: 52px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  font-size: 1.2rem;
  font-weight: 700;
  font-family: 'Courier New', monospace;
  color: rgba(168, 130, 255, 0.3);
  transition: all 0.2s;
  user-select: none;
}
.wheel-item b {
  font-size: 13px;
  line-height: 1.1;
  font-family: 'Microsoft YaHei', sans-serif;
}
.wheel-item small {
  font-size: 10px;
  line-height: 1.1;
  font-family: 'Microsoft YaHei', sans-serif;
  opacity: 0.8;
}
.wheel-item.active {
  color: #c084fc;
  text-shadow: 0 0 16px rgba(192, 132, 252, 0.6);
  font-size: 1.5rem;
}
.wheel-item.active b {
  font-size: 16px;
  color: #e9d5ff;
}
.wheel-item.active small {
  font-size: 11px;
  color: #a855f7;
}
.select-highlight {
  position: absolute;
  top: 50%;
  transform: translateY(-50%);
  left: 0;
  right: 0;
  height: 52px;
  border-top: 1px solid rgba(192, 132, 252, 0.4);
  border-bottom: 1px solid rgba(192, 132, 252, 0.4);
  background: rgba(192, 132, 252, 0.05);
  pointer-events: none;
  box-shadow: 0 0 12px rgba(192, 132, 252, 0.08) inset;
}
.reel-result {
  text-align: center;
  font-size: 16px;
  font-weight: bold;
  color: #c084fc;
  text-shadow: 0 0 12px rgba(192, 132, 252, 0.5);
  font-family: 'Courier New', monospace;
  letter-spacing: 1px;
  margin: 6px 0 2px;
  padding: 8px 0;
  border-radius: 8px;
  background: rgba(168, 130, 255, 0.06);
  border: 1px solid rgba(192, 132, 252, 0.2);
}
.history-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.hist-info {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px;
  background: rgba(168, 85, 247, 0.08);
  border: 1px solid rgba(168, 85, 247, 0.2);
  border-radius: 8px;
}
.hist-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  font-size: 13px;
  color: #c4b5fd;
}
.hist-row span { color: #a855f7; flex-shrink: 0; }
.hist-row b { color: #fff; font-family: monospace; }
.hist-row b.hist-ts {
  color: #d8b4fe;
  font-weight: bold;
  text-shadow: 0 0 8px rgba(168, 85, 247, 0.6);
}
.hist-status {
  font-size: 12px;
  color: #2ED573;
  text-align: center;
}
.hist-status.error { color: #FF4757; }
.hist-status.warn { color: #facc15; }
.cyber-btn:disabled { opacity: 0.5; cursor: not-allowed; }
/* ===== OPENCODE-EDIT-20 CAV 面板内容上移（不再 space-between 贴底） ===== */
.cav-right-panel {
  justify-content: flex-start;
  gap: 16px;
}
/* ===== OPENCODE-EDIT-20 END ===== */
/* ===== OPENCODE-EDIT-19 CAV 编队演示数据表 ===== */
.cav-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
  color: #c4b5fd;
}
.cav-table th {
  text-align: left;
  padding: 6px 4px;
  border-bottom: 1px solid rgba(168, 85, 247, 0.3);
  color: #a855f7;
  font-size: 11px;
  font-weight: bold;
}
.cav-table td {
  padding: 7px 4px;
  border-bottom: 1px solid rgba(168, 85, 247, 0.12);
  font-family: monospace;
}
.cav-table tr:last-child td { border-bottom: none; }
.cav-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: 5px;
  box-shadow: 0 0 6px currentColor;
}
/* ===== OPENCODE-EDIT-19 END ===== */
/* ===== OPENCODE-EDIT-16 END ===== */
</style>