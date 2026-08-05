<template>
  <div class="dashboard-layout">
    <!-- 顶部导航 -->
    <header class="top-header">
      <h1 class="glow-text">园区安全智能调控大屏</h1>
      <div class="mode-switch">
        <button :class="{ active: currentMode === 'heatmap' }" @click="switchMode('heatmap')">
          🔥 实时热力与拥堵
        </button>
        <button :class="{ active: currentMode === 'topology' }" @click="switchMode('topology')">
          🕸️ 交通枢纽分析 (PageRank)
        </button>
      </div>
    </header>

    <div class="main-body">
      <!-- 左侧预警面板 -->
      <aside class="left-panel">
        <div class="panel-box">
          <h3 class="panel-title">⚠️ 实时安全异常预警</h3>
          <ul class="alert-list">
            <li v-for="(alert, index) in alertList" :key="index" class="alert-item">
              <span class="alert-content">{{ alert.message || alert }}</span>
            </li>
            <li v-if="alertList.length === 0" class="empty-text">✅ 当前园区畅通，无异常事件</li>
          </ul>
        </div>
      </aside>

      <!-- 中间纯拓扑渲染 (无图底座) -->
      <main class="center-panel">
        <div ref="chartRef" class="chart-container"></div>
      </main>
    </div>

    <!-- 🌟 左下角：高科技 HUD 返回后台按钮 -->
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
        <p v-if="currentMode === 'topology'"><strong>PageRank 枢纽度：</strong>{{ currentNode?.pagerankScore || 0 }}</p>
        
        <h4 style="margin-top: 20px; color: #a855f7;">📈 MindSpore 未来 30 分钟拥堵预测</h4>
        <div ref="predictChartRef" class="predict-chart"></div>
        
        <button class="close-btn" @click="closeModal">关闭面板</button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, nextTick } from 'vue'
import { useRouter } from 'vue-router' // ★ 引入路由用于跳转
import * as echarts from 'echarts'
import topologyData from '../mock/topology.json' 
import { getNodesDataAPI, getAlertsAPI, getPredictionDataAPI } from '../api/index'

const router = useRouter() // ★ 实例化路由
const currentMode = ref<'heatmap' | 'topology'>('heatmap')
const alertList = ref<any[]>([])

const chartRef = ref<HTMLElement | null>(null)
let mainChart: echarts.ECharts | null = null

const showModal = ref(false)
const currentNode = ref<any>(null)
const predictChartRef = ref<HTMLElement | null>(null)
let predictChart: echarts.ECharts | null = null

let nodeDynamicDataMap: Record<string, any> = {}

// ★ 切回后台的函数
const goToAdmin = () => {
  router.push('/admin')
}

onMounted(async () => {
  mainChart = echarts.init(chartRef.value!)
  
  try {
    const alertsRes: any = await getAlertsAPI()
    if (alertsRes.code === 200 || alertsRes.code === 0) {
      alertList.value = alertsRes.data || []
    }
  } catch (e) {
    console.error("预警接口请求失败", e)
  }

  await fetchDynamicData()
  renderMainChart()

  mainChart.on('click', (params: any) => {
    if (params.dataType === 'node') {
      currentNode.value = params.data
      showModal.value = true
      nextTick().then(() => {
        renderPredictChart(currentNode.value.id) 
      })
    }
  })

  window.addEventListener('resize', handleResize)
})

onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
  mainChart?.dispose()
  predictChart?.dispose()
})

const handleResize = () => {
  mainChart?.resize()
  predictChart?.resize()
}

const fetchDynamicData = async () => {
  try {
    const nodeRes: any = await getNodesDataAPI()
    if ((nodeRes.code === 200 || nodeRes.code === 0) && nodeRes.data) {
      const nodeArray = nodeRes.data.nodes || nodeRes.data
      nodeArray.forEach((item: any) => { 
        nodeDynamicDataMap[item.nodeId || item.nodeName] = item 
      })
    }
  } catch (err) {
    console.error('动态数据拉取失败', err)
  }
}

