<template>
  <div class="admin-layout">
    <!-- 左侧导航：3D 立体书本透视容器 -->
    <div class="sidebar-wrapper">
      <aside class="sidebar-book">
        <div class="book-spine"></div>
        <div class="logo glow-text">园区调控后台</div>
        
        <ul class="nav-menu">
          <li 
            :class="{ active: currentTab === 'data' }" 
            @click="currentTab = 'data'"
          >
            <span class="tab-text">📊 数据管理</span>
          </li>
          <li 
            :class="{ active: currentTab === 'config' }" 
            @click="currentTab = 'config'"
          >
            <span class="tab-text">⚙️ 系统配置</span>
          </li>
        </ul>
      </aside>
    </div>

    <!-- 右侧内容区：紫黑科幻风 -->
    <main class="main-content">
      <header class="top-header">
        <h2 class="glow-title">{{ currentTab === 'data' ? '数据管理面板' : '系统配置面板' }}</h2>
        <div class="user-info">
          <span>欢迎，{{ userStore.username || '管理员' }}</span>
          <button @click="logout" class="logout-btn">退 出</button>
        </div>
      </header>

      <!-- 模块：数据管理 -->
      <section v-if="currentTab === 'data'" class="content-panel">
        <div class="card-grid">
          <div class="card glass-card">
            <h3>异常预警列表</h3>
            <p class="placeholder-text">暂无数据。后续将对接 /api/v1/security/alerts 接口。</p>
          </div>
          <div class="card glass-card">
            <h3>历史通行记录</h3>
            <p class="placeholder-text">表格区域骨架...</p>
          </div>
        </div>
      </section>

      <!-- 模块：系统配置 -->
      <section v-if="currentTab === 'config'" class="content-panel">
        <div class="card glass-card config-card">
          <h3>门闸策略配置</h3>
          <div class="form-row">
            <label>默认放行上限 (人/分钟):</label>
            <input type="number" value="120" class="cyber-input" />
          </div>
          <div class="form-row">
            <label>差分隐私加噪强度 (ε):</label>
            <input type="number" value="1.0" step="0.1" class="cyber-input" />
          </div>
          <button class="save-btn">保存配置</button>
        </div>
      </section>
    </main>

    <!-- ★ 屏幕左下角：与大屏严格对齐的高科技 HUD 切换按钮 (不倾斜) -->
    <div class="hud-dashboard-btn" @click="goToDashboard" title="切换到可视化大屏">
      <div class="hud-ring"></div>
      <div class="hud-icon">
        <span class="hud-text">大屏<br>VISUAL</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useUserStore } from '../stores/user'

const router = useRouter()
const userStore = useUserStore()

const currentTab = ref('data')

const goToDashboard = () => {
  router.push('/dashboard')
}

const logout = () => {
  userStore.clearLoginState()
  router.push('/login')
}
</script>

<style scoped>
/* 1. 整体布局深紫底色 */
.admin-layout {
  display: flex;
  height: 100vh;
  background-color: #0b0714;
  overflow: hidden;
  position: relative; /* 为绝对定位按钮做参照 */
}

/* =========================================
   2. 侧边栏：3D 立体书本特效 
   ========================================= */
.sidebar-wrapper {
  perspective: 1200px;
  width: 260px;
  z-index: 10;
}

.sidebar-book {
  width: 100%;
  height: 100%;
  transform-origin: left center;
  transform: rotateY(22deg); /* 书本倾斜 */
  background: linear-gradient(90deg, #0d071a 0%, #1e103c 80%, #2b1654 100%);
  box-shadow: 
    8px 0 0 #180d2b, 
    12px 0 0 #3d246c, 
    25px 0 30px rgba(0, 0, 0, 0.9);
  display: flex;
  flex-direction: column;
  position: relative;
  transition: transform 0.5s ease;
}

.sidebar-book:hover {
  transform: rotateY(15deg);
}

.book-spine {
  position: absolute;
  left: 10px;
  top: 0;
  bottom: 0;
  width: 4px;
  background: rgba(0, 0, 0, 0.3);
  box-shadow: 2px 0 5px rgba(255,255,255,0.05);
}

.logo {
  height: 80px;
  line-height: 80px;
  text-align: center;
  font-size: 20px;
  font-weight: 900;
  border-bottom: 1px solid rgba(168, 85, 247, 0.2);
  letter-spacing: 2px;
  margin-bottom: 20px;
}

.glow-text {
  color: #fff;
  text-shadow: 0 0 10px rgba(168, 85, 247, 0.8);
}

/* =========================================
   3. 菜单项：荧光笔高亮 & 索引贴抽出动效
   ========================================= */
.nav-menu {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
}

.nav-menu li {
  position: relative;
  margin: 15px 20px;
  padding: 12px 15px;
  cursor: pointer;
  transition: all 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275);
  background: linear-gradient(90deg, transparent 0%, rgba(168, 85, 247, 0.15) 20%, transparent 100%);
  border-radius: 4px;
  color: rgba(255, 255, 255, 0.6);
  font-weight: bold;
}