const renderMainChart = () => {
  if (!mainChart) return

  const nodeDensityMap: Record<string, number> = {}
  const nodeSizeMap: Record<string, number> = {}

  const graphNodes = topologyData.nodes.map((n: any) => {
    const dynamicData = nodeDynamicDataMap[n.name] || nodeDynamicDataMap[n.id] || {}
    
    const density = parseFloat(dynamicData.density) || 0.1
    const pagerank = parseFloat(dynamicData.pagerankScore || dynamicData.pagerank) || 0.1
    
    nodeDensityMap[n.id] = density
    nodeDensityMap[n.name] = density

    const baseColor = (topologyData.node_colors as any)[n.type] || '#c4b5fd'

    let symbolSize = 30 
    let itemColor = baseColor

    if (currentMode.value === 'heatmap') {
      symbolSize = 34 
      // 保持热力图拥堵特征色不变
      if (density >= 0.8) itemColor = '#FF4757' 
      else if (density >= 0.5) itemColor = '#FFA502' 
      else itemColor = '#2ED573' 
    } else {
      symbolSize = 20 + pagerank * 80 
      itemColor = baseColor 
    }

    nodeSizeMap[n.id] = symbolSize
    nodeSizeMap[n.name] = symbolSize

    return {
      id: n.id,
      name: n.name,
      type: n.type,
      value: [n.x, n.y],
      density: density, 
      pagerankScore: pagerank,
      symbol: 'circle', 
      symbolSize,
      itemStyle: { 
        color: itemColor, 
        shadowBlur: currentMode.value === 'heatmap' ? 18 : 10, 
        shadowColor: itemColor 
      }
    }
  })

  const graphEdges = topologyData.edges.map((e: any) => {
    const sourceDensity = nodeDensityMap[e.source] || 0.1
    const targetDensity = nodeDensityMap[e.target] || 0.1
    const roadDensity = Math.max(sourceDensity, targetDensity)

    const sourceSize = nodeSizeMap[e.source] || 30
    const targetSize = nodeSizeMap[e.target] || 30
    const roadRadiusWidth = Math.max(sourceSize, targetSize) / 2

    let roadColor = '#a855f7' // 拓扑模式下使用霓虹紫替代原先的蓝

    if (currentMode.value === 'heatmap') {
      if (roadDensity >= 0.8) roadColor = '#FF4757'      
      else if (roadDensity >= 0.5) roadColor = '#FFA502' 
      else roadColor = '#2ED573'                         
    }

    return {
      source: e.source,
      target: e.target,
      lineStyle: { 
        width: roadRadiusWidth, 
        opacity: currentMode.value === 'heatmap' ? 0.7 : 0.4, 
        color: roadColor,
        curveness: 0.05 
      }
    }
  })

  const option = {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'item' },
    grid: { top: '3%', bottom: '3%', left: '3%', right: '3%' },
    xAxis: { type: 'value', min: 0, max: 100, show: false },
    yAxis: { type: 'value', min: 0, max: 100, show: false },
    dataZoom: [
      {
        type: 'inside',
        xAxisIndex: 0,
        yAxisIndex: 0,
        filterMode: 'empty' 
      }
    ],
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

const switchMode = (mode: 'heatmap' | 'topology') => {
  currentMode.value = mode
  renderMainChart()
}

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
          lineStyle: { color: '#FF4757', type: 'dashed' }
        }
      }]
    }
    predictChart.setOption(option)
  }
}

const closeModal = () => {
  showModal.value = false
  predictChart?.dispose()
}
</script>

<style scoped>
/* 1. 整体布局底色调为深紫虚空背景 */
.dashboard-layout {
  height: 100vh;
  background-color: #0b0714; /* 极深紫调 */
  position: relative; /* 为绝对定位按钮做参照 */
  display: flex;
  flex-direction: column;
}

/* 2. 顶部导航紫调化 */
.top-header {
  height: 70px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0 30px;
  background-color: #130b21; /* 暗光紫 */
  border-bottom: 1px solid #2d1b4e;
}
.glow-text {
  color: #fff;
  font-size: 22px;
  margin: 0;
  text-shadow: 0 0 10px rgba(168, 85, 247, 0.8); /* 紫色霓虹发光字 */
}
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
/* 顶部激活按键变成夺目的赛博紫色 */
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
}

/* 3. 左侧预警面板紫调化 */
.left-panel {
  width: 320px;
  background-color: #130b21;
  border-right: 1px solid #2d1b4e;
  padding: 20px;
  overflow-y: auto;
}
.panel-title { 
  color: #e9d5ff; 
  border-bottom: 1px solid #3b2563; 
  padding-bottom: 12px; 
  margin-top: 0;
}
.alert-list {
  padding: 0; margin: 0; list-style: none;
}
.alert-item {
  color: #ff7675;
  background: rgba(255, 118, 117, 0.15);
  padding: 12px;
  margin-bottom: 12px;
  border-radius: 6px;
  font-size: 14px;
  border-left: 4px solid #ff7675;
}
.empty-text { color: #2ED573; font-size: 14px; margin-top: 10px;}

.center-panel { 
  flex: 1; 
  padding: 20px; 
  display: flex;
  justify-content: center;
  align-items: center;
}

/* 4. 网格线融入微弱的暗紫色 */
.chart-container {
  width: 100%;
  height: 100%;
  border-radius: 8px;
  background-color: transparent;
  background-image: 
    linear-gradient(rgba(147, 51, 234, 0.15) 1px, transparent 1px),
    linear-gradient(90deg, rgba(147, 51, 234, 0.15) 1px, transparent 1px);
  background-size: 40px 40px; 
}

/* ★ 5. 高科技 HUD 切换后台按钮（精髓交互） */
.hud-back-btn {
  position: absolute;
  bottom: 30px;
  left: 30px;
  width: 100px;
  height: 100px;
  cursor: pointer;
  z-index: 100;
  display: flex;
  justify-content: center;
  align-items: center;
  /* 平滑的弹簧回弹放大特效 */
  transition: transform 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275);
}
.hud-back-btn:hover {
  transform: scale(1.15); /* 悬浮大一圈 */
}
/* 内部紫色发光核心 */
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
/* 外部旋转扫描光环 */
.hud-ring {
  position: absolute;
  width: 100%;
  height: 100%;
  border-radius: 50%;
  border: 2px dashed rgba(196, 181, 253, 0.3);
  border-top-color: #d8b4fe; /* 亮侧 */
  border-bottom-color: #a855f7; /* 亮侧 */
  animation: hudSpin 5s linear infinite;
  z-index: 1;
}
@keyframes hudSpin {
  100% { transform: rotate(360deg); }
}

/* 弹窗样式调整为紫黑质感 */
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
.close-btn {
  width: 100%; margin-top: 20px; padding: 12px;
  background: linear-gradient(90deg, #7c3aed, #9333ea); 
  color: #fff; border: none; border-radius: 6px; cursor: pointer;
  font-size: 16px;
}
.close-btn:hover {
  background: linear-gradient(90deg, #8b5cf6, #a855f7);
}
</style>