.nav-menu li:hover, .nav-menu li.active {
  color: #fff;
  background: linear-gradient(90deg, #7c3aed, #a855f7);
  transform: translateX(40px) scale(1.05);
  border-radius: 4px 12px 12px 4px;
  border-left: 5px solid #fff;
  box-shadow: 5px 5px 15px rgba(124, 58, 237, 0.6);
}

.tab-text {
  position: relative;
  z-index: 2;
  letter-spacing: 1px;
}

/* =========================================
   ★ 4. 与大屏对齐的固定坐标 HUD 按钮 (正向无倾斜)
   ========================================= */
.hud-dashboard-btn {
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

.hud-dashboard-btn:hover {
  transform: scale(1.15); /* 鼠标悬浮大一圈 */
}

/* 内部发光电光蓝核心 */
.hud-icon {
  width: 55px;
  height: 55px;
  background: rgba(56, 189, 248, 0.25);
  border: 2px solid #38bdf8;
  border-radius: 50%;
  display: flex;
  justify-content: center;
  align-items: center;
  text-align: center;
  box-shadow: 0 0 15px rgba(56, 189, 248, 0.6), inset 0 0 10px rgba(56, 189, 248, 0.4);
  z-index: 2;
  backdrop-filter: blur(4px);
}

.hud-text {
  color: #fff;
  font-size: 11px;
  font-weight: bold;
  letter-spacing: 1px;
  line-height: 1.2;
  text-shadow: 0 0 5px #38bdf8;
}

/* 外部自转虚线光环 */
.hud-ring {
  position: absolute;
  width: 100%;
  height: 100%;
  border-radius: 50%;
  border: 2px dashed rgba(56, 189, 248, 0.3);
  border-top-color: #38bdf8;
  border-bottom-color: #0ea5e9;
  animation: hudSpin 6s linear infinite;
  z-index: 1;
}

@keyframes hudSpin {
  100% { transform: rotate(360deg); }
}

/* =========================================
   5. 右侧主体内容 (紫黑玻璃质感)
   ========================================= */
.main-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  background-image: 
    radial-gradient(circle at top right, rgba(147, 51, 234, 0.1), transparent 40%),
    radial-gradient(circle at bottom left, rgba(56, 189, 248, 0.05), transparent 40%);
}

.top-header {
  height: 70px;
  background: rgba(19, 11, 33, 0.8);
  backdrop-filter: blur(10px);
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0 40px;
  border-bottom: 1px solid rgba(168, 85, 247, 0.2);
}

.glow-title {
  font-size: 20px;
  color: #fff;
  margin: 0;
  text-shadow: 0 0 8px rgba(168, 85, 247, 0.5);
}

.user-info {
  display: flex;
  align-items: center;
  gap: 20px;
  color: #c4b5fd;
  font-size: 14px;
}

.logout-btn {
  padding: 8px 18px;
  border: 1px solid rgba(168, 85, 247, 0.5);
  background: transparent;
  color: #c4b5fd;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.3s;
}

.logout-btn:hover {
  background: rgba(255, 71, 87, 0.1);
  color: #ff4757;
  border-color: #ff4757;
  box-shadow: 0 0 10px rgba(255, 71, 87, 0.3);
}

.content-panel {
  padding: 30px 40px;
  flex: 1;
  overflow-y: auto;
}

.card-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 30px;
}

.glass-card {
  background: rgba(20, 12, 38, 0.6);
  backdrop-filter: blur(12px);
  border: 1px solid rgba(168, 85, 247, 0.2);
  border-radius: 12px;
  padding: 25px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
}

.glass-card h3 {
  margin-top: 0;
  border-bottom: 1px solid rgba(168, 85, 247, 0.2);
  padding-bottom: 15px;
  margin-bottom: 20px;
  color: #e9d5ff;
  font-weight: normal;
  letter-spacing: 1px;
}

.placeholder-text {
  color: #6b7280;
  font-style: italic;
}

.config-card {
  max-width: 600px;
}

.form-row {
  margin-bottom: 20px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.form-row label {
  color: #c4b5fd;
  font-size: 14px;
}

.cyber-input {
  padding: 12px;
  background: rgba(0, 0, 0, 0.3);
  border: 1px solid rgba(168, 85, 247, 0.3);
  border-radius: 6px;
  width: 250px;
  color: #fff;
  font-size: 15px;
  transition: all 0.3s;
  outline: none;
}

.cyber-input:focus {
  border-color: #a855f7;
  box-shadow: 0 0 10px rgba(168, 85, 247, 0.4);
}

.save-btn {
  margin-top: 10px;
  padding: 12px 30px;
  background: linear-gradient(90deg, #7c3aed, #9333ea);
  color: #fff;
  border: none;
  border-radius: 6px;
  cursor: pointer;
  font-size: 15px;
  font-weight: bold;
  letter-spacing: 2px;
  transition: all 0.3s;
}

.save-btn:hover {
  transform: translateY(-2px);
  box-shadow: 0 5px 15px rgba(147, 51, 234, 0.5);
}
</style>